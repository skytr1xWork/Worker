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
    get_broadcast_cancel_keyboard,
    get_broadcast_type_keyboard,
    get_cancel_keyboard,
    get_done_keyboard,
    get_format_keyboard,
    get_help_keyboard,
    get_main_keyboard,
    get_qr_done_keyboard,
    get_shazam_done_keyboard,
    get_url_done_keyboard,
    get_url_format_keyboard,
)
from qr_service import generate_qr_image, read_qr_from_image
from tiktok_shazam import shazam_tiktok_url
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
    waiting_for_shazam_url = State()
    selecting_format = State()


class QRState(StatesGroup):
    waiting_for_input = State()


class BroadcastState(StatesGroup):
    waiting_for_recipients = State()
    waiting_for_message = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    welcome_text = (
        "Здарова, щегол (я любя). Я хелпер бот для (и от) skytr1x.\n"
        "Я создан для помощи в быту и для навигации в самой экосистеме skytr1x лол.\n"
        "Для начала хочу уточнить пару деталей на будущее:\n\n"
        "Бот полностью бесплатный, но это компенсируется достаточно маленькой скоростью загрузки файлов, лимитом на размер файлов (20 мб на ввод и 50 мб на вывод) и присутствием очереди на скачивание по ссылке.\n\n"
        "За весь функционал бота отвечаю я лично (@skytr1xz). По любым вопросам функционала или проблемам писать мне лично. Я никого не укушу (наверное).\n\n"
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
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.update_data(help_menu_msg_id=None)
    help_text = (
        "Помощь (может пригодиться)\n\n"
        "Основные команды:\n"
        "/convert - запускает конвертер\n"
        "/url - скачать видео/мп3 по ссылке\n"
        "/qr - управление QR-кодами\n"
        "/shazam - распознать музыку из TikTok\n\n"
        "Дополнительные команды:\n"
        "/support - поддержать создателя (ну пж)\n"
        "/about - о создателе бота\n"
        "/cancel - отменить текущее действие\n\n"
        "Так же с недавнего времени бот сам понимает, что делать с файлами/ссылками, которые он получает :3\n\n"
        "Есть вопросы по чему то конкретному?"
    )
    await message.answer(
        help_text,
        reply_markup=get_help_keyboard(),
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


@router.message(Command("qr"))
@router.message(F.text == "Управление QR")
@router.message(F.text.lower() == "управление qr")
async def start_qr_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(QRState.waiting_for_input)
    prompt_text = (
        "Отправьте ссылку или текст для создания QR-кода, "
        "либо отправьте изображение с QR-кодом для его чтения."
    )
    await message.answer(
        prompt_text,
        reply_markup=get_cancel_keyboard(),
    )


@router.message(QRState.waiting_for_input, F.photo)
async def handle_qr_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.photo:
        return

    photo = message.photo[-1]
    buf = io.BytesIO()
    await bot.download(photo.file_id, destination=buf)
    buf.seek(0)

    results = read_qr_from_image(buf)
    if not results:
        await message.answer(
            "Не удалось обнаружить QR-код на изображении. Убедитесь, что QR-код четкий и не обрезан.",
            reply_markup=get_qr_done_keyboard(),
        )
        return

    decoded_content = "\n\n".join(results)
    await message.answer(
        f"Содержимое QR-кода:\n\n{decoded_content}",
        reply_markup=get_qr_done_keyboard(),
        disable_web_page_preview=False,
    )


@router.message(QRState.waiting_for_input, F.document)
async def handle_qr_document(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    if not doc:
        return

    buf = io.BytesIO()
    await bot.download(doc.file_id, destination=buf)
    buf.seek(0)

    results = read_qr_from_image(buf)
    if not results:
        await message.answer(
            "Не удалось обнаружить QR-код на изображении. Убедитесь, что QR-код четкий и не обрезан.",
            reply_markup=get_qr_done_keyboard(),
        )
        return

    decoded_content = "\n\n".join(results)
    await message.answer(
        f"Содержимое QR-кода:\n\n{decoded_content}",
        reply_markup=get_qr_done_keyboard(),
        disable_web_page_preview=False,
    )


@router.message(QRState.waiting_for_input, F.text)
async def handle_qr_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() in ("отмена", "/cancel"):
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=get_main_keyboard())
        return

    qr_buf = generate_qr_image(text)
    qr_file = BufferedInputFile(qr_buf.getvalue(), filename="qr_code.png")
    await message.answer_photo(
        photo=qr_file,
        caption=f"QR-код для содержимого:\n{text}",
        reply_markup=get_qr_done_keyboard(),
    )


@router.callback_query(F.data.in_({"qr:new_create", "qr:new_read"}))
async def handle_qr_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(QRState.waiting_for_input)
    if callback.message:
        await callback.message.answer(
            "Отправьте ссылку или текст для создания QR-кода, "
            "либо отправьте изображение с QR-кодом для его чтения.",
            reply_markup=get_cancel_keyboard(),
        )


@router.callback_query(F.data.startswith("help:"))
async def handle_help_callback(callback: CallbackQuery) -> None:
    section = callback.data.split(":", 1)[1]

    help_texts = {
        "converter": (
            "Простой конвертер файлов.\n\n"
            "Бот умеет конвертировать:\n\n"
            "Изображения: PNG, JPG, WEBP, BMP, TIFF, ICO, GIF\n"
            "Документы/тексты: TXT, DOCX, MD, CSV, DAT, JSON, XML, LOG, TSV, HTML\n"
            "Аудио: MP3, WAV, OGG, OPUS, FLAC, AAC, M4A, WMA, AIFF, AMR, AC3, MP2\n"
            "Видео: MP4, MOV, WEBM, AVI, MKV, GIF, FLV, WMV, 3GP, TS, MPEG, OGV\n\n"
            "Как использовать:\n"
            "• Просто отправь файл без сжатия (как документ)\n"
            "• Выбери нужный формат из предложенных\n"
            "• Получи готовый файл!\n\n"
            "Ограничения:\n"
            "• Входящий файл: до 20 МБ\n"
            "• Исходящий файл: до 50 МБ\n\n"
            "Также можно конвертировать голосовые сообщения и кружки!"
        ),
        "url_converter": (
            "Конвертер из ссылок.\n\n"
            "Поддерживаемые сервисы:\n\n"
            " • YouTube - видео, Shorts, Music\n"
            " • Pinterest - пины, фото, видео\n"
            " • TikTok - видео\n"
            " • VK - видео, клипы, посты\n"
            " • Яндекс Дзен - видео, статьи\n\n"
            "Форматы конвертации:\n"
            "• MP4 (видео)\n"
            "• MP3 (аудио)\n"
            "• PNG (фото/превью)\n\n"
            "Как использовать:\n"
            "• Просто отправь ссылку\n"
            "• Выбери нужный формат\n"
            "• Дождись загрузки\n\n"
            "Так же есть ограничения:\n"
            "• Есть очередь на скачивание (если кто либо так же скачивает что либо)\n"
            "• Максимальный размер на вывод: 50 МБ"
        ),
        "shazam": (
            "Шазам для Тик Тока (возможно скоро будет больше сервисов)\n\n"
            "Находит трек по ролику из Тик Тока (не всегда правильно)\n\n"
            "Что умеет:\n"
            "• Находить музыку (ну, а че еще)\n"
            "Как использовать:\n"
            "• Отправь ссылку на TikTok видео\n"
            "• Бот распознает музыку автоматически\n"
            "• Если трек не найден, можешь скачать аудио в MP3\n\n"
            "Функция подлежит реворку в скором времени. Лучше сейчас не надеятся на 100% точность.\n"
        ),
        "general": (
            "Общая информация\n\n"
            "О боте:\n"
            "Я хелпер бот для (и от) skytr1x. Создан для помощи и все (xD)\n\n"
            "Особенности:\n"
            "Полностью бесплатный\n"
            "Без подписок\n"
            "Постоянно развивается\n\n"
            "Ограничения:\n"
            "• Небольшая скорость загрузки\n"
            "• Лимит на размер файлов (20 МБ на ввод, 50 МБ на вывод)\n"
            "• Очередь на скачивание по ссылке\n\n"
            "Быстрый старт:\n"
            "Просто отправь файл или ссылку - бот сам поймет, что нужно делать!\n\n"
            "Обратная связь:\n"
            "По всем вопросам (и идеям тоже) пиши мне (@skytr1xz)\n\n"
            "Используй /support чтобы поддержать материально создателя (пожалуйста)\n"
            "Используй /about чтобы узнать больше о создателе"
        ),
    }

    text = help_texts.get(section, "Раздел не найден")
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=get_help_keyboard(),
            parse_mode="Markdown",
        )


@router.message(Command("shazam"))
@router.message(F.text == "Шазам (TikTok)")
@router.message(F.text.lower() == "шазам (tiktok)")
@router.message(F.text.lower() == "шазам")
async def start_shazam_mode(message: Message, state: FSMContext) -> None:
    await state.set_state(UrlConverterState.waiting_for_shazam_url)
    await message.answer(
        "Отправь ссылку на TikTok и я найду тебе музыку. (еще в тесте:3)",
        reply_markup=get_cancel_keyboard(),
        parse_mode="Markdown",
    )


async def process_shazam_for_url(event: Message | CallbackQuery, url: str, state: FSMContext) -> None:
    is_cb = isinstance(event, CallbackQuery)
    target_msg = event.message if is_cb else event

    if is_cb:
        await event.answer("Распознаю музыку...")

    if DOWNLOAD_SEMAPHORE.locked():
        if target_msg:
            txt = (
                "Вы в очереди на обработку...\n"
                "Очередь сделана в целях экономии ресурсов и сохранения сервиса бесплатным для вас!"
            )
            if is_cb:
                await target_msg.edit_text(txt, parse_mode="Markdown")
            else:
                await target_msg.answer(txt, parse_mode="Markdown")

    async with DOWNLOAD_SEMAPHORE:
        status_txt = "Извлекаю аудио и распознаю музыку через Shazam...\nПожалуйста, подождите немного."
        if is_cb and target_msg:
            status_msg = target_msg
            await target_msg.edit_text(status_txt, parse_mode="Markdown")
        else:
            status_msg = await target_msg.answer(status_txt, parse_mode="Markdown")

        try:
            track_info = await shazam_tiktok_url(url)
            if not track_info:
                not_found_txt = (
                    "К сожалению, Shazam не смог определить трек в этом видео из TikTok "
                    "(возможно, это оригинальный голос автора или сильный ремикс).\n\n"
                    "Вы можете скачать аудиодорожку напрямую в формате MP3:"
                )
                await state.update_data(url=url, service="tiktok", service_name="TikTok")
                await status_msg.edit_text(
                    not_found_txt,
                    reply_markup=get_shazam_done_keyboard(),
                )
                return

            title = track_info.get("title", "Неизвестно")
            artist = track_info.get("artist", "Неизвестно")
            album = track_info.get("album")
            genres = track_info.get("genres")
            shazam_url = track_info.get("shazam_url")
            apple_music_url = track_info.get("apple_music_url")
            cover_url = track_info.get("cover_url")

            lines = [
                "Музыка из TikTok найдена!\n",
                f"• Название: `{title}`",
                f"• Исполнитель: `{artist}`",
            ]
            if album:
                lines.append(f"• Альбом: `{album}`")
            if genres:
                lines.append(f"• Жанр: `{genres}`")

            links = []
            if shazam_url:
                links.append(f"[Shazam]({shazam_url})")
            if apple_music_url:
                links.append(f"[Apple Music]({apple_music_url})")
            if links:
                lines.append("")
                lines.append("" + " | ".join(links))

            text_result = "\n".join(lines)
            await state.update_data(url=url, service="tiktok", service_name="TikTok")

            if cover_url:
                try:
                    await target_msg.answer_photo(
                        photo=cover_url,
                        caption=text_result,
                        reply_markup=get_shazam_done_keyboard(),
                        parse_mode="Markdown",
                    )
                    await status_msg.delete()
                    return
                except Exception:
                    pass

            await status_msg.edit_text(
                text_result,
                reply_markup=get_shazam_done_keyboard(),
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )

        except Exception as exc:
            logger.exception("Error during Shazam recognition")
            await target_msg.answer(
                f"Ошибка при распознавании трека: {exc}\n"
                f"Попробуйте отправить ссылку заново или скачать в MP3.",
                reply_markup=get_shazam_done_keyboard(),
            )
        finally:
            gc.collect()


@router.message(F.text.regexp(r'https?://[^\s<>"]+'))
async def handle_url_message(message: Message, state: FSMContext) -> None:
    raw_text = message.text or ""
    url = extract_first_url(raw_text)
    if not url:
        return

    service_key, service_name = detect_service(url)
    if not service_key:
        current_st = await state.get_state()
        if current_st in (UrlConverterState.waiting_for_url, UrlConverterState.waiting_for_shazam_url):
            await message.answer(
                "Эта ссылка не принадлежит поддерживаемым сервисам.\n\n"
                "Поддерживаются: YouTube, Pinterest, TikTok, VK, Яндекс Дзен.",
                reply_markup=get_cancel_keyboard(),
            )
        return

    current_st = await state.get_state()

    # Автоматическое определение: TikTok ссылка = Shazam
    if service_key == "tiktok" and current_st not in (UrlConverterState.waiting_for_url, UrlConverterState.selecting_format):
        await state.set_state(UrlConverterState.selecting_format)
        await state.update_data(url=url, service=service_key, service_name=service_name)

        caption = (
            f"Обнаружена ссылка TikTok\n\n"
            f"Что хочешь сделать?"
        )
        await message.answer(
            caption,
            reply_markup=get_url_format_keyboard(service_key),
            parse_mode="Markdown",
        )
        return

    if current_st == UrlConverterState.waiting_for_shazam_url:
        await process_shazam_for_url(message, url, state)
        return

    await state.set_state(UrlConverterState.selecting_format)
    await state.update_data(url=url, service=service_key, service_name=service_name)

    caption = (
        f"Ссылка распознана: **{service_name}**\n\n"
        f"Выберите, во что нужно сконвертировать:"
    )
    await message.answer(
        caption,
        reply_markup=get_url_format_keyboard(service_key),
        parse_mode="Markdown",
    )


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc:
        return

    # Проверка на QR-код в режиме QR
    current_st = await state.get_state()
    if current_st == QRState.waiting_for_input:
        await handle_qr_document(message, state, message.bot)
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
        f"Файл получен!\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
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
        f"Видео получено!\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
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
        f"Кружок получен!\n\n"
        f"Формат: `MP4`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
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
        f"Анимация получена!\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
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
        f"Аудиофайл получен!\n\n"
        f"Имя: `{file_name}`\n"
        f"Формат: `{detected_format}`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
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
        f"Голосовое сообщение получено!\n\n"
        f"Формат: `OPUS`\n"
        f"Размер: {file_size_str}\n\n"
        f"Выбери формат для конвертации:"
    )

    await message.answer(
        caption,
        reply_markup=get_format_keyboard(detected_format, category="audio"),
        parse_mode="Markdown",
    )


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext) -> None:
    current_st = await state.get_state()

    # Если в режиме QR - обрабатываем как QR
    if current_st == QRState.waiting_for_input:
        await handle_qr_photo(message, state, message.bot)
        return

    # Иначе просим отправить без сжатия
    await message.answer(
            "Фото без сжатия.\n Отправь его как документ."
    )


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
    if target_format not in ("MP4", "MP3", "PNG", "SHAZAM"):
        await callback.answer("Неизвестный формат", show_alert=True)
        return

    data = await state.get_data()
    url = data.get("url")
    service_name = data.get("service_name", "Сервис")

    if not url:
        await callback.answer("Ссылка не найдена или сессия устарела. Отправьте ссылку заново.", show_alert=True)
        return

    if target_format == "SHAZAM":
        await process_shazam_for_url(callback, url, state)
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


@router.callback_query(F.data == "shazam:new_url")
async def handle_shazam_new_url(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(UrlConverterState.waiting_for_shazam_url)
    if callback.message:
        await callback.message.answer(
            "Отправь ссылку на TikTok и я найду тебе музыку. (еще в тесте:3)",
            reply_markup=get_cancel_keyboard(),
            parse_mode="Markdown",
        )


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
# Admin Broadcast (/sl) System
# ==========================================

@router.message(Command("sl"))
@router.message(F.text.lower() == "sl")
async def cmd_broadcast_start(message: Message, state: FSMContext) -> None:
    """
    Starts the broadcast wizard for authorized admins.
    Shows buttons 'Выборочно' and 'Всем'.
    """
    if not message.from_user:
        return

    admin_ids = get_admin_ids()
    if not admin_ids or message.from_user.id not in admin_ids:
        # Non-admin: silently ignore
        return

    await state.clear()
    total_users = db.get_users_count()
    await message.answer(
        f"📢 **Панель рассылки сообщений**\n\n"
        f"👥 Зарегистрировано пользователей в базе: **{total_users}**\n\n"
        f"Выберите тип рассылки:",
        reply_markup=get_broadcast_type_keyboard(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("broadcast:"))
async def handle_broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    admin_ids = get_admin_ids()
    if not callback.from_user or callback.from_user.id not in admin_ids:
        await callback.answer("У вас нет доступа", show_alert=True)
        return

    action = callback.data.split(":", 1)[1]

    if action == "cancel":
        await callback.answer("Рассылка отменена")
        await state.clear()
        if callback.message:
            await callback.message.edit_text("Рассылка отменена.")
        return

    if action == "all":
        await callback.answer()
        await state.set_state(BroadcastState.waiting_for_message)
        await state.update_data(target_mode="all")
        if callback.message:
            await callback.message.edit_text(
                "Напиши сообщение для рассылки",
                reply_markup=get_broadcast_cancel_keyboard(),
            )
        return

    if action == "targeted":
        await callback.answer()
        await state.set_state(BroadcastState.waiting_for_recipients)
        await state.update_data(target_mode="targeted")
        if callback.message:
            await callback.message.edit_text(
                "Сначала укажи ID в формате 1111111, 1111111, 1111111",
                reply_markup=get_broadcast_cancel_keyboard(),
            )
        return


@router.message(BroadcastState.waiting_for_recipients)
async def handle_broadcast_recipients_input(message: Message, state: FSMContext) -> None:
    admin_ids = get_admin_ids()
    if not message.from_user or message.from_user.id not in admin_ids:
        return

    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=get_main_keyboard())
        return

    raw_input = message.text or ""
    valid_ids, not_found = db.resolve_recipients(raw_input)

    if not valid_ids:
        not_found_str = f" (не найдены: {', '.join(not_found)})" if not_found else ""
        await message.answer(
            f"❌ Не удалось найти указанных пользователей{not_found_str}.\n"
            f"Укажи ID в формате 1111111, 1111111, 1111111 (или юзернеймы: @username1, @username2):",
            reply_markup=get_broadcast_cancel_keyboard(),
        )
        return

    await state.update_data(recipients=valid_ids)
    await state.set_state(BroadcastState.waiting_for_message)

    info_note = ""
    if not_found:
        info_note = f"\n⚠️ Не найдены в базе бота: {', '.join(not_found)}"

    await message.answer(
        f"✅ Найдено получателей: **{len(valid_ids)}**{info_note}\n\n"
        f"Напиши сообщение для рассылки",
        reply_markup=get_broadcast_cancel_keyboard(),
        parse_mode="Markdown",
    )


@router.message(BroadcastState.waiting_for_message)
async def handle_broadcast_message_input(message: Message, state: FSMContext, bot: Bot) -> None:
    admin_ids = get_admin_ids()
    if not message.from_user or message.from_user.id not in admin_ids:
        return

    if message.text and message.text.strip().lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    target_mode = data.get("target_mode", "all")

    if target_mode == "targeted":
        user_ids = data.get("recipients", [])
    else:
        user_ids = db.get_all_user_ids()

    if not user_ids:
        user_ids = [message.from_user.id]

    await state.clear()

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
            # Copy message preserves everything: text, photo, video, caption, entities, audio, etc.
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            success_count += 1
        except Exception as exc:
            failed_count += 1
            err_str = str(exc).lower()
            if any(k in err_str for k in ("forbidden", "blocked", "deactivated", "chat not found", "user is deactivated")):
                blocked_count += 1

        # Rate limit: 25 messages per second
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
        f"👥 Всего получателей: **{total_users}**\n"
        f"✅ Успешно доставлено: **{success_count}**\n"
        f"🚫 Заблокировали бота: **{blocked_count}**\n"
        f"❌ Прочие ошибки: **{failed_count - blocked_count}**",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )
