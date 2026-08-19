import asyncio
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
from url_converter import get_ytdlp_cmd, get_common_ytdlp_args

logger = logging.getLogger(__name__)


async def recognize_audio_track(file_path: str) -> dict | None:
    """
    Recognizes music from a local audio/video file using Shazam.
    Returns dict with song details or None if not recognized.
    """
    try:
        from shazamio import Shazam
        shazam = Shazam()
        out = await shazam.recognize(file_path)

        track = out.get("track")
        if not track:
            return None

        title = track.get("title", "Неизвестный трек")
        artist = track.get("subtitle", "Неизвестный исполнитель")
        images = track.get("images", {})
        cover_url = images.get("coverarthq") or images.get("coverart")

        # Extract extra links / metadata
        shazam_url = track.get("url")
        genres = track.get("genres", {}).get("primary")
        
        album = None
        for sec in track.get("sections", []):
            if sec.get("type") == "SONG":
                for meta in sec.get("metadata", []):
                    if meta.get("title") == "Album":
                        album = meta.get("text")
                        break

        # Apple music link
        apple_music_url = None
        hub = track.get("hub", {})
        for opt in hub.get("options", []):
            if opt.get("caption") == "OPEN IN APPLE MUSIC":
                for action in opt.get("actions", []):
                    if action.get("uri"):
                        apple_music_url = action.get("uri")
                        break

        # Preview audio URL
        preview_url = None
        for act in hub.get("actions", []):
            if act.get("type") == "uri" and act.get("uri") and act.get("uri").endswith(".mp3"):
                preview_url = act.get("uri")
                break

        return {
            "title": title,
            "artist": artist,
            "album": album,
            "genres": genres,
            "cover_url": cover_url,
            "shazam_url": shazam_url,
            "apple_music_url": apple_music_url,
            "preview_url": preview_url,
        }
    except Exception as e:
        logger.warning(f"Shazam recognition error: {e}")
        return None


def extract_tiktok_audio_snippet(url: str, output_path: str) -> bool:
    """
    Extracts up to 15 seconds of audio from TikTok video to output_path.
    """
    ytdlp_cmd = get_ytdlp_cmd() + [
        "--no-check-certificates",
        "--geo-bypass",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--no-warnings",
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--download-sections", "*00:00:00-00:00:15",
        "--no-playlist",
        "-o", output_path,
        url
    ]
    res = subprocess.run(ytdlp_cmd, capture_output=True, text=True, timeout=60)
    if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return True

    # Fallback without --download-sections (full audio)
    fallback_cmd = get_ytdlp_cmd() + [
        "--no-check-certificates",
        "--geo-bypass",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "--no-warnings",
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--no-playlist",
        "-o", output_path,
        url
    ]
    res = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=60)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


async def shazam_tiktok_url(url: str) -> dict | None:
    """
    Downloads audio from a TikTok link and identifies the song via Shazam.
    Returns dict with song information or None.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_file = os.path.join(tmp_dir, "tiktok_audio.mp3")
        success = await asyncio.to_thread(extract_tiktok_audio_snippet, url, audio_file)
        if not success:
            logger.warning(f"Could not extract audio from TikTok url: {url}")
            return None

        return await recognize_audio_track(audio_file)
