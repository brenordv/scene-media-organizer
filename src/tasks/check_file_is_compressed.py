import os
import re
import zipfile
from re import Pattern

# Any file ending in one of these is almost certainly compressed
COMPRESSED_EXTENSIONS = (
    ".zip",
    ".rar",
    ".7z",
    ".gz",
    ".gzip",
    ".tgz",
    ".tar.gz",
    ".tar.gzip",
    ".bz2",
    ".bzip2",
    ".tbz",
    ".tbz2",
    ".tar.bz2",
    ".xz",
    ".txz",
    ".tar.xz",
    ".lz",
    ".lzma",
    ".lzop",
    ".Z",
    ".z",  # old Unix compress
)

# Patterns for multipart archives
MULTIPART_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"\.part\d+\.rar$", re.IGNORECASE),  # foo.part01.rar
    re.compile(r"\.r\d{2,}$", re.IGNORECASE),  # foo.r00, foo.r001
    re.compile(r"\.z\d{2,}$", re.IGNORECASE),  # foo.z01, foo.z001
    re.compile(r"\.7z\.\d{3,}$", re.IGNORECASE),  # foo.7z.001
)

MAGIC_SIGNATURES = {
    b"\x1f\x8b": "gzip",  # .gz, .tgz, .tar.gz
    b"BZh": "bzip2",  # .bz2
    b"\xfd7zXZ\x00": "xz/lzma2",  # .xz, .txz, .tar.xz
    b"7z\xbc\xaf\x27\x1c": "7z",  # .7z
    b"Rar!\x1a\x07\x00": "rar (v4)",  # .rar
    b"Rar!\x1a\x07\x01\x00": "rar (v5)",  # .rar
    b"\x1f\x9d": "compress (.Z)",  # old Unix .Z
    b"LZIP": "lzip",  # .lz
}


def is_compressed_file(path: str) -> bool:
    """
    Return True if `path` points to a compressed archive (or part thereof).
    Otherwise, returns False.
    """
    # 1) sanity check
    if not os.path.isfile(path):
        return False

    name = os.path.basename(path)
    lower = name.lower()

    # 2) extension‐based shortcuts
    if lower.endswith(COMPRESSED_EXTENSIONS):
        return True
    for pattern in MULTIPART_PATTERNS:
        if pattern.search(lower):
            return True

    # 3) ZIP-specific check (more thorough than magic‐byte alone)
    try:
        if zipfile.is_zipfile(path):
            return True
    except Exception:
        pass

    # 4) magic‐byte inspection
    try:
        with open(path, "rb") as f:
            header = f.read(8)
    except Exception:
        return False

    for sig, _fmt in MAGIC_SIGNATURES.items():
        if header.startswith(sig):
            return True

    # no match → not a compressed/archive file
    return False
