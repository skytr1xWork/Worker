import asyncio
import gc
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from PIL import Image

for p in ("/usr/lib/python3.14/site-packages", "/usr/lib/python3/dist-packages", "/usr/local/lib/python3.14/dist-packages"):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.append(p)

logger = logging.getLogger(__name__)

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

# Public Invidious instances for YouTube fallback
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacyredirect.com",
    "https://invidious.fdn.fr",
    "https://yewtu.be",
    "https://invidious.lunar.icu",
]


def _fetch_invidious_video_info(video_id: str) -> dict | None:
    """Fetch video info from Invidious API as fallback for YouTube"""
    for instance in INVIDIOUS_INSTANCES:
        try:
            url = f"{instance}/api/v1/videos/{video_id}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                logger.info(f"✓ Invidious API success: {instance}")
                return data
        except Exception as e:
            logger.debug(f"Invidious instance {instance} failed: {e}")
            continue
    logger.warning("All Invidious instances failed")
    return None


def _extract_youtube_video_id(url: str) -> str | None:
    """Extract video ID from YouTube URL"""
    patterns = [
        r'(?:v=|/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be/([0-9A-Za-z_-]{11})',
        r'embed/([0-9A-Za-z_-]{11})',
        r'shorts/([0-9A-Za-z_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _setup_cookies_from_env():
    """Setup cookies from environment variable or secret file for containerized environments"""
    cookie_path = "/tmp/yt-dlp-cookies.txt"

    # Method 1: Secret File (Render supports mounting files to /etc/secrets/)
    secret_file_paths = [
        "/etc/secrets/yt-cookies.txt",
        "/etc/secrets/cookies.txt",
        os.path.expanduser("~/.config/yt-dlp/cookies.txt"),
    ]
    for secret_path in secret_file_paths:
        if os.path.exists(secret_path):
            try:
                shutil.copy(secret_path, cookie_path)
                logger.info(f"✓ YouTube cookies loaded from secret file: {secret_path}")
                return
            except Exception as e:
                logger.error(f"✗ Failed to copy secret file {secret_path}: {e}")

    # Method 2: SAPISID only (long-lived, 2-3 years)
    sapisid = os.getenv("YT_SAPISID")
    if sapisid:
        try:
            # SAPISID format: <hash>/<token> or just <token>
            if '/' in sapisid:
                hash_part, token_part = sapisid.split('/', 1)
            else:
                hash_part = token_part = sapisid

            expiry = int(time.time()) + 94608000  # ~3 years
            cookie_content = f"""# Netscape HTTP Cookie File
# Generated for yt-dlp YouTube authentication

.youtube.com	TRUE	/	FALSE	{expiry}	VISITOR_INFO1_LIVE	{hash_part}
.youtube.com	TRUE	/	FALSE	{expiry}	PREF	f1=50000000
.youtube.com	TRUE	/	FALSE	{expiry}	APISID	{hash_part}
.youtube.com	TRUE	/	TRUE	{expiry}	SAPISID	{sapisid}
.youtube.com	TRUE	/	FALSE	{expiry}	__Secure-1PAPISID	{sapisid}
.youtube.com	TRUE	/	TRUE	{expiry}	__Secure-3PAPISID	{sapisid}
.youtube.com	TRUE	/	FALSE	{expiry}	HSID	{hash_part}
.youtube.com	TRUE	/	TRUE	{expiry}	SSID	{hash_part}
.youtube.com	TRUE	/	FALSE	{expiry}	SID	{token_part}
.youtube.com	TRUE	/	TRUE	{expiry}	__Secure-1PSID	{token_part}
.youtube.com	TRUE	/	TRUE	{expiry}	__Secure-3PSID	{token_part}
.youtube.com	TRUE	/	FALSE	{expiry}	LOGIN_INFO	AFmmF2swRQIhAKZ
"""
            with open(cookie_path, "w") as f:
                f.write(cookie_content)
            logger.info(f"✓ YouTube SAPISID cookie configured → {cookie_path}")
            return
        except Exception as e:
            logger.error(f"✗ Failed to setup SAPISID: {e}")

    # Method 3: Full cookies file (base64 encoded)
    yt_cookies_b64 = os.getenv("YT_COOKIES_BASE64")
    if yt_cookies_b64:
        import base64
        try:
            # Remove any whitespace/newlines that might have been added
            yt_cookies_b64 = yt_cookies_b64.strip().replace('\n', '').replace('\r', '').replace(' ', '')
            cookie_data = base64.b64decode(yt_cookies_b64)
            with open(cookie_path, "wb") as f:
                f.write(cookie_data)
            logger.info(f"✓ YouTube cookies loaded from YT_COOKIES_BASE64 → {cookie_path} ({len(cookie_data)} bytes)")
            return
        except Exception as e:
            logger.error(f"✗ Failed to decode YT_COOKIES_BASE64: {e}")
            logger.error(f"   Base64 length: {len(yt_cookies_b64)} chars, first 50: {yt_cookies_b64[:50]}")

    logger.warning("No YouTube cookies configured (secret file, YT_SAPISID, or YT_COOKIES_BASE64)")


# Auto-setup cookies on module import
_setup_cookies_from_env()


def ttl_lru_cache(ttl_seconds=300, maxsize=128):
    def decorator(func):
        cache = {}
        cache_times = {}

        def wrapper(url: str):
            current_time = time.time()

            if url in cache:
                if current_time - cache_times[url] < ttl_seconds:
                    logger.debug(f"Pinterest cache HIT: {url}")
                    return cache[url]
                else:
                    logger.debug(f"Pinterest cache EXPIRED: {url}")
                    del cache[url]
                    del cache_times[url]

            logger.debug(f"Pinterest cache MISS: {url}")
            result = func(url)

            cache[url] = result
            cache_times[url] = current_time

            if len(cache) > maxsize:
                oldest = min(cache_times.items(), key=lambda x: x[1])[0]
                logger.debug(f"Pinterest cache EVICT: {oldest}")
                del cache[oldest]
                del cache_times[oldest]

            return result

        wrapper.cache_clear = lambda: (cache.clear(), cache_times.clear())
        wrapper.cache_info = lambda: {
            "size": len(cache),
            "maxsize": maxsize,
            "ttl_seconds": ttl_seconds
        }

        return wrapper
    return decorator

SUPPORTED_SERVICES = {
    "youtube": {
        "name": "YouTube",
        "patterns": [
            r'https?://(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/|embed/|v/|live/|clip/)?([a-zA-Z0-9_-]+)',
            r'https?://youtu\.be/([a-zA-Z0-9_-]+)',
        ],
        "formats": ["MP4", "MP3", "PNG"]
    },
    "pinterest": {
        "name": "Pinterest",
        "patterns": [
            r'https?://(?:[a-z]{2,3}\.)?pinterest\.(?:com|ru|co\.uk|fr|de|it|es|ca|com\.au)/pin/[0-9]+',
            r'https?://pin\.it/[a-zA-Z0-9]+',
            r'https?://(?:[a-z]{2,3}\.)?pinterest\.(?:com|ru)/.+',
        ],
        "formats": ["MP4", "MP3", "PNG"]
    },
    "tiktok": {
        "name": "TikTok",
        "patterns": [
            r'https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/.+',
        ],
        "formats": ["MP4", "MP3", "PNG"]
    },
    "vk": {
        "name": "VK",
        "patterns": [
            r'https?://(?:www\.|m\.)?vk\.com/(?:video|clip|wall|feed).+',
            r'https?://(?:www\.|m\.)?vkvideo\.ru/video.+',
            r'https?://(?:www\.|m\.)?vk\.com/[a-zA-Z0-9_.]+',
        ],
        "formats": ["MP4", "MP3", "PNG"]
    },
    "dzen": {
        "name": "Яндекс Дзен",
        "patterns": [
            r'https?://(?:www\.)?dzen\.ru/(?:video/watch/|shorts/|media/|embed/|a/|b/)?([a-zA-Z0-9_-]+)',
            r'https?://zen\.yandex\.ru/.+',
            r'https?://yandex\.ru/video/.+',
        ],
        "formats": ["MP4", "MP3", "PNG"]
    }
}


def get_ytdlp_cmd() -> list[str]:
    which_path = shutil.which("yt-dlp")
    if which_path:
        return [which_path]

    for p in (
        "/usr/bin/yt-dlp",
        "/usr/local/bin/yt-dlp",
        "/opt/render/project/src/.venv/bin/yt-dlp",
        os.path.expanduser("~/.local/bin/yt-dlp"),
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return [p]

    return [sys.executable, "-m", "yt_dlp"]


def get_common_ytdlp_args(is_youtube: bool = False) -> list[str]:
    args = [
        "--no-check-certificates",
        "--geo-bypass",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "--no-warnings",
        "--max-filesize", "48M",
    ]

    # Try to use cookies if available (check common locations)
    cookie_paths = [
        "/tmp/yt-dlp-cookies.txt",
        os.path.expanduser("~/.config/yt-dlp/cookies.txt"),
        os.path.expanduser("~/.yt-dlp/cookies.txt"),
    ]
    cookies_found = False
    for cookie_path in cookie_paths:
        if os.path.exists(cookie_path):
            args.extend(["--cookies", cookie_path])
            cookies_found = True
            logger.info(f"Using cookies from {cookie_path}")
            break

    if is_youtube:
        if cookies_found:
            # When cookies are available, use more permissive client
            args.extend([
                "--extractor-args", "youtube:player_client=web,android;player_skip=configs",
            ])
        else:
            # Without cookies, use android/ios clients that don't require auth
            args.extend([
                "--extractor-args", "youtube:player_client=android,ios,mweb;player_skip=webpage,configs",
            ])
        args.extend([
            "--extractor-retries", "3",
        ])

    return args


def detect_service(url: str) -> tuple[str | None, str | None]:
    clean_url = url.strip()
    for s_key, s_info in SUPPORTED_SERVICES.items():
        for pat in s_info["patterns"]:
            if re.search(pat, clean_url, re.IGNORECASE):
                return s_key, s_info["name"]
    return None, None


def extract_first_url(text: str) -> str | None:
    match = re.search(r'https?://[^\s<>"]+', text)
    return match.group(0) if match else None


@ttl_lru_cache(ttl_seconds=300, maxsize=128)
def _get_pinterest_media(url: str) -> dict:
    try:
        import gzip
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            if resp.info().get("Content-Encoding") == "gzip":
                raw_data = gzip.decompress(resp.read())
            else:
                raw_data = resp.read()
            html = raw_data.decode("utf-8", errors="replace")

        videos = re.findall(r'https://v1\.pinimg\.com/videos/[a-zA-Z0-9._/\-]+\.(?:mp4|m3u8)', html)
        mp4_videos = [v for v in videos if v.endswith('.mp4')]
        video_url = mp4_videos[0] if mp4_videos else (videos[0] if videos else None)

        images = re.findall(r'https://i\.pinimg\.com/(?:originals|[0-9]+x)/[a-zA-Z0-9/_.\-]+\.(?:jpg|png|webp)', html)
        orig_images = [img for img in images if '/originals/' in img]
        image_url = orig_images[0] if orig_images else (images[0] if images else None)

        title_m = re.search(r'<title>([^<]+)</title>', html)
        title = title_m.group(1).replace(' | Pinterest', '').strip() if title_m else "Pinterest Pin"

        return {
            "video_url": video_url,
            "image_url": image_url,
            "title": title
        }
    except Exception as e:
        logger.warning(f"Pinterest scrape error: {e}")
        return {
            "video_url": None,
            "image_url": None,
            "title": "Pinterest Pin"
        }


def get_url_metadata(url: str) -> dict:
    is_yt = "youtube" in url or "youtu.be" in url

    if "pinterest" in url or "pin.it" in url:
        p_media = _get_pinterest_media(url)
        return {
            "title": p_media.get("title") or "Pinterest Pin",
            "thumbnail": p_media.get("image_url"),
            "duration": None,
            "uploader": "Pinterest",
            "is_live": False
        }

    cmd = get_ytdlp_cmd() + get_common_ytdlp_args(is_youtube=is_yt) + [
        "--dump-json",
        "--no-playlist",
        "--ignore-errors",
        url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            info = json.loads(res.stdout)
            return {
                "title": info.get("title", "Медиафайл"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader") or info.get("channel"),
                "is_live": info.get("is_live", False),
            }
    except Exception as e:
        logger.warning(f"Failed to get metadata with yt-dlp: {e}")

    return {
        "title": "Медиафайл",
        "thumbnail": None,
        "duration": None,
        "uploader": None,
        "is_live": False
    }


def _build_ytdlp_audio_cmd(url: str, output_template: str, is_youtube: bool) -> list[str]:
    base = get_ytdlp_cmd() + get_common_ytdlp_args(is_youtube=is_youtube)
    format_arg = ["-f", "ba/b/best"] if is_youtube else ["-f", "bestaudio/best"]
    return base + format_arg + [
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-playlist",
        "-o", output_template,
        url
    ]


def _build_ytdlp_video_cmd(url: str, output_template: str, is_youtube: bool) -> list[str]:
    base = get_ytdlp_cmd() + get_common_ytdlp_args(is_youtube=is_youtube)

    if is_youtube:
        format_spec = "18/22/b[height<=720]/best[height<=720][ext=mp4]/b/best"
    else:
        format_spec = "bestvideo[height<=720]+bestaudio/best[height<=720]/best/b"

    return base + [
        "-f", format_spec,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", output_template,
        url
    ]


def _build_ytdlp_fallback_cmd(url: str, output_template: str, is_youtube: bool, media_type: str) -> list[str]:
    base_cmd = get_ytdlp_cmd() + [
        "--no-check-certificates",
        "--geo-bypass",
        "--user-agent", "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
        "--no-playlist",
        "-o", output_template,
    ]

    if media_type == "audio":
        if is_youtube:
            return base_cmd + [
                "--extractor-args", "youtube:player_client=ios,mweb;player_skip=webpage,js,configs",
                "-f", "ba/worst",
                "--extract-audio",
                "--audio-format", "mp3",
                url
            ]
    elif media_type == "video":
        if is_youtube:
            # Try multiple fallback strategies
            return base_cmd + [
                "--extractor-args", "youtube:player_client=ios,tv_embedded;player_skip=webpage,js,configs",
                "-f", "best[height<=480]/worst",
                url
            ]
        else:
            return base_cmd + [
                "-f", "worst/best",
                url
            ]
    return []


def download_url_to_file(url: str, target_format: str, output_dir: str) -> tuple[str, str, str, int]:
    fmt = target_format.upper().strip()
    target_format = fmt
    is_yt = "youtube" in url or "youtu.be" in url
    is_pin = "pinterest" in url or "pin.it" in url

    if is_pin:
        p_media = _get_pinterest_media(url)
        video_url = p_media.get("video_url")
        image_url = p_media.get("image_url")
        raw_title = p_media.get("title") or "pinterest"
        safe_title = re.sub(r'[\\/*?:"<>|]', '', raw_title)[:40].strip() or "pinterest"

        if target_format == "PNG":
            img_target = image_url or video_url
            if not img_target:
                raise RuntimeError("Не удалось найти изображение в данном пине.")
            out_png = os.path.join(output_dir, f"{safe_title}.png")
            req = urllib.request.Request(img_target, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with Image.open(resp) as img:
                    img.save(out_png, format="PNG")
            return out_png, "png", f"{safe_title}.png", os.path.getsize(out_png)

        elif target_format == "MP4":
            if video_url:
                out_mp4 = os.path.join(output_dir, f"{safe_title}.mp4")
                req = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(out_mp4, "wb") as f:
                    shutil.copyfileobj(resp, f)
                return out_mp4, "mp4", f"{safe_title}.mp4", os.path.getsize(out_mp4)
            elif image_url:
                img_temp = os.path.join(output_dir, "temp_img.jpg")
                out_mp4 = os.path.join(output_dir, f"{safe_title}.mp4")
                req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp, open(img_temp, "wb") as f:
                    shutil.copyfileobj(resp, f)
                subprocess.run([
                    "ffmpeg", "-y", "-loop", "1", "-i", img_temp,
                    "-threads", "2",
                    "-c:v", "libx264", "-t", "3", "-pix_fmt", "yuv420p",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-preset", "veryfast",
                    "-movflags", "+faststart",
                    out_mp4
                ], capture_output=True, timeout=30, check=False)
                if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
                    return out_mp4, "mp4", f"{safe_title}.mp4", os.path.getsize(out_mp4)
                raise RuntimeError("В этом пине только статическое фото. Выберите формат PNG.")
            else:
                raise RuntimeError("Не удалось извлечь медиафайл из Pinterest.")

        elif target_format == "MP3":
            if video_url:
                temp_vid = os.path.join(output_dir, "temp_vid.mp4")
                out_mp3 = os.path.join(output_dir, f"{safe_title}.mp3")
                req = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(temp_vid, "wb") as f:
                    shutil.copyfileobj(resp, f)
                subprocess.run([
                    "ffmpeg", "-y", "-threads", "2", "-i", temp_vid,
                    "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
                    out_mp3
                ], capture_output=True, timeout=30, check=False)
                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0:
                    return out_mp3, "mp3", f"{safe_title}.mp3", os.path.getsize(out_mp3)
            raise RuntimeError("В этом пине нет аудиодорожки.")

    ytdlp_base = get_ytdlp_cmd() + get_common_ytdlp_args(is_youtube=is_yt)

    if target_format == "PNG":
        out_png = os.path.join(output_dir, "output.png")

        thumb_template = os.path.join(output_dir, "thumb.%(ext)s")
        cmd_thumb = ytdlp_base + [
            "--write-thumbnail",
            "--skip-download",
            "--no-playlist",
            "-o", thumb_template,
            url
        ]
        subprocess.run(cmd_thumb, capture_output=True, timeout=25)

        thumb_files = [os.path.join(output_dir, f) for f in os.listdir(output_dir) if os.path.isfile(os.path.join(output_dir, f)) and f.startswith("thumb")]
        if thumb_files:
            try:
                with Image.open(thumb_files[0]) as img:
                    img.save(out_png, format="PNG")
                meta = get_url_metadata(url)
                safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "image"))[:40].strip() or "image"
                return out_png, "png", f"{safe_title}.png", os.path.getsize(out_png)
            except Exception:
                pass

        meta = get_url_metadata(url)
        if meta.get("thumbnail"):
            req = urllib.request.Request(
                meta["thumbnail"],
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                with Image.open(resp) as img:
                    img.save(out_png, format="PNG")
            safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "image"))[:40].strip() or "image"
            return out_png, "png", f"{safe_title}.png", os.path.getsize(out_png)

        raise RuntimeError("Не удалось извлечь изображение по ссылке")

    elif target_format == "MP3":
        # Try Invidious first for YouTube
        if is_yt:
            video_id = _extract_youtube_video_id(url)
            if video_id:
                inv_data = _fetch_invidious_video_info(video_id)
                if inv_data and inv_data.get("adaptiveFormats"):
                    # Find best audio format
                    audio_formats = [f for f in inv_data["adaptiveFormats"] if f.get("type", "").startswith("audio/")]
                    if audio_formats:
                        best_audio = max(audio_formats, key=lambda x: x.get("bitrate", 0))
                        audio_url = best_audio.get("url")

                        if audio_url:
                            try:
                                temp_audio = os.path.join(output_dir, "temp_audio.m4a")
                                out_mp3 = os.path.join(output_dir, "audio.mp3")

                                # Download audio
                                req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
                                with urllib.request.urlopen(req, timeout=60) as resp, open(temp_audio, "wb") as f:
                                    shutil.copyfileobj(resp, f)

                                # Convert to MP3
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", temp_audio,
                                    "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
                                    out_mp3
                                ], capture_output=True, timeout=60, check=False)

                                if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0:
                                    safe_title = re.sub(r'[\\/*?:"<>|]', '', inv_data.get("title", "audio"))[:40].strip() or "audio"
                                    logger.info(f"✓ Downloaded YouTube audio via Invidious: {safe_title}")
                                    return out_mp3, "mp3", f"{safe_title}.mp3", os.path.getsize(out_mp3)
                            except Exception as e:
                                logger.warning(f"Invidious audio download failed: {e}")

        # Fallback to yt-dlp
        out_template = os.path.join(output_dir, "audio.%(ext)s")
        cmd_audio = _build_ytdlp_audio_cmd(url, out_template, is_yt)
        res = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=90)

        if res.returncode != 0 and is_yt:
            cmd_fallback = _build_ytdlp_fallback_cmd(url, out_template, is_yt, "audio")
            res = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=90)

        for f in os.listdir(output_dir):
            if f.endswith(".mp3"):
                full_p = os.path.join(output_dir, f)
                meta = get_url_metadata(url)
                safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "audio"))[:40].strip() or "audio"
                return full_p, "mp3", f"{safe_title}.mp3", os.path.getsize(full_p)

        error_msg = res.stderr.strip() if res.stderr else "Неизвестная ошибка"
        raise RuntimeError(f"Не удалось скачать аудио: {error_msg[-250:]}")

    else:
        # Try Invidious first for YouTube video
        if is_yt:
            video_id = _extract_youtube_video_id(url)
            if video_id:
                inv_data = _fetch_invidious_video_info(video_id)
                if inv_data and inv_data.get("adaptiveFormats"):
                    # Find best video format <= 720p
                    video_formats = [f for f in inv_data["adaptiveFormats"]
                                   if f.get("type", "").startswith("video/")
                                   and f.get("qualityLabel")
                                   and "720" in f.get("qualityLabel", "")]

                    if not video_formats:
                        # Fallback to any video format
                        video_formats = [f for f in inv_data["adaptiveFormats"] if f.get("type", "").startswith("video/")]

                    # Get best audio
                    audio_formats = [f for f in inv_data["adaptiveFormats"] if f.get("type", "").startswith("audio/")]

                    if video_formats and audio_formats:
                        best_video = max(video_formats, key=lambda x: x.get("bitrate", 0))
                        best_audio = max(audio_formats, key=lambda x: x.get("bitrate", 0))

                        video_url = best_video.get("url")
                        audio_url = best_audio.get("url")

                        if video_url and audio_url:
                            try:
                                temp_video = os.path.join(output_dir, "temp_video.mp4")
                                temp_audio = os.path.join(output_dir, "temp_audio.m4a")
                                final_mp4 = os.path.join(output_dir, "final.mp4")

                                # Download video and audio
                                req_v = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
                                with urllib.request.urlopen(req_v, timeout=90) as resp, open(temp_video, "wb") as f:
                                    shutil.copyfileobj(resp, f)

                                req_a = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
                                with urllib.request.urlopen(req_a, timeout=60) as resp, open(temp_audio, "wb") as f:
                                    shutil.copyfileobj(resp, f)

                                # Merge video and audio
                                subprocess.run([
                                    "ffmpeg", "-y", "-i", temp_video, "-i", temp_audio,
                                    "-c", "copy",
                                    "-movflags", "+faststart",
                                    final_mp4
                                ], capture_output=True, timeout=60, check=False)

                                if os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 0:
                                    safe_title = re.sub(r'[\\/*?:"<>|]', '', inv_data.get("title", "video"))[:40].strip() or "video"
                                    logger.info(f"✓ Downloaded YouTube video via Invidious: {safe_title}")
                                    return final_mp4, "mp4", f"{safe_title}.mp4", os.path.getsize(final_mp4)
                            except Exception as e:
                                logger.warning(f"Invidious video download failed: {e}")

        # Fallback to yt-dlp
        out_template = os.path.join(output_dir, "video.%(ext)s")
        cmd_video = _build_ytdlp_video_cmd(url, out_template, is_yt)
        res = subprocess.run(cmd_video, capture_output=True, text=True, timeout=120)

        if res.returncode != 0:
            cmd_fallback = _build_ytdlp_fallback_cmd(url, out_template, is_yt, "video")
            res = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=120)

        raw_video_path = None
        for f in os.listdir(output_dir):
            if f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm") or f.endswith(".ts"):
                raw_video_path = os.path.join(output_dir, f)
                break

        if not raw_video_path:
            error_msg = res.stderr.strip() if res.stderr else "Неизвестная ошибка"
            raise RuntimeError(f"Не удалось скачать видео: {error_msg[-250:]}")

        final_mp4 = os.path.join(output_dir, "final.mp4")

        cp_res = subprocess.run([
            "ffmpeg", "-y", "-i", raw_video_path,
            "-c", "copy",
            "-movflags", "+faststart",
            final_mp4
        ], capture_output=True, timeout=30, check=False)

        if cp_res.returncode != 0 or not os.path.exists(final_mp4) or os.path.getsize(final_mp4) == 0:
            subprocess.run([
                "ffmpeg", "-y", "-i", raw_video_path,
                "-threads", "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-vf", "scale='min(1280,iw)':-2",
                "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart",
                final_mp4
            ], capture_output=True, timeout=120, check=False)

        result_path = final_mp4 if (os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 0) else raw_video_path
        meta = get_url_metadata(url)
        safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "video"))[:40].strip() or "video"
        return result_path, "mp4", f"{safe_title}.mp4", os.path.getsize(result_path)


def download_url_media(url: str, target_format: str) -> tuple[bytes, str, str]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path, ext, title, _ = download_url_to_file(url, target_format, tmp_dir)
        with open(path, "rb") as f:
            data = f.read()
        return data, ext, title
