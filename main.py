import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from handlers import router as main_router

TOKEN = os.getenv("BOT_TOKEN", "")


def main() -> None:
    if not TOKEN:
        raise ValueError("BOT_TOKEN не найден. Укажите переменную окружения BOT_TOKEN перед запуском бота.")

    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(main_router)
    print("Бот-конвертер запущен...")
    dp.run_polling(bot, handle_signals=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

