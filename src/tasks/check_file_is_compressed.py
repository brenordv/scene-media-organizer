import os
import re
import zipfile
from typing import Pattern

# Any file ending in one of these is almost certainly compressed
COMPRESSED_EXTENSIONS = (
    '.zip', '.rar', '.7z',
    '.gz', '.gzip', '.tgz', '.tar.gz', '.tar.gzip',
    '.bz2', '.bzip2', '.tbz', '.tbz2', '.tar.bz2',
    '.xz', '.txz', '.tar.xz',
    '.lz', '.lzma', '.lzop',
    '.Z', '.z'  # old Unix compress
)

# Patterns for multipart archives
MULTIPART_PATTERNS: tuple[Pattern, ...] = (
    re.compile(r'\.part\d+\.rar$', re.IGNORECASE),  # foo.part01.rar
    re.compile(r'\.r\d{2,}$',       re.IGNORECASE),  # foo.r00, foo.r001
    re.compile(r'\.z\d{2,}$',       re.IGNORECASE),  # foo.z01, foo.z001
    re.compile(r'\.7z\.\d{3,}$',    re.IGNORECASE),  # foo.7z.001
)

MAGIC_SIGNATURES = {
    b'\x1f\x8b':           "gzip",      # .gz, .tgz, .tar.gz
    b'BZh':                "bzip2",     # .bz2
    b'\xfd7zXZ\x00':       "xz/lzma2",  # .xz, .txz, .tar.xz
    b'7z\xbc\xaf\x27\x1c': "7z",        # .7z
    b'Rar!\x1a\x07\x00':   "rar (v4)",  # .rar
    b'Rar!\x1a\x07\x01\x00':"rar (v5)",  # .rar
    b'\x1f\x9d':           "compress (.Z)",  # old Unix .Z
    b'LZIP':               "lzip",      # .lz
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
        with open(path, 'rb') as f:
            header = f.read(8)
    except Exception:
        return False

    for sig, _fmt in MAGIC_SIGNATURES.items():
        if header.startswith(sig):
            return True

    # no match → not a compressed/archive file
    return False


if __name__ == "__main__":
    import tempfile
    import os

    def create_file(name: str, content: bytes = b"") -> str:
        """Helper to write a file under a temp dir."""
        path = os.path.join(tmpdir, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    # Collect (path, expected_result) pairs
    tests = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1) Extension-based positives
        tests.append((create_file("archive.zip"), True))
        tests.append((create_file("backup.tar.gz"), True))
        tests.append((create_file("video.mp4"), False))

        # 2) Multipart patterns
        tests.append((create_file("data.part01.rar"), True))
        tests.append((create_file("chunk.z01"),          True))
        tests.append((create_file("photo.r00"),           True))

        # 3) Magic-byte inspection
        # 3a) GZIP header (0x1f,0x8b,...)
        tests.append((create_file("mystery.bin", b"\x1f\x8b\x08\x00\x00\x00\x00\x00"), True))
        # 3b) BZIP2 header ("BZh")
        tests.append((create_file("stream.bin", b"BZh9dummydata"), True))
        # 3c) Random data → should be false
        tests.append((create_file("random.bin", os.urandom(16)), False))

        # 4) Non‐existent path → should be False
        tests.append(("/path/does/not/exist", False))

        # Run all tests
        passed = failed = 0
        for path, expected in tests:
            result = is_compressed_file(path)
            if result == expected:
                print(f"PASS: {os.path.basename(path)} → {result}")
                passed += 1
            else:
                print(f"FAIL: {os.path.basename(path)} → got {result}, expected {expected}")
                failed += 1

        # Summary
        total = len(tests)
        print(f"\n{passed}/{total} tests passed, {failed} failed.")

        # Exit with non-zero code on failure (useful if you integrate into CI)
        if failed:
            import sys
            sys.exit(1)
