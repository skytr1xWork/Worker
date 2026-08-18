from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from converter import SUPPORTED_IMAGE_FORMATS, normalize_format


def get_main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Конвертер")
    builder.button(text="Помощь")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Отмена")
    return builder.as_markup(resize_keyboard=True)


def get_format_keyboard(source_format: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    source_fmt = normalize_format(source_format)

    format_order = ["PNG", "JPG", "WEBP", "BMP", "TIFF", "ICO", "PDF", "GIF"]

    for fmt in format_order:
        if fmt != source_fmt:
            builder.button(text=f"{fmt}", callback_data=f"conv:{fmt}")

    builder.adjust(3, 3, 2)
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="conv:cancel"))
    return builder.as_markup()


def get_done_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сконвертировать другой файл", callback_data="conv:new_file")
    return builder.as_markup()
