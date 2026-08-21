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

# Global semaphore: allow 2 concurrent downloads with optimized memory usage
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

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


def get_common_ytdlp_args(is_youtube: bool = False) -> list[str]:
    """Returns low-memory extractor args and spoofing headers that bypass bot checks on cloud servers."""
    args = [
        "--no-check-certificates",
        "--geo-bypass",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--no-warnings",
        "--max-filesize", "48M",
    ]
    if is_youtube:
        args.extend([
            "--extractor-args", "youtube:player_client=android;player_skip=webpage,configs"
        ])
    return args


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


def _get_pinterest_media(url: str) -> dict:
    """
    Directly extracts direct video and image URLs from Pinterest pin page with gzip support.
    Returns dict with keys: video_url, image_url, title.
    """
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

        # 1. Video URLs
        videos = re.findall(r'https://v1\.pinimg\.com/videos/[a-zA-Z0-9._/\-]+\.(?:mp4|m3u8)', html)
        mp4_videos = [v for v in videos if v.endswith('.mp4')]
        video_url = mp4_videos[0] if mp4_videos else (videos[0] if videos else None)

        # 2. Image URLs
        images = re.findall(r'https://i\.pinimg\.com/(?:originals|[0-9]+x)/[a-zA-Z0-9/_.\-]+\.(?:jpg|png|webp)', html)
        orig_images = [img for img in images if '/originals/' in img]
        image_url = orig_images[0] if orig_images else (images[0] if images else None)

        # 3. Title
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
    """
    Extracts title, duration, thumbnail from the URL with fallback.
    """
    is_yt = "youtube" in url or "youtu.be" in url

    # Pinterest direct metadata
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


def download_url_to_file(url: str, target_format: str, output_dir: str) -> tuple[str, str, str, int]:
    """
    Downloads media directly to disk with strict low-RAM memory limits.
    Returns (final_file_path, file_extension, filename_title, file_size_bytes).
    """
    fmt = target_format.upper().strip()
    target_format = fmt
    is_yt = "youtube" in url or "youtu.be" in url
    is_pin = "pinterest" in url or "pin.it" in url

    # -------------------------------------------------------------
    # PINTEREST DIRECT PIPELINE (Ultra-fast, zero yt-dlp format errors)
    # -------------------------------------------------------------
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
                # User asked for MP4 on an image pin: create short 3s MP4 clip from image
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

    # -------------------------------------------------------------
    # YOUTUBE, TIKTOK, VK, DZEN PIPELINE (yt-dlp)
    # -------------------------------------------------------------
    ytdlp_base = get_ytdlp_cmd() + get_common_ytdlp_args(is_youtube=is_yt)

    # Case 1: Download PNG thumbnail or image
    if target_format == "PNG":
        out_png = os.path.join(output_dir, "output.png")

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
        format_arg = ["-f", "ba/b/best"] if is_yt else ["-f", "bestaudio/best"]
        cmd_audio = ytdlp_base + format_arg + [
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "5",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        res = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=90)

        # Fallback if first attempt failed on YouTube
        if res.returncode != 0 and is_yt:
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

    # Case 3: Download MP4 Video
    else:
        out_template = os.path.join(output_dir, "video.%(ext)s")
        # Format selector: YouTube uses progressive 18/22/b, others use bestvideo+bestaudio/best
        if is_yt:
            format_spec = "18/22/b[height<=720]/best[height<=720][ext=mp4]/b/best"
        else:
            format_spec = "bestvideo[height<=720]+bestaudio/best[height<=720]/best/b"

        cmd_video = ytdlp_base + [
            "-f", format_spec,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        res = subprocess.run(cmd_video, capture_output=True, text=True, timeout=120)

        # Fallback if first attempt failed
        if res.returncode != 0:
            if is_yt:
                cmd_fallback = get_ytdlp_cmd() + [
                    "--no-check-certificates",
                    "--geo-bypass",
                    "--extractor-args", "youtube:player_client=ios;player_skip=webpage,configs",
                    "-f", "b/best",
                    "--no-playlist",
                    "-o", out_template,
                    url
                ]
            else:
                cmd_fallback = get_ytdlp_cmd() + [
                    "--no-check-certificates",
                    "--geo-bypass",
                    "-f", "best/b",
                    "--no-playlist",
                    "-o", out_template,
                    url
                ]
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

        # Low-RAM MP4 faststart / transcode:
        # 1. First try stream copy with faststart (takes 0 CPU, < 5MB RAM)
        cp_res = subprocess.run([
            "ffmpeg", "-y", "-i", raw_video_path,
            "-c", "copy",
            "-movflags", "+faststart",
            final_mp4
        ], capture_output=True, timeout=30, check=False)

        # 2. If stream copy failed (e.g. non-mp4 codec), do lightweight transcode with 2 threads
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
    """Compatibility wrapper that reads bytes (used if needed)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path, ext, title, _ = download_url_to_file(url, target_format, tmp_dir)
        with open(path, "rb") as f:
            data = f.read()
        return data, ext, title
