import io
import logging
import os
import tempfile
from typing import List, Optional

import qrcode
import zxingcpp
from PIL import Image

logger = logging.getLogger(__name__)


def generate_qr_image(content: str) -> io.BytesIO:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def read_qr_from_image(image_source: str | io.BytesIO | bytes) -> List[str]:
    try:
        if isinstance(image_source, bytes):
            image_source = io.BytesIO(image_source)

        with Image.open(image_source) as img:
            results = zxingcpp.read_barcodes(img)
            if results:
                return [r.text for r in results if r.text]

            gray_img = img.convert("L")
            results = zxingcpp.read_barcodes(gray_img)
            if results:
                return [r.text for r in results if r.text]

            from PIL import ImageEnhance
            enhancer = ImageEnhance.Contrast(gray_img)
            contrast_img = enhancer.enhance(2.0)
            results = zxingcpp.read_barcodes(contrast_img)
            return [r.text for r in results if r.text]

    except Exception as e:
        logger.warning(f"Error reading QR code from image: {e}")
        return []
