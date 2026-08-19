from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from converter import (
    DOCUMENT_TARGETS,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_DOCUMENT_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    SUPPORTED_VIDEO_FORMATS,
    normalize_format,
)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Конвертер")
    builder.button(text="Конвертер (из ссылки)")
    builder.button(text="Помощь")
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="Отмена")
    return builder.as_markup(resize_keyboard=True)


def get_format_keyboard(source_format: str, category: str = "image") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    source_fmt = normalize_format(source_format)

    if category == "document":
        target_formats = DOCUMENT_TARGETS.get(
            source_fmt,
            [f for f in ["DOCX", "MD", "TXT", "HTML", "DAT", "CSV", "JSON"] if f != source_fmt],
        )
    elif category == "audio":
        audio_order = ["MP3", "WAV", "OGG", "OPUS", "FLAC", "AAC", "M4A", "WMA", "AIFF", "AMR", "AC3", "MP2"]
        target_formats = [fmt for fmt in audio_order if fmt != source_fmt]
    elif category == "video":
        video_order = ["MP4", "MOV", "WEBM", "AVI", "MKV", "GIF", "MP3", "WAV", "FLV", "WMV", "3GP", "TS", "MPEG", "OGV"]
        target_formats = [fmt for fmt in video_order if fmt != source_fmt]
    else:
        format_order = ["PNG", "JPG", "WEBP", "BMP", "TIFF", "ICO", "PDF", "GIF"]
        target_formats = [fmt for fmt in format_order if fmt != source_fmt]

    for fmt in target_formats:
        builder.button(text=f"{fmt}", callback_data=f"conv:{fmt}")

    builder.adjust(3, 3, 3)
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="conv:cancel"))
    return builder.as_markup()


def get_done_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сконвертировать другой файл", callback_data="conv:new_file")
    return builder.as_markup()


def get_url_format_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 MP4 (Видео)", callback_data="urlconv:MP4")
    builder.button(text="🎵 MP3 (Аудио)", callback_data="urlconv:MP3")
    builder.button(text="🖼 PNG (Фото/Превью)", callback_data="urlconv:PNG")
    builder.adjust(1, 2)
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="urlconv:cancel"))
    return builder.as_markup()


def get_url_done_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Сконвертировать другую ссылку", callback_data="urlconv:new_url")
    return builder.as_markup()


def get_broadcast_type_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Выборочно", callback_data="broadcast:targeted")
    builder.button(text="👥 Всем", callback_data="broadcast:all")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="broadcast:cancel"))
    return builder.as_markup()


def get_broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отмена", callback_data="broadcast:cancel")
    return builder.as_markup()
