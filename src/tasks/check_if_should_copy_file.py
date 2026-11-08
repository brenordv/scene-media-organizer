from pathlib import Path

_MEDIA_EXTENSIONS = frozenset(
    {"mkv", "mp4", "avi", "m4v", "mov", "ts", "wmv", "mpg", "mpeg", "webm", "srt", "ass", "sub", "idx"}
)


def check_should_copy_file(filename: str, is_archive: bool) -> bool:
    """Decide from a file's name whether it is wanted.

    The check reads the name only, so a ROM renamed ``.mp4`` passes.

    Args:
        filename: The file's path.
        is_archive: Whether the file is an archive. An archive is not judged by its extension, which can be anything
            from ``.rar`` to ``.r00``.

    Returns:
        False when the name contains "sample". Otherwise True for an archive and for a video or subtitle extension.
    """
    if "sample" in filename.lower():
        # We don't care about files that contain "sample" in the name
        return False

    if is_archive:
        return True

    return Path(filename).suffix.lower().removeprefix(".") in _MEDIA_EXTENSIONS
