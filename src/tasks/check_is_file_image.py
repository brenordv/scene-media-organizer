import os
from PIL import Image, UnidentifiedImageError
from simple_log_factory.log_factory import log_factory

_img_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "heif", "heic", "avif", "ico", "svg" ]
_logger = log_factory("Check File Image", unique_handler_types=True)

def is_image_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False

    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except (ImportError, UnidentifiedImageError, OSError) as e:
        _logger.debug(f"Error verifying image file [{path}]: {str(e)}")
        return any(path.endswith(ext) for ext in _img_extensions)
