import os
import re


def is_main_archive_file(file_path: str) -> bool:
    """
    Determine if the given file is the main (first) volume of a supported archive.

    Returns True for:
      - example.rar, example.zip, example.7z
      - example.tar, example.tar.gz, example.tgz, example.tar.bz2, example.tbz2,
        example.tar.xz, example.txz, example.gz, example.bz2, example.xz

    Returns False for:
      - any known multi-part volume, e.g. example.r00, example.r01, example.part01.rar,
        example.z01, example.zip.001, example.7z.002, example.tar.gz.003, etc.
      - any non-archive file.
    """
    filename = os.path.basename(file_path).lower()

    # Define each format's "main" extensions and known multi-part patterns
    formats = [
        # RAR
        {
            "main": [r"\.rar$"],
            "multi": [r"\.r\d{2,}$", r"\.part\d+\.rar$"],
        },
        # ZIP
        {
            "main": [r"\.zip$"],
            "multi": [r"\.z\d{2,}$", r"\.zip\.\d{3,}$", r"\.part\d+\.zip$"],
        },
        # 7z
        {
            "main": [r"\.7z$"],
            "multi": [r"\.7z\.\d{3,}$", r"\.part\d+\.7z$"],
        },
        # TAR
        {
            "main": [r"\.tar$"],
            "multi": [r"\.tar\.\d{3,}$", r"\.part\d+\.tar$"],
        },
        # TAR.GZ / TGZ
        {
            "main": [r"\.tar\.gz$", r"\.tgz$"],
            "multi": [
                r"\.tar\.gz\.\d{3,}$", r"\.tgz\.\d{3,}$",
                r"\.part\d+\.tar\.gz$", r"\.part\d+\.tgz$"
            ],
        },
        # TAR.BZ2 / TBZ2
        {
            "main": [r"\.tar\.bz2$", r"\.tbz2$"],
            "multi": [
                r"\.tar\.bz2\.\d{3,}$", r"\.tbz2\.\d{3,}$",
                r"\.part\d+\.tar\.bz2$", r"\.part\d+\.tbz2$"
            ],
        },
        # TAR.XZ / TXZ
        {
            "main": [r"\.tar\.xz$", r"\.txz$"],
            "multi": [
                r"\.tar\.xz\.\d{3,}$", r"\.txz\.\d{3,}$",
                r"\.part\d+\.tar\.xz$", r"\.part\d+\.txz$"
            ],
        },
        # GZ
        {
            "main": [r"\.gz$"],
            "multi": [r"\.gz\.\d{3,}$", r"\.part\d+\.gz$"],
        },
        # BZ2
        {
            "main": [r"\.bz2$"],
            "multi": [r"\.bz2\.\d{3,}$", r"\.part\d+\.bz2$"],
        },
        # XZ
        {
            "main": [r"\.xz$"],
            "multi": [r"\.xz\.\d{3,}$", r"\.part\d+\.xz$"],
        },
    ]

    # Check each format: multi-part first (=> False), then main-part (=> True)
    for fmt in formats:
        for pat in fmt["multi"]:
            if re.search(pat, filename):
                return False
        for pat in fmt["main"]:
            if re.search(pat, filename):
                return True

    # If we didn't recognize it as any supported archive, assume it's not a main archive file
    return False


if __name__ == "__main__":
    # Built-in tests for is_main_archive()
    tests = [
        ("example.rar", True),
        ("example.R00", False),
        ("example.r01", False),
        ("example.part01.rar", False),
        ("backup.zip", True),
        ("backup.z01", False),
        ("backup.zip.001", False),
        ("backup.part02.zip", False),
        ("data.7z", True),
        ("data.7z.001", False),
        ("data.part1.7z", False),
        ("archive.tar", True),
        ("archive.tar.gz", True),
        ("archive.tar.gz.002", False),
        ("stuff.tbz2", True),
        ("stuff.tbz2.003", False),
        ("document.txt", False),
        ("image.jpeg", False),
    ]

    failures = 0
    for path, expected in tests:
        result = is_main_archive_file(path)
        status = "PASS" if result == expected else "FAIL"
        print(f"{path:25} expected={expected!s:5} got={result!s:5} → {status}")
        if status == "FAIL":
            failures += 1

    print()
    if failures:
        print(f"{failures} test(s) failed.")
        exit(1)
    else:
        print("All tests passed!")
        exit(0)
