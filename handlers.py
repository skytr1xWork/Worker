import io
import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from converter import (
    AUDIO_EXTENSIONS,
    AUDIO_MIME_TYPES,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_DOCUMENT_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    convert_audio,
    convert_document,
    convert_image,
    detect_file_type,
    format_size,
    normalize_format,
)
from keyboards import (
    get_cancel_keyboard,
    get_done_keyboard,
    get_format_keyboard,
    get_main_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="main_router")


class ConverterState(StatesGroup):
    waiting_for_file = State()
    selecting_format = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    welcome_text = (
        "Привет! Я хелпер бот для (и от) skytr1x.\n"
        "Я создан для помощи в быту и для навигации в самой экосистеме skytr1x лол.\n"
        "Внизу будут кнопки с моими умениями вдруг чего"
    )
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@router.message(Command("help"))
@router.message(F.text == "Помощь")
@router.message(F.text == "Привет")
async def cmd_help(message: Message) -> None:
    help_text = (
        "Я по факту массивный бот хелпер для всего (по крайней мере по задумке), но так же имею пару комманд, которые могут как то тебя заинтересовать:\n\n"
        "/support - поддержать любимого skytr1x\n"
        "/about - а кто такой ваще ваш skytr1x\n\n"
        "Пользуйтесь на здоровье, и да, подписок в боте не будет хD"
    )
    await message.answer(
        help_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@router.message(Command("about"))
@router.message(F.text == "О создателе")
async def cmd_about(message: Message) -> None:
    about_text = (
        "Я (skytr1x) - программист на Python и С++. Занимаюсь относительно маленькими, но полезными проектами.\n\n"
        'У меня есть свой <a href="https://github.com/skytr1x">GitHub</a>, но если вы пользователь exteraGram, '
        'у меня есть <a href="https://t.me/sktrxdev">Телеграм канал</a>, где я выкладываю плагины для этого клиента.'
    )
    await message.answer(
        about_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(Command("support"))
@router.message(F.text == "Поддержка")
async def cmd_support(message: Message) -> None:
    support_text = (
        "Вы можете меня поддержать просто подписавшись на мой <a href='https://t.me/sktrxdev'>тгк</a> или же поддержать меня материально закинув мне чутка на хлеб через:\n"
        "TON (GRAM): UQDL0xc0CBLU2_K15rxjpf-dga0f36qXYht-UTlSKESpoNlq\n\n"
        "Я буду очень благодарен :3"
    )
    await message.answer(
        support_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(Command("cancel"))
@router.message(F.text == "Отмена")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено. Вы вернулись в главное меню.",
        reply_markup=get_main_keyboard(),
    )


@router.message(Command("convert"))
@router.message(Command("converter"))
@router.message(F.text == "Конвертер")
@router.message(F.text.lower() == "конвертер")
async def start_converter_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(ConverterState.waiting_for_file)
    prompt_text = (
        "Отправь мне файл (изображение, документ или аудио):\n"
        "• Картинки: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
        "• Текст/документы: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
        "• Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
        "Обязательно без сжатия."
    )
    await message.answer(
        prompt_text,
        reply_markup=get_cancel_keyboard(),
    )


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc:
        return

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    category, detected_format = detect_file_type(doc.file_name, doc.mime_type)

    if not detected_format or not category:
        await message.answer(
            f"Файл «{doc.file_name or 'документ'}» в неподдерживаемом формате.\n\n"
            "Поддерживаемые форматы:\n"
            "• Картинки: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
            "• Текст/документы: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
            "• Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2",
        )
        return

    file_name = doc.file_name or f"file.{detected_format.lower()}"
    file_size_str = format_size(doc.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=doc.file_id,
        file_name=file_name,
        source_format=detected_format,
        category=category,
        file_size=doc.file_size or 0,
    )

    caption = (
        f"Файл получен\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n\n"
        f"Выберите в какой формат его нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category=category),
        parse_mode="Markdown",
    )


@router.message(F.audio)
async def handle_audio(message: Message, state: FSMContext) -> None:
    audio = message.audio
    if not audio:
        return

    if audio.file_size and audio.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    detected_format = "MP3"
    if audio.file_name:
        ext = os.path.splitext(audio.file_name)[1].lower().lstrip(".")
        if ext in AUDIO_EXTENSIONS:
            detected_format = AUDIO_EXTENSIONS[ext]
    elif audio.mime_type:
        for m_k, f_v in AUDIO_MIME_TYPES.items():
            if m_k in audio.mime_type.lower():
                detected_format = f_v
                break

    file_name = audio.file_name or f"audio.{detected_format.lower()}"
    file_size_str = format_size(audio.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=audio.file_id,
        file_name=file_name,
        source_format=detected_format,
        category="audio",
        file_size=audio.file_size or 0,
    )

    caption = (
        f"Аудиофайл получен\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n\n"
        f"Выберите в какой формат его нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="audio"),
        parse_mode="Markdown",
    )


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext) -> None:
    voice = message.voice
    if not voice:
        return

    if voice.file_size and voice.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    file_name = "voice.opus"
    detected_format = "OPUS"
    file_size_str = format_size(voice.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=voice.file_id,
        file_name=file_name,
        source_format=detected_format,
        category="audio",
        file_size=voice.file_size or 0,
    )

    caption = (
        f"Голосовое сообщение получено\n\n"
        f"Формат: `OPUS`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выберите в какой формат его нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="audio"),
        parse_mode="Markdown",
    )


@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    await message.answer("Отправьте фото файлом без сжатия.")


@router.callback_query(F.data.startswith("conv:"))
async def handle_conversion_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "cancel":
        await callback.answer("Конвертация отменена")
        if callback.message:
            await callback.message.edit_text("Конвертация отменена.")
        await state.clear()
        return

    if action == "new_file":
        await callback.answer()
        await state.set_state(ConverterState.waiting_for_file)
        if callback.message:
            await callback.message.answer(
                "Отправь мне файл (изображение, документ или аудио):\n"
                "• Картинки: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
                "• Текст/документы: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
                "• Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
                "Обязательно без сжатия.",
                reply_markup=get_cancel_keyboard(),
            )
        return

    target_format = normalize_format(action)
    if (
        target_format not in SUPPORTED_IMAGE_FORMATS
        and target_format not in SUPPORTED_DOCUMENT_FORMATS
        and target_format not in SUPPORTED_AUDIO_FORMATS
    ):
        await callback.answer("Неизвестный формат", show_alert=True)
        return

    data = await state.get_data()
    file_id = data.get("file_id")
    orig_filename = data.get("file_name", "file")
    source_format = data.get("source_format", "UNKNOWN")
    category = data.get("category", "image")

    if not file_id:
        await callback.answer("Файл не найден или сессия устарела. Отправьте файл заново.", show_alert=True)
        return

    await callback.answer(f"Конвертирую в {target_format}...")

    if callback.message:
        await callback.message.edit_text(
            f"Конвертирую `{orig_filename}` в {target_format}...",
            parse_mode="Markdown",
        )

    try:
        file_info = await bot.get_file(file_id)
        if not file_info.file_path:
            raise ValueError("Не удалось получить путь к файлу от Telegram")

        file_stream = io.BytesIO()
        await bot.download_file(file_info.file_path, destination=file_stream)
        input_bytes = file_stream.getvalue()

        # Convert based on category
        if category == "audio" or target_format in SUPPORTED_AUDIO_FORMATS:
            output_bytes, ext = convert_audio(input_bytes, source_format, target_format)
        elif category == "document" or target_format in SUPPORTED_DOCUMENT_FORMATS:
            output_bytes, ext = convert_document(input_bytes, source_format, target_format)
        else:
            output_bytes, ext = convert_image(input_bytes, target_format)

        base_name = os.path.splitext(orig_filename)[0] or "converted_file"
        new_filename = f"{base_name}.{ext}"

        output_file = BufferedInputFile(output_bytes, filename=new_filename)

        caption = (
            f"Конвертация завершена\n\n"
            f"Исходный файл: `{orig_filename}` ({source_format})\n"
            f"Результат: `{new_filename}` ({target_format})\n"
            f"Размер: {format_size(len(output_bytes))}"
        )

        if callback.message:
            await callback.message.answer_document(
                document=output_file,
                caption=caption,
                reply_markup=get_done_keyboard(),
                parse_mode="Markdown",
            )

    except Exception as exc:
        logger.exception("Error during conversion")
        if callback.message:
            await callback.message.answer(
                f"Ошибка при конвертации: {exc}\n"
                f"Попробуйте отправить файл повторно или выбрать другой формат.",
                reply_markup=get_main_keyboard(),
            )


@router.message(ConverterState.waiting_for_file)
async def handle_unexpected_file_input(message: Message) -> None:
    await message.answer(
        "Отправьте файл без сжатия.",
        reply_markup=get_cancel_keyboard(),
    )
