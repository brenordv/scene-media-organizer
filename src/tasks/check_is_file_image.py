import os

from PIL import Image, UnidentifiedImageError
from opentelemetry import trace

from src.utils import get_otel_log_handler

_img_extensions = [
    "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif",
    "webp", "heif", "heic", "avif", "ico", "svg",
]
_logger = get_otel_log_handler("Check File Image", unique_handler_types=True)


@_logger.trace("is_image_file")
def is_image_file(path: str) -> bool:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("file.path", str(path))

    if not os.path.isfile(path):
        return False

    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except (ImportError, UnidentifiedImageError, OSError) as e:
        _logger.debug(f"Error verifying image file [{path}]: {str(e)}")
        return any(path.endswith(ext) for ext in _img_extensions)
