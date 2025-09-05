import os
import subprocess
import zipfile
import tarfile
import gzip
import bz2
import lzma
from pathlib import Path

from simple_log_factory.log_factory import log_factory

_unrar_path = os.environ.get('UNRAR_PATH', '7z')
_logger = log_factory("Decompress File", unique_handler_types=True)


def decompress_file(file_path):
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        return False

    extract_dir = path.parent
    suffix = path.suffix.lower()

    try:
        if suffix == '.7z':
            _logger.debug("Trying to decompress a 7z archive...")
            try:
                import py7zr
                with py7zr.SevenZipFile(path, mode='r') as archive:
                    archive.extractall(path=extract_dir)
                return True
            except ImportError as e:
                _logger.error(f"Error importing py7zr: {str(e)}")
                return False

        elif suffix == '.rar':
            _logger.debug("Trying to decompress a rar archive...")
            try:
                subprocess.run([
                    _unrar_path, 'x', '-y', str(path), f"-o{str(extract_dir)}"
                ], check=True)
                return True
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                _logger.error(f"Error decompressing rar archive: {str(e)}")
                return False

        elif suffix == '.zip':
            _logger.debug("Trying to decompress a zip archive...")
            try:
                with zipfile.ZipFile(path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                return True
            except zipfile.BadZipFile as e:
                _logger.error(f"Error decompressing zip archive: {str(e)}")
                return False

        elif suffix in ['.tar', '.tgz'] or path.name.endswith(('.tar.gz', '.tar.bz2', '.tar.xz')):
            _logger.debug("Trying to decompress a tar archive...")
            try:
                with tarfile.open(path, 'r:*') as tar_ref:
                    tar_ref.extractall(extract_dir)
                return True
            except tarfile.TarError as e:
                _logger.error(f"Error decompressing tar archive: {str(e)}")
                return False

        elif suffix == '.gz' and not path.name.endswith('.tar.gz'):
            return _decompress_native(path, gzip, 'gzip', extract_dir / path.stem)

        elif suffix == '.bz2' and not path.name.endswith('.tar.bz2'):
            return _decompress_native(path, bz2, 'bz2', extract_dir / path.stem)

        elif suffix == '.xz' and not path.name.endswith('.tar.xz'):
            return _decompress_native(path, lzma, 'lzma', extract_dir / path.stem)

        else:
            # Unsupported archive format
            return False

    except Exception as e:
        _logger.error(f"Unexpected error decompressing file: {str(e)}")
        return False

def _decompress_native(path, engine, archive_type, output_path):
    _logger.debug(f"Trying to decompress a {archive_type} archive...")
    try:
        with engine.open(path, 'rb') as file:
            with open(output_path, 'wb') as output_file:
                output_file.write(file.read())
        return True
    except OSError as e:
        _logger.error(f"Error decompressing {archive_type} archive: {str(e)}")
        return False
