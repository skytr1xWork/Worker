import asyncio
import gc
import io
import logging
import os
import re
import tempfile

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message

import db
from converter import (
    AUDIO_EXTENSIONS,
    AUDIO_MIME_TYPES,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_DOCUMENT_FORMATS,
    SUPPORTED_IMAGE_FORMATS,
    SUPPORTED_VIDEO_FORMATS,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_TYPES,
    convert_audio,
    convert_document,
    convert_image,
    convert_video,
    detect_file_type,
    format_size,
    normalize_format,
)
from keyboards import (
    get_cancel_keyboard,
    get_done_keyboard,
    get_format_keyboard,
    get_main_keyboard,
    get_url_done_keyboard,
    get_url_format_keyboard,
)
from url_converter import (
    DOWNLOAD_SEMAPHORE,
    detect_service,
    download_url_to_file,
    extract_first_url,
)

logger = logging.getLogger(__name__)

router = Router(name="main_router")


@router.message.outer_middleware()
async def track_user_middleware(handler, event: Message, data):
    """Automatically records every active bot user to the database."""
    if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
        db.add_user(
            user_id=event.from_user.id,
            username=event.from_user.username,
            first_name=event.from_user.first_name,
        )
    return await handler(event, data)


def get_admin_ids() -> set[int]:
    """Returns set of admin user IDs defined in environment variables (ADMIN_ID / ADMIN_IDS / OWNER_ID)."""
    raw = os.getenv("ADMIN_ID") or os.getenv("ADMIN_IDS") or os.getenv("OWNER_ID") or ""
    ids = set()
    for chunk in re.split(r'[,\s;]+', raw.strip()):
        if chunk.isdigit() or (chunk.startswith("-") and chunk[1:].isdigit()):
            ids.add(int(chunk))
    return ids


class ConverterState(StatesGroup):
    waiting_for_file = State()
    selecting_format = State()


class UrlConverterState(StatesGroup):
    waiting_for_url = State()
    selecting_format = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    welcome_text = (
        "Здарова, щегол (я любя). Я хелпер бот для (и от) skytr1x.\n"
        "Я создан для помощи в быту и для навигации в самой экосистеме skytr1x лол.\n"
        "Для начала хочу уточнить пару деталей на будущее:\n\n"
        "Бот полностью бесплатный, но это компенсируется достаточно маленькой скоростью загрузки файлов, лимитом на размер файлов (20 мб на ввод и 50 мб на вывод) и присутствием очереди на скачивание по ссылке.\n\n"
        "Возможно когда-то будет возможность расширения материального функционала бота, но уж точно не в ближайшее время.\n"
        "Я стараюсь делать бота максимально быстрым и удобным в пределах возможностей."
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
        "Отправь мне файл (картинку, документ, аудио или видео):\n"
        "• Картинки: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
        "• Документы/текст: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
        "• Аудио: MP3, WAV, OGG, OPUS (Голосовые сообщения), FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
        "• Видео: MP4, MOV, WEBM, AVI, MKV, GIF, FLV, WMV, 3GP, TS, MPEG, OGV\n\n"
        "Обязательно без сжатия.\n"
        "Сообщение каждого формата должно быть меньше 20 мб. Но если вы меня поддержите (не намек) то можно будет открыть свой впс и отправлять файлы больше :DDD"
    )
    await message.answer(
        prompt_text,
        reply_markup=get_cancel_keyboard(),
    )


@router.message(Command("url"))
@router.message(Command("convert_url"))
@router.message(F.text == "Конвертер (из ссылки)")
@router.message(F.text.lower() == "конвертер (из ссылки)")
async def start_url_converter_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(UrlConverterState.waiting_for_url)
    prompt_text = (
        "Отправь мне ссылку на медиа из поддерживаемого сервиса:\n"
        "• YouTube (видео, Shorts, Music)\n"
        "• Pinterest (пины, фото, видео)\n"
        "• TikTok (видео)\n"
        "• VK (видео, клипы, посты)\n"
        "• Яндекс Дзен (видео, статьи)\n\n"
        "Я могу сконвертировать медиа в **MP4**, **MP3** или **PNG**."
    )
    await message.answer(
        prompt_text,
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )


@router.message(F.text.regexp(r'https?://[^\s<>"]+'))
async def handle_url_message(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    url = extract_first_url(raw_text)
    if not url:
        return

    service_key, service_name = detect_service(url)
    if not service_key:
        current_st = await state.get_state()
        if current_st == UrlConverterState.waiting_for_url:
            await message.answer(
                "Эта ссылка не принадлежит поддерживаемым сервисам.\n\n"
                "Поддерживаются: YouTube, Pinterest, TikTok, VK, Яндекс Дзен.",
                reply_markup=get_cancel_keyboard(),
            )
        return

    await state.set_state(UrlConverterState.selecting_format)
    await state.update_data(url=url, service=service_key, service_name=service_name)

    caption = (
        f"Ссылка распознана: **{service_name}**\n\n"
        f"URL: `{url}`\n\n"
        f"Выберите, во что нужно сконвертировать:"
    )
    await message.answer(
        caption,
        reply_markup=get_url_format_keyboard(),
        parse_mode="Markdown",
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
            "• Документы: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
            "• Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
            "• Видео: MP4, MOV, WEBM, AVI, MKV, GIF, FLV, WMV, 3GP, TS, MPEG, OGV",
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


@router.message(F.video)
async def handle_video(message: Message, state: FSMContext) -> None:
    video = message.video
    if not video:
        return

    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    detected_format = "MP4"
    if video.file_name:
        ext = os.path.splitext(video.file_name)[1].lower().lstrip(".")
        if ext in VIDEO_EXTENSIONS:
            detected_format = VIDEO_EXTENSIONS[ext]
    elif video.mime_type:
        for m_k, f_v in VIDEO_MIME_TYPES.items():
            if m_k in video.mime_type.lower():
                detected_format = f_v
                break

    file_name = video.file_name or f"video.{detected_format.lower()}"
    file_size_str = format_size(video.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=video.file_id,
        file_name=file_name,
        source_format=detected_format,
        category="video",
        file_size=video.file_size or 0,
    )

    caption = (
        f"Видео получено\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n\n"
        f"Выберите в какой формат его нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="video"),
        parse_mode="Markdown",
    )


@router.message(F.video_note)
async def handle_video_note(message: Message, state: FSMContext) -> None:
    vn = message.video_note
    if not vn:
        return

    if vn.file_size and vn.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    file_name = "video_note.mp4"
    detected_format = "MP4"
    file_size_str = format_size(vn.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=vn.file_id,
        file_name=file_name,
        source_format=detected_format,
        category="video",
        file_size=vn.file_size or 0,
    )

    caption = (
        f"Видеосообщение (кружок) получено\n\n"
        f"Формат: `MP4`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выберите в какой формат его нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="video"),
        parse_mode="Markdown",
    )


@router.message(F.animation)
async def handle_animation(message: Message, state: FSMContext) -> None:
    anim = message.animation
    if not anim:
        return

    if anim.file_size and anim.file_size > 20 * 1024 * 1024:
        await message.answer(
            "Размер файла превышает 20 МБ.\n"
            "Пожалуйста, отправьте файл меньшего размера."
        )
        return

    file_name = anim.file_name or "animation.mp4"
    detected_format = "GIF" if anim.mime_type == "image/gif" else "MP4"
    file_size_str = format_size(anim.file_size or 0)

    await state.set_state(ConverterState.selecting_format)
    await state.update_data(
        file_id=anim.file_id,
        file_name=file_name,
        source_format=detected_format,
        category="video",
        file_size=anim.file_size or 0,
    )

    caption = (
        f"Анимация получена\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n\n"
        f"Выберите в какой формат её нужно конвертировать:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="video"),
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


@router.callback_query(F.data.startswith("urlconv:"))
async def handle_url_conversion_callback(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "cancel":
        await callback.answer("Конвертация отменена")
        if callback.message:
            await callback.message.edit_text("Конвертация ссылки отменена.")
        await state.clear()
        return

    if action == "new_url":
        await callback.answer()
        await state.set_state(UrlConverterState.waiting_for_url)
        if callback.message:
            await callback.message.answer(
                "Отправь мне ссылку (YouTube, Pinterest, TikTok, VK, Яндекс Дзен):",
                reply_markup=get_cancel_keyboard(),
            )
        return

    target_format = action.upper()
    if target_format not in ("MP4", "MP3", "PNG"):
        await callback.answer("Неизвестный формат", show_alert=True)
        return

    data = await state.get_data()
    url = data.get("url")
    service_name = data.get("service_name", "Сервис")

    if not url:
        await callback.answer("Ссылка не найдена или сессия устарела. Отправьте ссылку заново.", show_alert=True)
        return

    await callback.answer(f"Запрос принят: {target_format}")

    # If another download process is already running, notify about the queue
    if DOWNLOAD_SEMAPHORE.locked():
        if callback.message:
            await callback.message.edit_text(
                f"Вы в очереди на скачивание...\n"
                f"Очередь сделана в целях экономии ресурсов и сохранения сервиса бесплатным для вас!",
                parse_mode="Markdown",
            )

    async with DOWNLOAD_SEMAPHORE:
        if callback.message:
            await callback.message.edit_text(
                f"Скачиваю и конвертирую из **{service_name}** в `{target_format}`...\n"
                f"Пожалуйста, подождите немного.",
                parse_mode="Markdown",
            )

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                file_path, ext, filename, file_size = await asyncio.to_thread(
                    download_url_to_file, url, target_format, temp_dir
                )

                if file_size > 50 * 1024 * 1024:
                    if callback.message:
                        await callback.message.answer(
                            "Файл получился больше 50 МБ (лимит Telegram на отправку ботом).\n"
                            "Попробуйте выбрать другой формат (например, MP3 или PNG).",
                            reply_markup=get_main_keyboard(),
                        )
                    return

                output_file = FSInputFile(file_path, filename=filename)
                caption = (
                    f"Готово! Конвертация ссылки завершена\n\n"
                    f"Сервис: **{service_name}**\n"
                    f"Формат: `{target_format}`\n"
                    f"Размер: {format_size(file_size)}"
                )

                if callback.message:
                    if target_format == "MP4":
                        try:
                            await callback.message.answer_video(
                                video=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )
                        except Exception:
                            await callback.message.answer_document(
                                document=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )
                    elif target_format == "MP3":
                        try:
                            await callback.message.answer_audio(
                                audio=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )
                        except Exception:
                            await callback.message.answer_document(
                                document=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )
                    elif target_format == "PNG":
                        try:
                            await callback.message.answer_photo(
                                photo=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )
                        except Exception:
                            await callback.message.answer_document(
                                document=output_file,
                                caption=caption,
                                reply_markup=get_url_done_keyboard(),
                                parse_mode="Markdown",
                            )

        except Exception as exc:
            logger.exception("Error during URL conversion")
            if callback.message:
                await callback.message.answer(
                    f"Ошибка при обработке ссылки: {exc}\n"
                    f"Проверьте доступность ссылки или попробуйте другой формат.",
                    reply_markup=get_main_keyboard(),
                )
        finally:
            gc.collect()


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
                "Отправь мне файл (картинку, документ, аудио или видео):\n"
                "• Картинки: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
                "• Текст/документы: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
                "• Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
                "• Видео: MP4, MOV, WEBM, AVI, MKV, GIF, FLV, WMV, 3GP, TS, MPEG, OGV\n"
                "Обязательно без сжатия.",
                reply_markup=get_cancel_keyboard(),
            )
        return

    target_format = normalize_format(action)
    if (
        target_format not in SUPPORTED_IMAGE_FORMATS
        and target_format not in SUPPORTED_DOCUMENT_FORMATS
        and target_format not in SUPPORTED_AUDIO_FORMATS
        and target_format not in SUPPORTED_VIDEO_FORMATS
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

        if category == "video" or target_format in SUPPORTED_VIDEO_FORMATS:
            output_bytes, ext = convert_video(input_bytes, source_format, target_format, orig_filename=orig_filename)
        elif category == "audio" or target_format in SUPPORTED_AUDIO_FORMATS:
            output_bytes, ext = convert_audio(input_bytes, source_format, target_format, orig_filename=orig_filename)
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
            if target_format in ("MP4", "MOV", "WEBM", "MKV", "AVI"):
                try:
                    await callback.message.answer_video(
                        video=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
                except Exception:
                    await callback.message.answer_document(
                        document=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
            elif target_format == "GIF":
                try:
                    await callback.message.answer_animation(
                        animation=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
                except Exception:
                    await callback.message.answer_document(
                        document=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
            elif target_format == "OPUS":
                try:
                    await callback.message.answer_voice(
                        voice=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
                except Exception:
                    await callback.message.answer_document(
                        document=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
            elif target_format in SUPPORTED_AUDIO_FORMATS:
                try:
                    await callback.message.answer_audio(
                        audio=output_file,
                        caption=caption,
                        title=base_name,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
                except Exception:
                    await callback.message.answer_document(
                        document=output_file,
                        caption=caption,
                        reply_markup=get_done_keyboard(),
                        parse_mode="Markdown",
                    )
            else:
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


@router.message(UrlConverterState.waiting_for_url)
async def handle_unexpected_url_input(message: Message) -> None:
    await message.answer(
        "Отправьте ссылку на видео, фото или аудио (YouTube, Pinterest, TikTok, VK, Яндекс Дзен).",
        reply_markup=get_cancel_keyboard(),
    )


# ==========================================
# Admin Broadcast (/sl) Handler
# ==========================================

@router.message(Command("sl"))
async def cmd_broadcast(message: Message, bot: Bot) -> None:
    """
    Broadcasts a message to all bot users.
    Only available to users with IDs specified in ADMIN_ID / ADMIN_IDS / OWNER_ID env variables.
    """
    if not message.from_user:
        return

    admin_ids = get_admin_ids()
    if not admin_ids or message.from_user.id not in admin_ids:
        # Non-admin: silently ignore to keep command hidden
        return

    raw_text = message.text or ""
    broadcast_text = re.sub(r"^/sl(?:@\w+)?\s*", "", raw_text, flags=re.IGNORECASE).strip()
    is_reply = bool(message.reply_to_message)

    if not broadcast_text and not is_reply:
        await message.answer(
            "❌ **Формат команды рассылки:**\n\n"
            "• `/sl <текст сообщения>` — отправить текст всем пользователям бота\n"
            "• Ответить командой `/sl` на любое сообщение (текст/фото/видео/документ)\n\n"
            f"👥 Пользователей в базе: **{db.get_users_count()}**",
            parse_mode="Markdown",
        )
        return

    user_ids = db.get_all_user_ids()
    if not user_ids:
        # If database is empty, include current user
        user_ids = [message.from_user.id]

    total_users = len(user_ids)
    status_msg = await message.answer(
        f"⏳ **Начинаю рассылку...**\n"
        f"👥 Получателей: {total_users}\n"
        f"Пожалуйста, подождите завершения отправки.",
        parse_mode="Markdown",
    )

    success_count = 0
    failed_count = 0
    blocked_count = 0

    for idx, uid in enumerate(user_ids, 1):
        try:
            if is_reply and message.reply_to_message:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=message.chat.id,
                    message_id=message.reply_to_message.message_id,
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=broadcast_text,
                    disable_web_page_preview=False,
                )
            success_count += 1
        except Exception as exc:
            failed_count += 1
            err_str = str(exc).lower()
            if any(k in err_str for k in ("forbidden", "blocked", "deactivated", "chat not found", "user is deactivated")):
                blocked_count += 1

        # Rate limit: 25 messages per second to strictly follow Telegram limits
        await asyncio.sleep(0.04)

        if total_users > 20 and (idx % 25 == 0 or idx == total_users):
            try:
                await status_msg.edit_text(
                    f"⏳ **Рассылка в процессе...** ({idx}/{total_users})\n"
                    f"✅ Доставлено: {success_count}\n"
                    f"❌ Ошибок / заблокировано: {failed_count}",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    await message.answer(
        f"📢 **Рассылка успешно завершена!**\n\n"
        f"👥 Всего пользователей: **{total_users}**\n"
        f"✅ Успешно доставлено: **{success_count}**\n"
        f"🚫 Заблокировали бота: **{blocked_count}**\n"
        f"❌ Прочие ошибки: **{failed_count - blocked_count}**",
        parse_mode="Markdown",
    )
