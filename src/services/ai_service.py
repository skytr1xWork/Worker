import csv
import os
from typing import Any, Dict

import aiohttp
import logging

logger = logging.getLogger(__name__)


class AIService:
    """Сервис для работы с OpenRouter API для суммаризации статей."""

    def __init__(self, settings_path: str = "config/ai_settings.csv"):
        self.settings = self._load_settings(settings_path)
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"

    def _load_settings(self, path: str) -> Dict[str, Any]:
        """Загрузка настроек из CSV файла."""
        settings = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    value = row["value"]

                    # Подстановка переменных окружения
                    if value.startswith("${") and value.endswith("}"):
                        env_var = value[2:-1]
                        value = os.getenv(env_var, "")
                        if not value:
                            logger.warning(f"Environment variable {env_var} not set")

                    # Конвертация типов
                    param = row["parameter"]
                    if param in ["temperature", "top_p", "frequency_penalty", "presence_penalty"]:
                        try:
                            value = float(value) if value else 0.0
                        except ValueError:
                            value = 0.0
                    elif param == "max_tokens":
                        try:
                            value = int(value) if value else 1000
                        except ValueError:
                            value = 1000

                    settings[param] = value
        except FileNotFoundError:
            logger.error(f"Settings file not found: {path}")
            raise
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            raise

        return settings

    async def fetch_article_content(self, url: str) -> str:
        """
        Получение текстового содержимого статьи по URL.
        Использует Jina AI Reader API для извлечения чистого текста из статей.
        """
        try:
            # Используем Jina AI Reader API для получения чистого текста
            jina_url = f"https://r.jina.ai/{url}"

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "text/plain",
                    "X-With-Generated-Alt": "true"
                }
                async with session.get(jina_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to fetch article via Jina Reader: HTTP {resp.status}")

                    text = await resp.text()
                    text = text.strip()

                    if not text:
                        raise Exception("No text content extracted from URL")

                    # Ограничиваем длину (чтобы не превысить лимиты API)
                    max_length = 15000  # примерно 3750 токенов
                    if len(text) > max_length:
                        text = text[:max_length] + "..."

                    return text

        except asyncio.TimeoutError:
            raise Exception("Timeout while fetching article")
        except aiohttp.ClientError as e:
            raise Exception(f"Network error: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching article content: {e}")
            raise

    async def summarize_article(self, article_text: str) -> str:
        """Создание краткой выжимки статьи через OpenRouter API."""
        if not self.settings.get("api_key"):
            raise ValueError("AI_API не настроен в переменных окружения")
        if not self.settings.get("model"):
            raise ValueError("AI_MODEL не настроен в переменных окружения")

        prompt = self.settings.get("prompt", "")
        full_prompt = f"{prompt}\n\n{article_text}"

        payload = {
            "model": self.settings["model"],
            "messages": [{"role": "user", "content": full_prompt}],
            "temperature": self.settings.get("temperature", 0.3),
            "max_tokens": self.settings.get("max_tokens", 1000),
            "top_p": self.settings.get("top_p", 0.9),
            "frequency_penalty": self.settings.get("frequency_penalty", 0.0),
            "presence_penalty": self.settings.get("presence_penalty", 0.0),
        }

        headers = {
            "Authorization": f"Bearer {self.settings['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"OpenRouter API error: {resp.status} - {error_text}")
                        raise Exception(f"OpenRouter API error: {resp.status}")

                    data = await resp.json()
                    if "choices" not in data or not data["choices"]:
                        raise Exception("Invalid response from OpenRouter API")

                    return data["choices"][0]["message"]["content"]

        except asyncio.TimeoutError:
            raise Exception("Timeout while waiting for AI response")
        except aiohttp.ClientError as e:
            raise Exception(f"Network error: {str(e)}")
        except Exception as e:
            logger.error(f"Error during summarization: {e}")
            raise

    async def summarize_from_url(self, url: str) -> str:
        """
        Полный процесс: получение статьи по URL и создание выжимки.
        """
        # Получаем содержимое статьи
        article_text = await self.fetch_article_content(url)

        # Создаём выжимку
        summary = await self.summarize_article(article_text)

        return summary
