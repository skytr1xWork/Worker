import asyncio
import gc
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from PIL import Image

# Ensure system package path is included if running in custom venv
for p in ("/usr/lib/python3.14/site-packages", "/usr/lib/python3/dist-packages", "/usr/local/lib/python3.14/dist-packages"):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.append(p)

logger = logging.getLogger(__name__)

# Global semaphore to limit downloads to exactly 1 process at a time (protects Render free tier 512MB RAM)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(1)

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
    """Dynamically finds the best command to execute yt-dlp across all environments."""
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


def get_common_ytdlp_args() -> list[str]:
    """Returns low-memory extractor args and spoofing headers that bypass bot checks on cloud servers."""
    return [
        "--no-check-certificates",
        "--geo-bypass",
        "--extractor-args", "youtube:player_client=android;player_skip=webpage,configs",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--no-warnings",
        "--buffer-size", "16K",
        "--http-chunk-size", "5M",
        "--max-filesize", "48M",
        "--concurrent-fragments", "1",
    ]


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
            html_content = resp.read().decode("utf-8", errors="replace")

        # Search og:image and title
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
    cmd = get_ytdlp_cmd() + get_common_ytdlp_args() + [
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


def download_url_to_file(url: str, target_format: str, output_dir: str) -> tuple[str, str, str, int]:
    """
    Downloads media directly to disk with strict low-RAM memory limits.
    Returns (final_file_path, file_extension, filename_title, file_size_bytes).
    """
    fmt = target_format.upper().strip()
    target_format = fmt
    ytdlp_base = get_ytdlp_cmd() + get_common_ytdlp_args()

    # Case 1: Download PNG thumbnail or image
    if target_format == "PNG":
        out_png = os.path.join(output_dir, "output.png")

        # Pinterest direct image
        if "pinterest" in url or "pin.it" in url:
            p_img, p_title = _get_pinterest_image_url(url)
            if p_img:
                try:
                    req = urllib.request.Request(
                        p_img,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        with Image.open(resp) as img:
                            img.save(out_png, format="PNG")
                    safe_title = re.sub(r'[\\/*?:"<>|]', '', p_title or "pinterest_image")[:40].strip() or "pinterest_image"
                    return out_png, "png", f"{safe_title}.png", os.path.getsize(out_png)
                except Exception as e:
                    logger.warning(f"Failed to download direct Pinterest image: {e}")

        # yt-dlp thumbnail
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

        # Fallback from metadata thumbnail
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

    # Case 2: Download MP3 Audio
    elif target_format == "MP3":
        out_template = os.path.join(output_dir, "audio.%(ext)s")
        cmd_audio = ytdlp_base + [
            "-f", "ba/b/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "5",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        res = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=90)

        # Fallback if first attempt failed on YouTube
        if res.returncode != 0 and ("youtube" in url or "youtu.be" in url):
            cmd_fallback = get_ytdlp_cmd() + [
                "--no-check-certificates",
                "--geo-bypass",
                "--extractor-args", "youtube:player_client=ios;player_skip=webpage,configs",
                "-f", "ba/b/best",
                "--extract-audio",
                "--audio-format", "mp3",
                "--no-playlist",
                "-o", out_template,
                url
            ]
            res = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=90)

        for f in os.listdir(output_dir):
            if f.endswith(".mp3"):
                full_p = os.path.join(output_dir, f)
                meta = get_url_metadata(url)
                safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "audio"))[:40].strip() or "audio"
                return full_p, "mp3", f"{safe_title}.mp3", os.path.getsize(full_p)

        error_msg = res.stderr.strip() if res.stderr else "Неизвестная ошибка"
        raise RuntimeError(f"Не удалось скачать аудио: {error_msg[-250:]}")

    # Case 3: Download MP4 Video (constrained to 720p and max 45MB to protect RAM)
    else:
        out_template = os.path.join(output_dir, "video.%(ext)s")
        cmd_video = ytdlp_base + [
            "-f", "18/22/b[height<=720]/best[height<=720][ext=mp4]/b/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        res = subprocess.run(cmd_video, capture_output=True, text=True, timeout=120)

        # Fallback if first attempt failed on YouTube
        if res.returncode != 0 and ("youtube" in url or "youtu.be" in url):
            cmd_fallback = get_ytdlp_cmd() + [
                "--no-check-certificates",
                "--geo-bypass",
                "--extractor-args", "youtube:player_client=ios;player_skip=webpage,configs",
                "-f", "b/best",
                "--no-playlist",
                "-o", out_template,
                url
            ]
            res = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=120)

        raw_video_path = None
        for f in os.listdir(output_dir):
            if f.endswith(".mp4") or f.endswith(".mkv") or f.endswith(".webm"):
                raw_video_path = os.path.join(output_dir, f)
                break

        if not raw_video_path:
            error_msg = res.stderr.strip() if res.stderr else "Неизвестная ошибка"
            raise RuntimeError(f"Не удалось скачать видео: {error_msg[-250:]}")

        final_mp4 = os.path.join(output_dir, "final.mp4")

        # Low-RAM MP4 faststart / transcode:
        # 1. First try stream copy with faststart (takes 0 CPU, < 5MB RAM)
        cp_res = subprocess.run([
            "ffmpeg", "-y", "-i", raw_video_path,
            "-c", "copy",
            "-movflags", "+faststart",
            final_mp4
        ], capture_output=True, timeout=30)

        # 2. If stream copy failed (e.g. non-mp4 codec), do lightweight transcode with 1 thread
        if cp_res.returncode != 0 or not os.path.exists(final_mp4) or os.path.getsize(final_mp4) == 0:
            subprocess.run([
                "ffmpeg", "-y", "-i", raw_video_path,
                "-threads", "1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                "-vf", "scale='min(1280,iw)':-2",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                final_mp4
            ], capture_output=True, timeout=120)

        result_path = final_mp4 if (os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 0) else raw_video_path
        meta = get_url_metadata(url)
        safe_title = re.sub(r'[\\/*?:"<>|]', '', meta.get("title", "video"))[:40].strip() or "video"
        return result_path, "mp4", f"{safe_title}.mp4", os.path.getsize(result_path)


def download_url_media(url: str, target_format: str) -> tuple[bytes, str, str]:
    """Compatibility wrapper that reads bytes (used if needed)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path, ext, title, _ = download_url_to_file(url, target_format, tmp_dir)
        with open(path, "rb") as f:
            data = f.read()
        return data, ext, title
