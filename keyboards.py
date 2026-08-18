from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from converter import SUPPORTED_IMAGE_FORMATS, normalize_format


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Returns the persistent main menu reply keyboard."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔄 Конвертер")
    builder.button(text="ℹ️ Помощь")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Returns the cancel reply keyboard when waiting for file upload."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)


def get_format_keyboard(source_format: str) -> InlineKeyboardMarkup:
    """
    Generates inline keyboard with all supported target formats except the source format.
    """
    builder = InlineKeyboardBuilder()
    source_fmt = normalize_format(source_format)

    # Order of display
    format_order = ["PNG", "JPG", "WEBP", "BMP", "TIFF", "ICO", "PDF", "GIF"]

    for fmt in format_order:
        if fmt != source_fmt:
            builder.button(text=f"➡️ {fmt}", callback_data=f"conv:{fmt}")

    builder.adjust(3, 3, 2)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="conv:cancel"))
    return builder.as_markup()


def get_done_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard shown after conversion."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Сконвертировать другой файл", callback_data="conv:new_file")
    return builder.as_markup()
