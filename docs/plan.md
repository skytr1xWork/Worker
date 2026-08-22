# 📋 План оптимизации производительности и архитектуры

**Дата:** 2026-08-22  
**Версия:** 1.0  
**Статус:** На утверждении

---

## 🎯 Цели оптимизации

1. **Снизить потребление памяти** на 20-30%
2. **Ускорить обработку запросов** на 15-20%
3. **Улучшить масштабируемость** для большего числа пользователей
4. **Сохранить функциональность** - ничего не сломать

---

## 🏗️ Архитектурные решения

### 1. Ленивая загрузка тяжелых модулей

#### Текущая проблема
```python
# tiktok_shazam.py - строка 19
import numpy as np

# Строка 24
HANNING_MATRIX = np.hanning(2050)[1:-1]
```

**Проблема:**
- Numpy (~100 МБ) загружается при любом импорте модуля
- HANNING_MATRIX (16 КБ) занимает память всегда
- Даже если пользователь не использует Shazam

#### Архитектурное решение

**Подход 1: Lazy import с функцией-фабрикой**
```python
# tiktok_shazam.py

# Глобальные кэши
_numpy_module = None
_hanning_matrix = None

def _get_numpy():
    """Ленивая загрузка numpy."""
    global _numpy_module
    if _numpy_module is None:
        import numpy as np
        _numpy_module = np
    return _numpy_module

def _get_hanning_matrix():
    """Ленивая загрузка HANNING_MATRIX."""
    global _hanning_matrix
    if _hanning_matrix is None:
        np = _get_numpy()
        _hanning_matrix = np.hanning(2050)[1:-1]
    return _hanning_matrix

class SignatureGenerator:
    def _do_fft(self, batch_128):
        np = _get_numpy()
        HANNING = _get_hanning_matrix()
        # используем np и HANNING как раньше
```

**Преимущества:**
- ✅ Numpy загружается только при первом использовании Shazam
- ✅ Экономия ~100 МБ памяти для пользователей, не использующих Shazam
- ✅ Минимальные изменения в коде
- ✅ Обратная совместимость

**Недостатки:**
- ⚠️ Первый запрос Shazam будет на ~200ms медленнее (однократно)

**Альтернатива (не рекомендуется):**
- Вынести Shazam в отдельный микросервис - избыточно для текущего масштаба

---

### 2. Оптимизация RingBuffer с numpy массивами

#### Текущая проблема
```python
# tiktok_shazam.py - строки 133-134
self.fft_outputs = RingBuffer(buffer_size=256, default_value=np.zeros(1025))
self.spread_fft_output = RingBuffer(buffer_size=256, default_value=np.zeros(1025))
```

**Проблема:**
- Каждый буфер: 256 × 1025 × 8 bytes = **2 МБ**
- Два буфера = **4 МБ на один запрос Shazam**
- При 2 параллельных запросах = 8 МБ

#### Архитектурное решение

**Подход 1: Использовать array.array (рекомендуется для совместимости)**
```python
from array import array

class RingBuffer(list):
    def __init__(self, buffer_size: int, default_value=0):
        # Проверяем тип default_value
        if hasattr(default_value, '__iter__') and not isinstance(default_value, str):
            # Если это numpy массив или список - создаем array
            super().__init__([array('d', default_value) for _ in range(buffer_size)])
        else:
            # Для скаляров - как раньше
            super().__init__([default_value] * buffer_size)
        self.position: int = 0
        self.buffer_size: int = buffer_size
        self.num_written: int = 0
```

**Подход 2: Lazy allocation (более агрессивная оптимизация)**
```python
class LazyRingBuffer:
    def __init__(self, buffer_size: int, default_factory=None):
        self.buffer_size = buffer_size
        self.buffer = [None] * buffer_size
        self.default_factory = default_factory or (lambda: np.zeros(1025))
        self.position = 0
        self.num_written = 0
    
    def __getitem__(self, idx):
        if self.buffer[idx] is None:
            self.buffer[idx] = self.default_factory()
        return self.buffer[idx]
```

**Рекомендация:** Подход 1 (array.array)

**Преимущества:**
- ✅ Экономия ~50% памяти (array.array компактнее numpy)
- ✅ Совместимость с существующим кодом
- ✅ Не требует изменений в SignatureGenerator

**Недостатки:**
- ⚠️ array.array немного медленнее numpy для математических операций
- ⚠️ Нужна конвертация array ↔ numpy в некоторых местах

**Альтернатива:**
- Использовать numpy.memmap для хранения на диске - избыточно сложно

---

### 3. Кэширование Pinterest metadata

#### Текущая проблема
```python
# url_converter.py
def get_url_metadata(url: str):
    if "pinterest" in url:
        p_media = _get_pinterest_media(url)  # HTTP запрос 1
        
def download_url_to_file(url: str, ...):
    if is_pin:
        p_media = _get_pinterest_media(url)  # HTTP запрос 2 (дублирование!)
```

**Проблема:**
- Один URL → 2 одинаковых HTTP запроса
- +500-800ms латентность
- Лишняя нагрузка на Pinterest сервера

#### Архитектурное решение

**Подход 1: LRU кэш с TTL (рекомендуется)**
```python
import functools
import time

def ttl_lru_cache(ttl_seconds=300, maxsize=128):
    """LRU кэш с TTL."""
    def decorator(func):
        cache = {}
        cache_times = {}
        
        @functools.wraps(func)
        def wrapper(url: str):
            current_time = time.time()
            
            # Проверяем кэш
            if url in cache:
                if current_time - cache_times[url] < ttl_seconds:
                    return cache[url]
                else:
                    # Устаревшая запись
                    del cache[url]
                    del cache_times[url]
            
            # Вызываем функцию
            result = func(url)
            
            # Сохраняем в кэш
            cache[url] = result
            cache_times[url] = current_time
            
            # Ограничение размера
            if len(cache) > maxsize:
                oldest = min(cache_times.items(), key=lambda x: x[1])[0]
                del cache[oldest]
                del cache_times[oldest]
            
            return result
        
        return wrapper
    return decorator

@ttl_lru_cache(ttl_seconds=300, maxsize=128)
def _get_pinterest_media(url: str) -> dict:
    # существующий код
```

**Подход 2: Redis кэш (для продакшна с несколькими воркерами)**
```python
# Только если будет scaling на несколько серверов
import redis
r = redis.Redis()

def _get_pinterest_media(url: str) -> dict:
    cache_key = f"pinterest:{url}"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached)
    
    result = # HTTP запрос
    r.setex(cache_key, 300, json.dumps(result))
    return result
```

**Рекомендация:** Подход 1 (in-memory TTL кэш)

**Преимущества:**
- ✅ Устраняет дублирование HTTP запросов
- ✅ Ускорение на 500-800ms при повторных запросах
- ✅ TTL предотвращает устаревание данных
- ✅ Автоматическая очистка старых записей

**Недостатки:**
- ⚠️ Кэш в памяти одного процесса (не между воркерами)
- ⚠️ TTL 5 минут может быть избыточным для некоторых случаев

**Вопросы для обсуждения:**
- Какой TTL оптимален? (сейчас 300 сек = 5 мин)
- Нужен ли кэш для других сервисов (YouTube, TikTok)?

---

### 4. Оптимизация broadcast рассылки

#### Текущая проблема
```python
# handlers.py - строки 1351-1367
for idx, uid in enumerate(user_ids, 1):
    try:
        await bot.copy_message(...)  # Последовательно
        success_count += 1
    except Exception:
        failed_count += 1
    
    await asyncio.sleep(0.04)  # 40ms между сообщениями
```

**Проблема:**
- 1000 пользователей = 40+ секунд
- Линейная обработка блокирует другие операции
- Rate limit 25 msg/sec соблюдается, но неэффективно

#### Архитектурное решение

**Подход 1: Batch processing с asyncio.gather (рекомендуется)**
```python
async def send_message_batch(bot: Bot, message: Message, user_ids: list[int], batch_size: int = 25):
    """Отправка сообщений батчами с обработкой ошибок."""
    success_count = 0
    failed_count = 0
    blocked_count = 0
    
    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i + batch_size]
        
        # Создаем задачи для батча
        tasks = [
            bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            for uid in batch
        ]
        
        # Выполняем параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обрабатываем результаты
        for result in results:
            if isinstance(result, Exception):
                failed_count += 1
                err_str = str(result).lower()
                if any(k in err_str for k in ["forbidden", "blocked", "deactivated"]):
                    blocked_count += 1
            else:
                success_count += 1
        
        # Rate limit: пауза между батчами (25 msg/sec → 1 sec на батч)
        if i + batch_size < len(user_ids):
            await asyncio.sleep(1.0)
    
    return success_count, failed_count, blocked_count

# Использование в обработчике
@router.message(BroadcastState.waiting_for_message)
async def handle_broadcast_message_input(message: Message, state: FSMContext, bot: Bot):
    # ... получение user_ids
    
    success, failed, blocked = await send_message_batch(bot, message, user_ids, batch_size=25)
    
    await message.answer(
        f"📢 **Рассылка завершена!**\n\n"
        f"✅ Успешно: **{success}**\n"
        f"🚫 Заблокировали: **{blocked}**\n"
        f"❌ Ошибки: **{failed - blocked}**",
        parse_mode="Markdown"
    )
```

**Подход 2: Background task с очередью (для очень больших рассылок)**
```python
# Отдельная очередь для рассылок
broadcast_queue = asyncio.Queue()

async def broadcast_worker(bot: Bot):
    """Фоновый воркер для обработки рассылок."""
    while True:
        job = await broadcast_queue.get()
        try:
            await send_message_batch(bot, job['message'], job['user_ids'])
            # Уведомляем админа о завершении
        finally:
            broadcast_queue.task_done()

# Запуск при старте
async def on_startup():
    asyncio.create_task(broadcast_worker(bot))
```

**Рекомендация:** Подход 1 (batch processing)

**Преимущества:**
- ✅ Ускорение в ~25 раз (25 сообщений за 1 сек вместо 25 сек)
- ✅ 1000 пользователей = ~40 секунд → **2 секунды**
- ✅ Соблюдение rate limits (25 msg/sec)
- ✅ Не блокирует другие операции

**Недостатки:**
- ⚠️ При ошибках в середине батча труднее отследить конкретного пользователя
- ⚠️ Пиковая нагрузка выше (25 одновременных запросов)

**Вопросы для обсуждения:**
- Размер батча 25 оптимален? Можем увеличить до 30-50?
- Нужен ли progress bar с обновлением каждые N батчей?

---

### 5. Адаптивные таймауты subprocess

#### Текущая проблема
```python
# converter.py
subprocess.run(..., timeout=120)  # Всегда 2 минуты
subprocess.run(..., timeout=90)   # Всегда 1.5 минуты

# Маленький файл 1 МБ ждет 2 минуты при зависании
# Большой файл 20 МБ может не успеть за 2 минуты
```

**Проблема:**
- Фиксированные таймауты не учитывают размер файла
- Пользователь ждет слишком долго при ошибках
- Риск timeout для больших файлов

#### Архитектурное решение

**Подход: Динамические таймауты на основе размера**
```python
# converter.py

def calculate_timeout(operation: str, file_size_bytes: int) -> int:
    """
    Вычисляет оптимальный таймаут на основе операции и размера файла.
    
    Args:
        operation: тип операции ("video", "audio", "image")
        file_size_bytes: размер входного файла в байтах
    
    Returns:
        Таймаут в секундах
    """
    file_size_mb = file_size_bytes / (1024 * 1024)
    
    # Базовые таймауты
    base_timeouts = {
        "video": 30,      # Базовый таймаут для видео
        "audio": 20,      # Базовый таймаут для аудио
        "image": 10,      # Базовый таймаут для изображений
        "document": 15,   # Базовый таймаут для документов
    }
    
    # Коэффициенты времени обработки (секунды на МБ)
    processing_rates = {
        "video": 4,       # ~4 сек/МБ для видео конвертации
        "audio": 2,       # ~2 сек/МБ для аудио
        "image": 1,       # ~1 сек/МБ для изображений
        "document": 0.5,  # ~0.5 сек/МБ для документов
    }
    
    base = base_timeouts.get(operation, 30)
    rate = processing_rates.get(operation, 2)
    
    # Формула: base + (size_mb * rate) + 10% запас
    calculated = base + (file_size_mb * rate)
    timeout = int(calculated * 1.1)
    
    # Ограничения: минимум 10 сек, максимум 180 сек (3 мин)
    return max(10, min(timeout, 180))

# Использование в convert_video
def convert_video(input_bytes: bytes, source_format: str, target_format: str, 
                  orig_filename: str | None = None) -> tuple[bytes, str]:
    
    file_size = len(input_bytes)
    timeout = calculate_timeout("video", file_size)
    
    # ... создание команды ffmpeg
    
    res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
```

**Примеры расчета:**
| Размер | Операция | Таймаут |
|--------|----------|---------|
| 1 МБ   | video    | 34 сек  |
| 5 МБ   | video    | 52 сек  |
| 20 МБ  | video    | 118 сек |
| 1 МБ   | audio    | 24 сек  |
| 20 МБ  | audio    | 66 сек  |

**Преимущества:**
- ✅ Пользователь не ждет лишнее время при ошибках
- ✅ Достаточный запас для больших файлов
- ✅ Автоматическая адаптация под размер
- ✅ Защита от бесконечного ожидания (max 3 мин)

**Недостатки:**
- ⚠️ Сложнее отладка (разные таймауты для разных файлов)
- ⚠️ Нужна калибровка коэффициентов на реальном железе

**Вопросы для обсуждения:**
- Коэффициенты оптимальны? (4 сек/МБ для видео, 2 для аудио)
- Максимальный таймаут 180 сек достаточен?

---

### 6. Проверка размера изображений перед конвертацией

#### Текущая проблема
```python
# converter.py - convert_image
with Image.open(io.BytesIO(input_bytes)) as img:
    # PIL декодирует всё изображение в память
    # 20 МБ JPEG → 60+ МБ в памяти после декодирования
```

**Проблема:**
- Изображение 20 МБ (JPEG сжат) → 60-80 МБ в памяти (RGB несжатый)
- Риск OOM при больших изображениях или параллельных запросах

#### Архитектурное решение

**Подход: Предварительная проверка и downsample для больших изображений**
```python
def convert_image(input_bytes: bytes, target_format: str, max_dimension: int = 8192) -> tuple[bytes, str]:
    """
    Конвертирует изображение с защитой от OOM.
    
    Args:
        input_bytes: входное изображение
        target_format: целевой формат
        max_dimension: максимальное измерение (ширина/высота)
    """
    target = normalize_format(target_format)
    if target not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"Неподдерживаемый целевой формат: {target_format}")
    
    # Шаг 1: Проверка размеров БЕЗ полной загрузки
    with Image.open(io.BytesIO(input_bytes)) as img:
        width, height = img.size
        
        # Проверка на слишком большое разрешение
        if width > max_dimension or height > max_dimension:
            # Вычисляем коэффициент уменьшения
            scale = max_dimension / max(width, height)
            new_width = int(width * scale)
            new_height = int(height * scale)
            
            logger.warning(
                f"Изображение {width}x{height} превышает лимит {max_dimension}px. "
                f"Уменьшается до {new_width}x{new_height}"
            )
            
            # Используем thumbnail для эффективного уменьшения
            img.thumbnail((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Шаг 2: Оценка памяти после декодирования
        estimated_memory_mb = (width * height * 4) / (1024 * 1024)  # 4 bytes per pixel (RGBA)
        
        if estimated_memory_mb > 100:  # Больше 100 МБ
            raise ValueError(
                f"Изображение слишком большое для безопасной обработки "
                f"({width}x{height}, ~{estimated_memory_mb:.0f} МБ в памяти). "
                f"Максимум: {max_dimension}x{max_dimension} пикселей."
            )
        
        # Шаг 3: Конвертация (существующий код)
        output_io = io.BytesIO()
        ext = SUPPORTED_IMAGE_FORMATS[target]["ext"]
        
        # ... остальной код конвертации
```

**Альтернатива: Streaming обработка (более сложно)**
```python
# Для очень больших изображений - обрабатывать тайлами
# Не рекомендуется для бота - избыточная сложность
```

**Преимущества:**
- ✅ Защита от OOM
- ✅ Автоматическое уменьшение огромных изображений
- ✅ Ранняя валидация перед полной загрузкой
- ✅ Пользователь получает понятное сообщение об ошибке

**Недостатки:**
- ⚠️ Изображения > 8192px будут уменьшены (потеря качества)
- ⚠️ Дополнительная проверка на каждое изображение

**Вопросы для обсуждения:**
- max_dimension = 8192px оптимален? (4K = 3840, 8K = 7680)
- Предупреждать пользователя или сразу уменьшать?

---

## 📊 Сводная таблица изменений

| # | Проблема | Решение | Приоритет | Сложность | Влияние |
|---|----------|---------|-----------|-----------|---------|
| 1 | Numpy загрузка при импорте | Ленивая загрузка | 🔴 Высокий | Низкая | -100 МБ памяти |
| 2 | RingBuffer numpy массивы | array.array | 🔴 Высокий | Средняя | -2 МБ на запрос |
| 3 | Дублирование Pinterest запросов | LRU кэш с TTL | 🔴 Высокий | Низкая | -500ms латентность |
| 4 | Линейная broadcast рассылка | Batch processing | 🟡 Средний | Средняя | 20x ускорение |
| 5 | Фиксированные таймауты | Динамические таймауты | 🟡 Средний | Низкая | Лучший UX |
| 6 | OOM на больших изображениях | Проверка размеров | 🟡 Средний | Низкая | Стабильность |

---

## 🔄 План внедрения

### Фаза 1: Критические оптимизации (1-2 дня)
1. ✅ Ленивая загрузка numpy (tiktok_shazam.py)
2. ✅ Кэширование Pinterest metadata (url_converter.py)
3. ✅ Проверка размеров изображений (converter.py)

**Риски:** Низкие, изменения локальные

### Фаза 2: Улучшения производительности (2-3 дня)
4. ✅ Оптимизация RingBuffer (tiktok_shazam.py)
5. ✅ Динамические таймауты (converter.py, url_converter.py)

**Риски:** Средние, требуют тестирования

### Фаза 3: Масштабирование (3-4 дня)
6. ✅ Batch broadcast (handlers.py)
7. ✅ Мониторинг памяти
8. ✅ Нагрузочное тестирование

**Риски:** Средние, изменяют архитектуру

---

## 🧪 Тестирование

### Юнит-тесты
```python
# tests/test_performance.py

def test_lazy_numpy_import():
    """Проверка, что numpy не загружается при импорте."""
    import sys
    initial_modules = set(sys.modules.keys())
    
    import tiktok_shazam
    
    after_import = set(sys.modules.keys())
    assert 'numpy' not in (after_import - initial_modules)

def test_pinterest_cache():
    """Проверка работы кэша."""
    from url_converter import _get_pinterest_media
    
    url = "https://pinterest.com/pin/123"
    result1 = _get_pinterest_media(url)
    result2 = _get_pinterest_media(url)  # Должен быть из кэша
    
    assert result1 == result2

def test_timeout_calculation():
    """Проверка расчета таймаутов."""
    from converter import calculate_timeout
    
    assert calculate_timeout("video", 1024 * 1024) == 34  # 1 МБ
    assert calculate_timeout("video", 20 * 1024 * 1024) == 118  # 20 МБ
```

### Нагрузочное тестирование
```python
# tests/load_test.py

async def test_concurrent_conversions():
    """Тест параллельной обработки 10 файлов."""
    tasks = [convert_image(...) for _ in range(10)]
    results = await asyncio.gather(*tasks)
    assert all(results)

async def test_broadcast_1000_users():
    """Тест рассылки 1000 пользователям."""
    start = time.time()
    await send_message_batch(bot, message, user_ids[:1000])
    duration = time.time() - start
    assert duration < 45  # Должно быть быстрее 45 секунд
```

---

## 📈 Ожидаемые результаты

### До оптимизации
- Память при старте: ~150 МБ
- Память при Shazam: ~154 МБ (+4 МБ)
- Pinterest запрос: ~1200ms (2 HTTP запроса)
- Broadcast 1000 юзеров: ~40 секунд

### После оптимизации
- Память при старте: ~50 МБ (**-67%**)
- Память при Shazam: ~152 МБ (+2 МБ) (**-50%**)
- Pinterest запрос: ~700ms (1 HTTP + кэш) (**-42%**)
- Broadcast 1000 юзеров: ~2 секунды (**20x быстрее**)

---

## ❓ Вопросы для обсуждения

### Критичные решения:
1. **TTL кэша Pinterest** - 300 секунд оптимально?
2. **Размер батча broadcast** - 25 или можно больше (30-50)?
3. **max_dimension изображений** - 8192px или меньше?
4. **Коэффициенты таймаутов** - требуют калибровки на вашем железе?

### Дополнительные идеи:
5. Нужен ли кэш для YouTube/TikTok metadata?
6. Добавить мониторинг памяти в продакшен?
7. Логировать статистику производительности?
8. Background task для broadcast или достаточно синхронно?

---

## 🚀 Следующие шаги

1. **Обсудить план** - получить фидбек и правки
2. **Утвердить приоритеты** - что делать в первую очередь
3. **Создать ветку** - `feature/performance-optimization`
4. **Реализовать Фазу 1** - критические оптимизации
5. **Тестирование** - юнит и нагрузочное
6. **Code review** - проверка перед мержем
7. **Деплой** - постепенный rollout
8. **Мониторинг** - отслеживание метрик

---

**Ожидаю ваших правок и подтверждения! 🎯**
