import asyncio
import io
import json
import logging
import os
import re
import struct
import subprocess
import tempfile
import urllib.request
import uuid
from base64 import b64encode
from binascii import crc32
from ctypes import LittleEndianStructure, c_uint32
from enum import IntEnum
from math import exp, sqrt
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_URI_PREFIX = "data:audio/vnd.shazam.sig;base64,"

# Ленивая загрузка numpy для экономии памяти (~100 МБ)
_numpy_module = None
_hanning_matrix = None


def _get_numpy():
    """Ленивая загрузка numpy - загружается только при первом использовании Shazam."""
    global _numpy_module
    if _numpy_module is None:
        import numpy as np
        _numpy_module = np
    return _numpy_module


def _get_hanning_matrix():
    """Ленивая загрузка HANNING_MATRIX - создается только при первом использовании."""
    global _hanning_matrix
    if _hanning_matrix is None:
        np = _get_numpy()
        _hanning_matrix = np.hanning(2050)[1:-1]
    return _hanning_matrix


class FrequencyBand(IntEnum):
    hz_250_520 = 0
    hz_520_1450 = 1
    hz_1450_3500 = 2
    hz_3500_5500 = 3


class RawSignatureHeader(LittleEndianStructure):
    _pack_ = True
    _fields_ = [
        ("magic1", c_uint32),
        ("crc32", c_uint32),
        ("size_minus_header", c_uint32),
        ("magic2", c_uint32),
        ("void1", c_uint32 * 3),
        ("shifted_sample_rate_id", c_uint32),
        ("void2", c_uint32 * 2),
        ("number_samples_plus_divided_sample_rate", c_uint32),
        ("fixed_value", c_uint32),
    ]


class FrequencyPeak:
    def __init__(
        self,
        fft_pass_number: int,
        peak_magnitude: int,
        corrected_peak_frequency_bin: int,
        sample_rate_hz: int = 16000,
    ):
        self.fft_pass_number = fft_pass_number
        self.peak_magnitude = peak_magnitude
        self.corrected_peak_frequency_bin = corrected_peak_frequency_bin
        self.sample_rate_hz = sample_rate_hz


class DecodedMessage:
    def __init__(self):
        self.sample_rate_hz = 16000
        self.number_samples = 0
        self.frequency_band_to_sound_peaks: Dict[FrequencyBand, List[FrequencyPeak]] = {}

    def encode_to_binary(self) -> bytes:
        header = RawSignatureHeader()
        header.magic1 = 0xCAFE2580
        header.magic2 = 0x94119C00
        header.shifted_sample_rate_id = 3 << 27
        header.fixed_value = (15 << 19) + 0x40000
        header.number_samples_plus_divided_sample_rate = int(
            self.number_samples + self.sample_rate_hz * 0.24
        )

        contents_buf = io.BytesIO()
        for frequency_band, frequency_peaks in sorted(self.frequency_band_to_sound_peaks.items()):
            peaks_buf = io.BytesIO()
            fft_pass_number = 0

            for frequency_peak in sorted(frequency_peaks, key=lambda p: p.fft_pass_number):
                if frequency_peak.fft_pass_number - fft_pass_number >= 255:
                    peaks_buf.write(b"\xff")
                    peaks_buf.write(frequency_peak.fft_pass_number.to_bytes(4, "little"))
                    fft_pass_number = frequency_peak.fft_pass_number

                peaks_buf.write(bytes([frequency_peak.fft_pass_number - fft_pass_number]))
                peaks_buf.write(frequency_peak.peak_magnitude.to_bytes(2, "little"))
                peaks_buf.write(frequency_peak.corrected_peak_frequency_bin.to_bytes(2, "little"))
                fft_pass_number = frequency_peak.fft_pass_number

            contents_buf.write((0x60030040 + int(frequency_band)).to_bytes(4, "little"))
            contents_buf.write(len(peaks_buf.getvalue()).to_bytes(4, "little"))
            contents_buf.write(peaks_buf.getvalue())
            contents_buf.write(b"\x00" * (-len(peaks_buf.getvalue()) % 4))

        header.size_minus_header = len(contents_buf.getvalue()) + 8
        buf = io.BytesIO()
        buf.write(header)
        buf.write((0x40000000).to_bytes(4, "little"))
        buf.write((len(contents_buf.getvalue()) + 8).to_bytes(4, "little"))
        buf.write(contents_buf.getvalue())

        buf.seek(8)
        header.crc32 = crc32(buf.read()) & 0xFFFFFFFF
        buf.seek(0)
        buf.write(header)
        return buf.getvalue()

    def encode_to_uri(self) -> str:
        return DATA_URI_PREFIX + b64encode(self.encode_to_binary()).decode("ascii")


class RingBuffer(list):
    """
    Кольцевой буфер с оптимизированным хранением.
    Использует array.array для numpy массивов для экономии памяти (~50% меньше).
    """
    def __init__(self, buffer_size: int, default_value=0):
        # Проверяем тип default_value
        if hasattr(default_value, '__len__') and hasattr(default_value, 'dtype'):
            # Это numpy массив - импортируем array для оптимизации
            from array import array
            # Конвертируем numpy массив в list array для каждого элемента
            # array('d') использует double (8 bytes) для совместимости с numpy float64
            super().__init__([array('d', default_value.tolist()) if hasattr(default_value, 'tolist') else default_value for _ in range(buffer_size)])
        elif hasattr(default_value, '__iter__') and not isinstance(default_value, str):
            # Если это список или другой итерируемый объект
            from array import array
            super().__init__([array('d', default_value) for _ in range(buffer_size)])
        else:
            # Для скаляров - как раньше
            super().__init__([default_value] * buffer_size)
        self.position: int = 0
        self.buffer_size: int = buffer_size
        self.num_written: int = 0

    def append(self, value):
        """Добавляет значение в буфер с поддержкой numpy массивов."""
        if hasattr(value, 'tolist'):
            # Конвертируем numpy массив в array.array для экономии памяти
            from array import array
            self[self.position] = array('d', value.tolist())
        else:
            self[self.position] = value
        self.position = (self.position + 1) % self.buffer_size
        self.num_written += 1

    def __getitem__(self, idx):
        """Получает элемент с автоконвертацией array.array обратно в numpy."""
        item = super().__getitem__(idx)
        # Если это array.array, конвертируем обратно в numpy для операций
        if hasattr(item, 'typecode') and item.typecode == 'd':
            np = _get_numpy()
            return np.array(item)
        return item


class SignatureGenerator:
    def __init__(self):
        self.ring_buffer_of_samples = RingBuffer(buffer_size=2048, default_value=0)
        # Ленивая инициализация numpy массивов - создаются при первом использовании
        np = _get_numpy()
        self.fft_outputs = RingBuffer(buffer_size=256, default_value=np.zeros(1025))
        self.spread_fft_output = RingBuffer(buffer_size=256, default_value=np.zeros(1025))
        self.signature = DecodedMessage()

    def feed_samples(self, samples: List[int]) -> None:
        self.signature.number_samples += len(samples)
        for i in range(0, len(samples), 128):
            batch = samples[i : i + 128]
            if len(batch) < 128:
                batch = batch + [0] * (128 - len(batch))
            self._do_fft(batch)
            self._do_peak_spreading()
            if self.spread_fft_output.num_written >= 46:
                self._do_peak_recognition()

    def _do_fft(self, batch_128: List[int]) -> None:
        np = _get_numpy()
        HANNING_MATRIX = _get_hanning_matrix()

        pos = self.ring_buffer_of_samples.position
        for idx, val in enumerate(batch_128):
            self.ring_buffer_of_samples[(pos + idx) % 2048] = val
        self.ring_buffer_of_samples.position = (pos + 128) % 2048
        self.ring_buffer_of_samples.num_written += 128

        curr_pos = self.ring_buffer_of_samples.position
        excerpt = np.array(
            self.ring_buffer_of_samples[curr_pos:] + self.ring_buffer_of_samples[:curr_pos],
            dtype=np.float64,
        )
        fft_res = np.fft.rfft(HANNING_MATRIX * excerpt)
        power = (fft_res.real**2 + fft_res.imag**2) / (1 << 17)
        power = np.maximum(power, 1e-10)
        self.fft_outputs.append(power)

    def _do_peak_spreading(self) -> None:
        np = _get_numpy()

        last_fft = self.fft_outputs[(self.fft_outputs.position - 1) % self.fft_outputs.buffer_size]
        tile = np.tile(last_fft, 3).reshape((3, -1))
        tile[1] = np.roll(tile[1], -1)
        tile[2] = np.roll(tile[2], -2)
        spread = np.hstack([tile.max(axis=0)[:-3], last_fft[-3:]])

        i1 = (self.spread_fft_output.position - 1) % self.spread_fft_output.buffer_size
        i2 = (self.spread_fft_output.position - 3) % self.spread_fft_output.buffer_size
        i3 = (self.spread_fft_output.position - 6) % self.spread_fft_output.buffer_size

        arr = np.vstack([spread, self.spread_fft_output[i1], self.spread_fft_output[i2], self.spread_fft_output[i3]])
        arr[1] = np.max(arr[:2], axis=0)
        arr[2] = np.max(arr[:3], axis=0)
        arr[3] = np.max(arr[:4], axis=0)

        self.spread_fft_output[i1] = arr[1]
        self.spread_fft_output[i2] = arr[2]
        self.spread_fft_output[i3] = arr[3]
        self.spread_fft_output.append(spread)

    def _do_peak_recognition(self) -> None:
        np = _get_numpy()

        fft_m46 = self.fft_outputs[(self.fft_outputs.position - 46) % self.fft_outputs.buffer_size]
        fft_m49 = self.spread_fft_output[(self.spread_fft_output.position - 49) % self.spread_fft_output.buffer_size]

        for bin_pos in range(10, 1015):
            if fft_m46[bin_pos] >= 1 / 64 and fft_m46[bin_pos] >= fft_m49[bin_pos - 1]:
                max_neighbor = 0
                for off in [-10, -7, -4, -3, 1, 2, 5, 8]:
                    idx = bin_pos + off
                    if 0 <= idx < len(fft_m49):
                        max_neighbor = max(max_neighbor, fft_m49[idx])

                if fft_m46[bin_pos] > max_neighbor:
                    max_adj = max_neighbor
                    for off in [-53, -45]:
                        row = self.spread_fft_output[(self.spread_fft_output.position + off) % self.spread_fft_output.buffer_size]
                        if bin_pos - 1 < len(row):
                            max_adj = max(max_adj, row[bin_pos - 1])

                    if fft_m46[bin_pos] > max_adj:
                        fft_num = self.spread_fft_output.num_written - 46
                        p_mag = np.log(max(1 / 64, fft_m46[bin_pos])) * 1477.3 + 6144
                        p_bef = np.log(max(1 / 64, fft_m46[bin_pos - 1])) * 1477.3 + 6144
                        p_aft = np.log(max(1 / 64, fft_m46[bin_pos + 1])) * 1477.3 + 6144

                        var1 = p_mag * 2 - p_bef - p_aft
                        if var1 <= 0:
                            continue
                        var2 = (p_aft - p_bef) * 32 / var1
                        corrected_bin = bin_pos * 64 + var2
                        freq_hz = corrected_bin * (16000 / 2 / 1024 / 64)

                        # Быстрое определение частотного диапазона через границы
                        if not (250 < freq_hz <= 5500):
                            continue

                        if freq_hz <= 520:
                            band = FrequencyBand.hz_250_520
                        elif freq_hz <= 1450:
                            band = FrequencyBand.hz_520_1450
                        elif freq_hz <= 3500:
                            band = FrequencyBand.hz_1450_3500
                        else:
                            band = FrequencyBand.hz_3500_5500

                        if band not in self.signature.frequency_band_to_sound_peaks:
                            self.signature.frequency_band_to_sound_peaks[band] = []

                        self.signature.frequency_band_to_sound_peaks[band].append(
                            FrequencyPeak(
                                fft_pass_number=fft_num,
                                peak_magnitude=int(p_mag),
                                corrected_peak_frequency_bin=int(corrected_bin),
                                sample_rate_hz=16000,
                            )
                        )


def create_signature_from_pcm(raw_pcm_s16le: bytes) -> str:
    """Generates Shazam Data-URI signature from raw 16kHz 16-bit mono PCM bytes."""
    num_samples = len(raw_pcm_s16le) // 2
    samples = struct.unpack(f"<{num_samples}h", raw_pcm_s16le[: num_samples * 2])

    gen = SignatureGenerator()
    gen.feed_samples(list(samples))
    return gen.signature.encode_to_uri()


def query_shazam_api(signature_uri: str) -> dict | None:
    """Sends signature to Shazam API and returns parsed response."""
    device_id = str(uuid.uuid4()).upper()
    tag_id = str(uuid.uuid4()).upper()
    api_url = f"https://amp.shazam.com/discovery/v5/ru/RU/android/-/tag/{device_id}/{tag_id}?sync=true"

    payload = {
        "signatures": [
            {
                "samplems": 3100,
                "timestamp": 0,
                "uri": signature_uri,
            }
        ],
        "timezone": "Europe/Moscow",
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": "Shazam/13.15.0-230209 (Linux; Android 11; SM-G973F) gzip",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                return None
            body = resp.read().decode("utf-8")
            res_json = json.loads(body)
            track = res_json.get("track")
            if not track:
                return None

            title = track.get("title", "Неизвестный трек")
            artist = track.get("subtitle", "Неизвестный исполнитель")
            images = track.get("images", {})
            cover_url = images.get("coverarthq") or images.get("coverart")
            shazam_url = track.get("url")
            genres = track.get("genres", {}).get("primary")

            album = None
            for sec in track.get("sections", []):
                if sec.get("type") == "SONG":
                    for meta in sec.get("metadata", []):
                        if meta.get("title") == "Album":
                            album = meta.get("text")
                            break

            apple_music_url = None
            hub = track.get("hub", {})
            for opt in hub.get("options", []):
                if opt.get("caption") == "OPEN IN APPLE MUSIC":
                    for act in opt.get("actions", []):
                        if act.get("uri"):
                            apple_music_url = act.get("uri")
                            break

            return {
                "title": title,
                "artist": artist,
                "album": album,
                "genres": genres,
                "cover_url": cover_url,
                "shazam_url": shazam_url,
                "apple_music_url": apple_music_url,
            }
    except Exception as e:
        logger.warning(f"Shazam API request failed: {e}")
        return None


def extract_audio_pcm_from_url(url: str) -> Optional[bytes]:
    """
    Downloads up to 10 seconds of audio from a URL and converts to 16kHz mono raw s16le PCM.
    """
    from url_converter import get_ytdlp_cmd

    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_audio = os.path.join(tmp_dir, "snippet.mp3")

        # 1. Download snippet using yt-dlp
        cmd = get_ytdlp_cmd() + [
            "--no-check-certificates",
            "--geo-bypass",
            "--user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "--no-warnings",
            "-f", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "5",
            "--download-sections", "*00:00:00-00:00:10",
            "--no-playlist",
            "-o", temp_audio,
            url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        if not (res.returncode == 0 and os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 0):
            # Fallback without --download-sections
            fallback_cmd = get_ytdlp_cmd() + [
                "--no-check-certificates",
                "--geo-bypass",
                "--user-agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "--no-warnings",
                "-f", "bestaudio/best",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "5",
                "--no-playlist",
                "-o", temp_audio,
                url,
            ]
            res2 = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=40)
            if not (res2.returncode == 0 and os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 0):
                return None

        # 2. Convert to raw 16kHz mono 16-bit PCM using ffmpeg
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-threads", "2",
            "-i", temp_audio,
            "-t", "8",
            "-ac", "1",
            "-ar", "16000",
            "-f", "s16le",
            "-",
        ]
        ff_res = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=20, check=False)
        if ff_res.returncode == 0 and ff_res.stdout:
            return ff_res.stdout
        return None


async def shazam_tiktok_url(url: str) -> dict | None:
    """
    Downloads audio from a TikTok link and identifies the song via Shazam API.
    100% pure Python + numpy + ffmpeg, without C++/Rust build dependencies.
    """
    pcm_data = await asyncio.to_thread(extract_audio_pcm_from_url, url)
    if not pcm_data:
        return None

    signature_uri = await asyncio.to_thread(create_signature_from_pcm, pcm_data)
    return await asyncio.to_thread(query_shazam_api, signature_uri)
