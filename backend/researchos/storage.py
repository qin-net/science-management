"""Local file storage. Keys are derived from IDs only, never from user-supplied filenames.
Each version owns its key, so concurrent uploads or deletes of equal bytes cannot collide."""

import hashlib
import os
import re
import uuid
from pathlib import Path

from researchos.config import get_settings
from researchos.errors import ApiError

PDF_MAGIC = b"%PDF-"


def _root() -> Path:
    root = get_settings().storage_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def storage_key(workspace_id: uuid.UUID, project_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return f"ws/{workspace_id}/pr/{project_id}/{version_id}.pdf"


def clean_filename(raw: str | None) -> str:
    """Reduce a client filename to a display name. It is never used in a path."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[-255:] or "upload.pdf"


def resolve(key: str) -> Path:
    root = _root()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ApiError(400, "invalid_storage_key", "storage key escapes the data directory")
    return path


def temp_dir() -> Path:
    """Scratch space on the same volume and with the same access as stored papers."""
    path = _root() / "tmp"
    path.mkdir(exist_ok=True)
    return path


class IncomingFile:
    """An upload being spooled to a temp file inside storage, so the final rename is atomic."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.size = 0
        self._digest = hashlib.sha256()
        self._head = b""
        self.path = temp_dir() / f"{uuid.uuid4()}.part"
        self._fh = self.path.open("wb")

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.max_bytes:
            raise ApiError(
                413, "file_too_large", f"file exceeds {self.max_bytes // (1024 * 1024)} MB"
            )
        if len(self._head) < len(PDF_MAGIC):
            self._head += chunk[: len(PDF_MAGIC) - len(self._head)]
        self._digest.update(chunk)
        self._fh.write(chunk)

    def finish(self) -> str:
        """Validate and sync the spooled bytes; returns their sha256."""
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        if self.size == 0:
            raise ApiError(415, "not_a_pdf", "uploaded file is empty")
        if self._head != PDF_MAGIC:
            raise ApiError(415, "not_a_pdf", "uploaded file is not a PDF (missing %PDF- header)")
        return self._digest.hexdigest()

    def commit(self, key: str) -> None:
        dest = resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.path, dest)

    def discard(self) -> None:
        if not self._fh.closed:
            self._fh.close()
        self.path.unlink(missing_ok=True)


def open_pdf(key: str) -> Path:
    path = resolve(key)
    if not path.is_file():
        raise ApiError(410, "file_missing", "the stored file is no longer available")
    return path


def delete(key: str) -> None:
    resolve(key).unlink(missing_ok=True)


def storage_writable() -> bool:
    try:
        root = _root()
        probe = root / f".write-probe-{uuid.uuid4()}"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False
