import io
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from PIL import Image

logger = logging.getLogger(__name__)

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


def detect_service(url: str) -> tuple[str | None, str | None]:
    """
    Detects if the URL belongs to a supported service.
    Returns (service_key, service_display_name).
    """
    clean_url = url.strip()
    for s_key, s_info in SUPPORTED_SERVICES.items():
        for pat in s_info["patterns"]:
            if re.search(pat, clean_url, re.IGNORECASE):
                return s_key, s_info["name"]
    return None, None


def extract_first_url(text: str) -> str | None:
    """Extracts first HTTP/HTTPS link from message text."""
    match = re.search(r'https?://[^\s<>"]+', text)
    return match.group(0) if match else None


def _get_pinterest_image_url(url: str) -> tuple[str | None, str | None]:
    """Directly extracts high-res image URL and title from Pinterest webpage."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            final_url = resp.geturl()
            html_content = resp.read().decode("utf-8", errors="replace")

        # 1. Search og:image
        og_img_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html_content)
        title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html_content)

        img_url = og_img_match.group(1) if og_img_match else None
        title = title_match.group(1) if title_match else "Pinterest"

        # Upgrade Pinterest thumbnail URL to originals if possible
        if img_url:
            img_url = re.sub(r'/(?:236x|474x|736x)/', '/originals/', img_url)

        return img_url, title
    except Exception as e:
        logger.warning(f"Pinterest image scrape failed: {e}")
        return None, None


def get_url_metadata(url: str) -> dict:
    """
    Extracts title, duration, thumbnail from the URL using yt-dlp with fallback.
    """
    cmd = [
        "/usr/bin/yt-dlp",
        "--dump-json",
        "--no-playlist",
        "--no-warnings",
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

    # Fallback for Pinterest
    if "pinterest" in url or "pin.it" in url:
        p_img, p_title = _get_pinterest_image_url(url)
        if p_img:
            return {
                "title": p_title or "Pinterest Pin",
                "thumbnail": p_img,
                "duration": None,
                "uploader": "Pinterest",
                "is_live": False
            }

    return {
        "title": "Медиафайл",
        "thumbnail": None,
        "duration": None,
        "uploader": None,
        "is_live": False
    }


def download_url_media(url: str, target_format: str) -> tuple[bytes, str, str]:
    """
    Downloads media from URL and converts it to target_format (MP4, MP3, PNG).
    Returns (file_bytes, file_extension, filename_title).
    """
    fmt = target_format.upper().strip()
    target_format = fmt

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Case 1: Download PNG thumbnail or image
        if target_format == "PNG":
            # If Pinterest pin, try direct image extraction first
            if "pinterest" in url or "pin.it" in url:
                p_img, p_title = _get_pinterest_image_url(url)
                if p_img:
                    try:
                        req = urllib.request.Request(
                            p_img,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            raw_img = resp.read()
                        with Image.open(io.BytesIO(raw_img)) as img:
                            out_io = io.BytesIO()
                            img.save(out_io, format="PNG")
                            safe_title = re.sub(r'[\\/*?:"<>|]', '', p_title or "pinterest_image")[:40].strip() or "pinterest_image"
                            return out_io.getvalue(), "png", f"{safe_title}.png"
                    except Exception as e:
                        logger.warning(f"Failed to download direct Pinterest image: {e}")

            # 1. Try to download thumbnail via yt-dlp
            thumb_template = os.path.join(tmp_dir, "thumb.%(ext)s")
            cmd_thumb = [
                "/usr/bin/yt-dlp",
                "--write-thumbnail",
                "--skip-download",
                "--no-playlist",
                "-o", thumb_template,
                url
            ]
            subprocess.run(cmd_thumb, capture_output=True, timeout=25)
            
            thumb_files = [os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if os.path.isfile(os.path.join(tmp_dir, f))]
            if thumb_files:
                img_path = thumb_files[0]
                with Image.open(img_path) as img:
                    out_io = io.BytesIO()
                    img.save(out_io, format="PNG")
                    return out_io.getvalue(), "png", "image.png"

            # 2. Fallback: get metadata thumbnail URL and download directly
            meta = get_url_metadata(url)
            if meta.get("thumbnail"):
                req = urllib.request.Request(
                    meta["thumbnail"],
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw_img = resp.read()
                with Image.open(io.BytesIO(raw_img)) as img:
                    out_io = io.BytesIO()
                    img.save(out_io, format="PNG")
                    safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "image"))[:40].strip() or "image"
                    return out_io.getvalue(), "png", f"{safe_title}.png"

            raise RuntimeError("Не удалось извлечь изображение по ссылке")

        # Case 2: Download MP3 Audio
        elif target_format == "MP3":
            out_template = os.path.join(tmp_dir, "audio.%(ext)s")
            cmd_audio = [
                "/usr/bin/yt-dlp",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--no-playlist",
                "-o", out_template,
                url
            ]
            res = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=90)
            
            # Find output mp3 file
            for f in os.listdir(tmp_dir):
                if f.endswith(".mp3"):
                    full_p = os.path.join(tmp_dir, f)
                    with open(full_p, "rb") as af:
                        audio_b = af.read()
                    meta = get_url_metadata(url)
                    safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "audio"))[:40].strip() or "audio"
                    return audio_b, "mp3", f"{safe_title}.mp3"

            raise RuntimeError(f"Не удалось скачать аудио: {res.stderr[-200:] if res.stderr else 'Неизвестная ошибка'}")

        # Case 3: Download MP4 Video
        else:
            out_template = os.path.join(tmp_dir, "video.%(ext)s")
            cmd_video = [
                "/usr/bin/yt-dlp",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "-o", out_template,
                url
            ]
            res = subprocess.run(cmd_video, capture_output=True, text=True, timeout=120)

            # Find output mp4 or video file
            mp4_path = None
            for f in os.listdir(tmp_dir):
                if f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm"):
                    mp4_path = os.path.join(tmp_dir, f)
                    break

            if not mp4_path:
                raise RuntimeError(f"Не удалось скачать видео: {res.stderr[-200:] if res.stderr else 'Неизвестная ошибка'}")

            # Transcode with faststart if needed
            final_mp4 = os.path.join(tmp_dir, "final.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-i", mp4_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                final_mp4
            ], capture_output=True, timeout=120)

            result_path = final_mp4 if os.path.exists(final_mp4) else mp4_path
            with open(result_path, "rb") as vf:
                video_b = vf.read()

            meta = get_url_metadata(url)
            safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "video"))[:40].strip() or "video"
            return video_b, "mp4", f"{safe_title}.mp4"
