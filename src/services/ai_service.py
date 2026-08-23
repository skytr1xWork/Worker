import asyncio
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
                    "Accept": "text/plain"
                }
                async with session.get(jina_url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Jina Reader failed with status {resp.status}, falling back to simple parsing")
                        return await self._fetch_article_simple(url)

                    text = await resp.text()
                    text = text.strip()

                    # Проверяем что Jina вернула реальный контент, а не страницу ошибки/авторизации
                    if not text or "Internal error" in text or "Log in" in text[:500]:
                        logger.warning("Jina Reader returned error/auth page, falling back to simple parsing")
                        return await self._fetch_article_simple(url)

                    # Ограничиваем длину (чтобы не превысить лимиты API)
                    max_length = 15000
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

    async def _fetch_article_simple(self, url: str) -> str:
        """Резервный метод для извлечения текста из HTML."""
        import re

        async with aiohttp.ClientSession() as session:
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru,en;q=0.9",
            }
            # Следуем редиректам (habr.com/p/ -> habr.com/ru/p/)
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30), allow_redirects=True) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to fetch URL: HTTP {resp.status}")

                html_content = await resp.text()

                # Удаляем script и style
                html_content = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
                html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

                # Habr: вытаскиваем контент из article-formatted-body
                habr_match = re.search(
                    r'class="article-formatted-body[^"]*">(.*?)(?=<div class="tm-article-presenter__meta)',
                    html_content, re.DOTALL
                )
                if habr_match:
                    text = re.sub(r"<[^>]+>", " ", habr_match.group(1))
                else:
                    # Для остальных сайтов: ищем семантические теги
                    semantic_match = re.search(
                        r"<(?:article|main|section)[^>]*>(.*?)</(?:article|main|section)>",
                        html_content, re.DOTALL | re.IGNORECASE
                    )
                    if semantic_match:
                        text = re.sub(r"<[^>]+>", " ", semantic_match.group(1))
                    else:
                        # Грубая очистка всей страницы как последний резерв
                        text = re.sub(r"<[^>]+>", " ", html_content)

                text = re.sub(r"\s+", " ", text).strip()

                if not text or len(text) < 100:
                    raise Exception("No text content extracted from URL")

                max_length = 15000
                if len(text) > max_length:
                    text = text[:max_length] + "..."

                return text

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
                    self.api_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"OpenRouter API error: {resp.status} - {error_text}")
                        raise Exception(f"OpenRouter API error: {resp.status}")

                    data = await resp.json()
                    if "choices" not in data or not data["choices"]:
                        raise Exception("Invalid response from OpenRouter API")

                    content = data["choices"][0]["message"].get("content")
                    if not content or not content.strip():
                        raise Exception("Empty response from OpenRouter API")

                    return content.strip()

        except (asyncio.TimeoutError, TimeoutError):
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
