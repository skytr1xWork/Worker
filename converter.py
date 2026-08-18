import io
import os
from PIL import Image

SUPPORTED_IMAGE_FORMATS = {
    "PNG": {"ext": "png", "mime": "image/png", "label": "PNG (без потерь)"},
    "JPG": {"ext": "jpg", "mime": "image/jpeg", "label": "JPG (сжатый)"},
    "WEBP": {"ext": "webp", "mime": "image/webp", "label": "WEBP (веб)"},
    "BMP": {"ext": "bmp", "mime": "image/bmp", "label": "BMP (растровый)"},
    "TIFF": {"ext": "tiff", "mime": "image/tiff", "label": "TIFF (высокое качество)"},
    "ICO": {"ext": "ico", "mime": "image/x-icon", "label": "ICO (иконка)"},
    "PDF": {"ext": "pdf", "mime": "application/pdf", "label": "PDF (документ)"},
    "GIF": {"ext": "gif", "mime": "image/gif", "label": "GIF (анимация/растр)"},
}

IMAGE_EXTENSIONS = {
    "png": "PNG",
    "jpg": "JPG",
    "jpeg": "JPG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
    "ico": "ICO",
    "gif": "GIF",
    "pdf": "PDF",
}

IMAGE_MIME_TYPES = {
    "image/png": "PNG",
    "image/jpeg": "JPG",
    "image/pjpeg": "JPG",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
    "image/x-ms-bmp": "BMP",
    "image/tiff": "TIFF",
    "image/x-icon": "ICO",
    "image/vnd.microsoft.icon": "ICO",
    "image/gif": "GIF",
    "application/pdf": "PDF",
}


def normalize_format(fmt: str) -> str:
    """Normalizes format string to standard uppercase key."""
    if not fmt:
        return ""
    cleaned = fmt.upper().strip().lstrip(".")
    if cleaned in ("JPEG", "JPG"):
        return "JPG"
    if cleaned in ("TIF", "TIFF"):
        return "TIFF"
    return cleaned


def detect_image_format(filename: str | None = None, mime_type: str | None = None) -> str | None:
    """
    Detects image format from filename extension and/or mime type.
    Returns normalized format name (e.g. 'PNG', 'JPG', 'WEBP') or None if not an image.
    """
    if filename:
        ext = os.path.splitext(filename)[1].lower().lstrip(".")
        if ext in IMAGE_EXTENSIONS:
            return IMAGE_EXTENSIONS[ext]

    if mime_type:
        mime = mime_type.lower()
        if mime in IMAGE_MIME_TYPES:
            return IMAGE_MIME_TYPES[mime]
        # General substring fallback
        if "jpeg" in mime or "jpg" in mime:
            return "JPG"
        if "png" in mime:
            return "PNG"
        if "webp" in mime:
            return "WEBP"
        if "bmp" in mime:
            return "BMP"
        if "tiff" in mime:
            return "TIFF"
        if "icon" in mime or "ico" in mime:
            return "ICO"
        if "gif" in mime:
            return "GIF"

    return None


def format_size(size_bytes: int) -> str:
    """Formats file size into human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"
    return f"{size_bytes / (1024 * 1024):.2f} МБ"


def convert_image(input_bytes: bytes, target_format: str) -> tuple[bytes, str]:
    """
    Converts image raw bytes into the requested target format.
    Returns a tuple of (converted_bytes, file_extension).
    """
    target = normalize_format(target_format)
    if target not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"Неподдерживаемый целевой формат: {target_format}")

    with Image.open(io.BytesIO(input_bytes)) as img:
        output_io = io.BytesIO()
        ext = SUPPORTED_IMAGE_FORMATS[target]["ext"]

        if target == "JPG":
            # JPG does not support alpha transparency: blend onto white background
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="JPEG", quality=95, optimize=True)

        elif target == "PNG":
            img.save(output_io, format="PNG", optimize=True)

        elif target == "WEBP":
            img.save(output_io, format="WEBP", quality=95)

        elif target == "BMP":
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="BMP")

        elif target == "TIFF":
            img.save(output_io, format="TIFF")

        elif target == "ICO":
            ico_img = img.copy()
            # Standard ICO dimension limit is 256x256
            if ico_img.width > 256 or ico_img.height > 256:
                ico_img.thumbnail((256, 256), Image.Resampling.LANCZOS)
            if ico_img.mode not in ("RGBA", "RGB"):
                ico_img = ico_img.convert("RGBA")
            ico_img.save(output_io, format="ICO")

        elif target == "PDF":
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                img_rgba = img.convert("RGBA")
                bg.paste(img_rgba, mask=img_rgba.split()[3])
                save_img = bg
            elif img.mode != "RGB":
                save_img = img.convert("RGB")
            else:
                save_img = img
            save_img.save(output_io, format="PDF", resolution=100.0)

        elif target == "GIF":
            img.save(output_io, format="GIF")

        return output_io.getvalue(), ext
