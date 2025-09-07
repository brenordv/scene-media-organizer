from pathlib import Path

_extensions_to_skip = [
    "sh", "bat", "ps1", "py", "js", "rb", "pl", "php", "lua",
    "exe", "dll", "bin", "so", "out",

]


def check_should_copy_file(filename):
    if "sample" in filename.lower():
        # We don't care about files that contain "sample" in the name
        return False

    path = Path(filename)

    if path.suffix.lower().replace(".", "") in _extensions_to_skip:
        return False

    return True
