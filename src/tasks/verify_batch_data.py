from pathlib import Path

from opentelemetry import trace

from src.data.activity_logger import ActivityTracker
from src.utils import _sha256

_activity_logger = ActivityTracker("Verify Batch Data")


@_activity_logger.trace("verify_batch_data")
def verify_batch_data(batch_id, batch_data):
    span = trace.get_current_span()
    done_items = [
        item for item in batch_data
        if item.get("status") == "DONE" and item.get("target_path") is not None
    ]

    if span.is_recording():
        span.set_attributes({
            "batch.id": str(batch_id),
            "batch.total_items": len(batch_data),
            "batch.done_items": len(done_items),
        })

    if len(done_items) == 0:
        _activity_logger.warning(
            f"[B.ID: {batch_id}] No items set as DONE. Looks like it failed."
        )
        return False, {}

    _activity_logger.debug(
        f"[B.ID: {batch_id}] Verifying {len(done_items)} items set as DONE."
    )

    all_ok = True

    verification_result = {}
    for item in done_items:
        source_file = item.get("full_path")
        destination_path = item.get("target_path")
        filename = item.get("filename")

        if any(x is None for x in [source_file, destination_path, filename]):
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"is missing one of the required fields."
            )
            continue

        _activity_logger.info(
            f"[B.ID: {batch_id}] Verifying item [{filename}]..."
        )

        src_path = Path(source_file)
        dst_path = Path(destination_path).joinpath(filename)

        if not dst_path.exists():
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"destination file does not exist: {dst_path}"
            )
            all_ok = False
            continue

        if not src_path.exists():
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"source file does not exist: {source_file}"
            )
            all_ok = False
            continue

        src_size = src_path.stat().st_size
        dst_size = dst_path.stat().st_size

        if src_size != dst_size:
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"file size mismatch: src={src_size} dst={dst_size} "
                f"({src_path} -> {dst_path})"
            )
            all_ok = False
            verification_result[filename] = {"size": False, "hash": None}
            continue

        try:
            src_hash = _sha256(src_path)
            dst_hash = _sha256(dst_path)
        except Exception as exc:
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"error computing hashes: {exc} ({src_path} -> {dst_path})"
            )
            all_ok = False
            continue

        if src_hash != dst_hash:
            _activity_logger.error(
                f"[B.ID: {batch_id}] Item [{item.get('id')}] "
                f"content mismatch (SHA-256 differs) ({src_path} -> {dst_path})"
            )
            all_ok = False
            verification_result[filename] = {"size": True, "hash": False}
            continue

        _activity_logger.debug(
            f"[B.ID: {batch_id}] Item [{item.get('id')}] "
            f"verified successfully ({src_path.name})"
        )

        verification_result[filename] = {"size": True, "hash": True}

    if all_ok:
        _activity_logger.info(
            f"[B.ID: {batch_id}] Verification complete. All DONE items look good."
        )

    return all_ok, verification_result
