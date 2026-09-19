"""File-backed workspace and normalized source storage for ContextPack."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_WORKSPACE_ID = "demo"
DEMO_WORKSPACE_NAME = "Partial Refund Demo"
DEMO_ROLE = "3주 만에 프로젝트에 복귀한 백엔드 개발자"
DEMO_TASK = "결제 모듈의 부분환불 기능 수정"
_WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{32}\Z")
_SOURCE_ID = re.compile(r"src_[0-9a-f]{32}\Z")
_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
}


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceValidationError(ValueError):
    pass


class SourceNotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, content: bytes) -> None:
    """Replace one complete file; temporary files stay on the same filesystem."""
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows denies replacement while the separate MCP process briefly reads a manifest.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.02 * (2 ** attempt))
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkspaceValidationError("Workspace data must contain valid JSON values.") from exc


class WorkspaceStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root if root is not None else PROJECT_ROOT / "workspaces").resolve()
        self._lock = threading.RLock()

    def _workspace_path(self, workspace_id: str) -> Path:
        if not isinstance(workspace_id, str) or (
            workspace_id != DEMO_WORKSPACE_ID and not _WORKSPACE_ID.fullmatch(workspace_id)
        ):
            raise WorkspaceValidationError("Invalid workspace ID.")
        path = self.root / workspace_id
        if path.is_symlink() or path.resolve().parent != self.root:
            raise WorkspaceValidationError("Workspace path must stay inside the workspace directory.")
        return path

    @staticmethod
    def _child_path(parent: Path, name: str) -> Path:
        path = parent / name
        if path.is_symlink() or path.resolve().parent != parent.resolve():
            raise WorkspaceValidationError("Source path must stay inside its workspace directory.")
        return path

    @staticmethod
    def _text(value: Any, field: str, *, required: bool = False) -> str:
        if not isinstance(value, str) or "\x00" in value:
            raise WorkspaceValidationError(f"{field} must be text.")
        value = value.strip()
        if required and not value:
            raise WorkspaceValidationError(f"{field} is required.")
        return value

    def _write_manifest(self, manifest: dict) -> None:
        directory = self._workspace_path(manifest["workspace_id"])
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._child_path(directory, "manifest.json"), _json_bytes(manifest))

    def _ensure_demo(self) -> None:
        directory = self._workspace_path(DEMO_WORKSPACE_ID)
        manifest_path = self._child_path(directory, "manifest.json")
        if manifest_path.exists():
            return
        created_at = _now()
        self._write_manifest({
            "workspace_id": DEMO_WORKSPACE_ID,
            "name": DEMO_WORKSPACE_NAME,
            "role": DEMO_ROLE,
            "task": DEMO_TASK,
            "is_demo": True,
            "created_at": created_at,
            "updated_at": created_at,
            "sources": [
                {"id": f"demo-{connector}", "source_type": "demo", "title": title,
                 "connector": connector, "description": "Local demo fixture"}
                for connector, title in (("github", "GitHub"), ("jira", "Jira"),
                                         ("slack", "Slack"), ("notion", "Notion"))
            ],
        })

    def get_workspace(self, workspace_id: str) -> dict:
        with self._lock:
            directory = self._workspace_path(workspace_id)
            if workspace_id == DEMO_WORKSPACE_ID:
                self._ensure_demo()
            path = self._child_path(directory, "manifest.json")
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise WorkspaceNotFound("Workspace was not found.") from exc
            except (ValueError, UnicodeError) as exc:
                raise WorkspaceValidationError("Workspace manifest is invalid.") from exc
            if (not isinstance(manifest, dict)
                    or manifest.get("workspace_id") != workspace_id
                    or not isinstance(manifest.get("sources"), list)
                    or any(not isinstance(source, dict) for source in manifest["sources"])
                    or manifest.get("is_demo") is not (workspace_id == DEMO_WORKSPACE_ID)):
                raise WorkspaceValidationError("Workspace manifest is invalid.")
            return manifest

    def list_workspaces(self) -> list[dict]:
        with self._lock:
            demo = self.get_workspace(DEMO_WORKSPACE_ID)
            workspaces = []
            for path in self.root.iterdir():
                if _WORKSPACE_ID.fullmatch(path.name) and path.is_dir():
                    workspaces.append(self.get_workspace(path.name))
            workspaces.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
            return [demo, *workspaces]

    def create_workspace(self, name: str = "Untitled Workspace", role: str = "", task: str = "") -> dict:
        with self._lock:
            timestamp = _now()
            manifest = {
                "workspace_id": f"ws_{uuid4().hex}",
                "name": self._text(name, "name", required=True),
                "role": self._text(role, "role"),
                "task": self._text(task, "task"),
                "is_demo": False,
                "created_at": timestamp,
                "updated_at": timestamp,
                "sources": [],
            }
            self._write_manifest(manifest)
            return manifest

    def update_workspace(self, workspace_id: str, /, **fields: Any) -> dict:
        with self._lock:
            if set(fields) - {"name", "role", "task"}:
                raise WorkspaceValidationError("Only name, role and task can be updated.")
            manifest = self.get_workspace(workspace_id)
            for key, value in fields.items():
                value = self._text(value, key, required=key == "name")
                if workspace_id == DEMO_WORKSPACE_ID and key == "name" and value != DEMO_WORKSPACE_NAME:
                    raise WorkspaceValidationError("The demo workspace name cannot be changed.")
                manifest[key] = value
            if fields:
                manifest["updated_at"] = _now()
                self._write_manifest(manifest)
            return manifest

    def list_sources(self, workspace_id: str) -> list[dict]:
        return self.get_workspace(workspace_id)["sources"]

    @staticmethod
    def _github_repository(value: str) -> str:
        if not isinstance(value, str) or "\x00" in value:
            raise WorkspaceValidationError("GitHub repository must be text.")
        value = value.strip()
        for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
            if value.lower().startswith(prefix):
                value = value[len(prefix):]
                break
        value = value.strip().strip("/")
        if value.endswith(".git"):
            value = value[:-4]
        parts = value.split("/")
        if len(parts) != 2 or any(
            not part or len(part) > 100 or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            for part in parts
        ):
            raise WorkspaceValidationError("GitHub repository must be in owner/repo form.")
        return f"{parts[0]}/{parts[1]}"

    def add_github_source(self, workspace_id: str, repository: str) -> dict:
        with self._lock:
            manifest = self.get_workspace(workspace_id)
            if manifest["is_demo"]:
                raise WorkspaceValidationError("Live GitHub connections are not added to the demo workspace.")
            repository = self._github_repository(repository)
            for existing in manifest["sources"]:
                if existing.get("source_type") == "connector" and existing.get("connector") == "github" \
                        and existing.get("repository", "").casefold() == repository.casefold():
                    raise WorkspaceValidationError("This GitHub repository is already connected.")
            source = {
                "id": f"src_{uuid4().hex}",
                "source_type": "connector",
                "connector": "github",
                "title": f"GitHub · {repository}",
                "repository": repository,
                "created_at": _now(),
            }
            manifest["sources"].append(source)
            manifest["updated_at"] = source["created_at"]
            self._write_manifest(manifest)
            return source

    @staticmethod
    def _filename(filename: str) -> str:
        if not isinstance(filename, str) or any(ord(char) < 32 for char in filename):
            raise WorkspaceValidationError("Invalid source filename.")
        # Browsers may provide a Windows fakepath or a Unix path. Never use either as storage paths.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not filename or len(filename) > 255 or Path(filename).suffix.lower() not in _MIME_TYPES:
            raise WorkspaceValidationError("Supported source files are PDF, DOCX, TXT, MD and JSON.")
        return filename

    def add_source(self, workspace_id: str, filename: str, raw: bytes, document: dict) -> dict:
        with self._lock:
            manifest = self.get_workspace(workspace_id)
            filename = self._filename(filename)
            if not isinstance(raw, bytes) or not raw:
                raise WorkspaceValidationError("Source file is empty.")
            if not isinstance(document, dict) or not isinstance(document.get("body"), str):
                raise WorkspaceValidationError("Normalized document body must be text.")
            if not document["body"].strip():
                raise WorkspaceValidationError("Source file has no extractable text.")
            metadata = document.get("metadata", {})
            if not isinstance(metadata, dict):
                raise WorkspaceValidationError("Normalized document metadata must be an object.")
            source_id = f"src_{uuid4().hex}"
            created_at = _now()
            suffix = Path(filename).suffix.lower()
            mime_type = _MIME_TYPES[suffix]
            source = {
                "id": source_id,
                "document_id": source_id,
                "title": filename,
                "original_filename": filename,
                "source_type": "upload",
                "mime_type": mime_type,
                "size_bytes": len(raw),
                "created_at": created_at,
                "warnings": metadata.get("warnings", []),
            }
            normalized = {
                **document,
                "id": source_id,
                "workspace_id": workspace_id,
                "source_type": "upload",
                "title": filename,
                "timestamp": document.get("timestamp"),
                "metadata": {**metadata, "original_filename": filename,
                             "mime_type": mime_type, "size_bytes": len(raw)},
            }
            normalized_bytes = _json_bytes(normalized)
            directory = self._workspace_path(workspace_id)
            originals = self._child_path(directory, "sources")
            documents = self._child_path(directory, "normalized")
            originals.mkdir(exist_ok=True)
            documents.mkdir(exist_ok=True)
            raw_path = self._child_path(originals, source_id + suffix)
            normalized_path = self._child_path(documents, source_id + ".json")
            try:
                _atomic_write(raw_path, raw)
                _atomic_write(normalized_path, normalized_bytes)
                manifest["sources"].append(source)
                manifest["updated_at"] = created_at
                self._write_manifest(manifest)
            except Exception:
                raw_path.unlink(missing_ok=True)
                normalized_path.unlink(missing_ok=True)
                raise
            return source

    @staticmethod
    def _registered_source(manifest: dict, source_id: str) -> dict:
        for source in manifest["sources"]:
            if source.get("id") == source_id:
                return source
        raise SourceNotFound("Source was not found in this workspace.")

    def get_document(self, workspace_id: str, document_id: str) -> dict:
        with self._lock:
            manifest = self.get_workspace(workspace_id)
            if not isinstance(document_id, str) or not _SOURCE_ID.fullmatch(document_id):
                raise SourceNotFound("Document was not found in this workspace.")
            source = self._registered_source(manifest, document_id)
            if source.get("source_type") != "upload" or source.get("document_id") != document_id:
                raise SourceNotFound("Document was not found in this workspace.")
            directory = self._child_path(self._workspace_path(workspace_id), "normalized")
            path = self._child_path(directory, document_id + ".json")
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise SourceNotFound("Normalized document was not found.") from exc
            except (ValueError, UnicodeError) as exc:
                raise WorkspaceValidationError("Normalized document is invalid.") from exc
            if (not isinstance(document, dict) or document.get("id") != document_id
                    or document.get("workspace_id") != workspace_id
                    or document.get("source_type") != "upload"
                    or not isinstance(document.get("body"), str)):
                raise WorkspaceValidationError("Normalized document does not match its workspace.")
            return document

    def list_documents(self, workspace_id: str) -> list[dict]:
        with self._lock:
            manifest = self.get_workspace(workspace_id)
            return [self.get_document(workspace_id, source.get("document_id"))
                    for source in manifest["sources"] if source.get("source_type") == "upload"]

    def remove_source(self, workspace_id: str, source_id: str) -> dict:
        with self._lock:
            manifest = self.get_workspace(workspace_id)
            source = self._registered_source(manifest, source_id)
            source_type = source.get("source_type")
            if source_type == "demo":
                raise WorkspaceValidationError("Demo fixture sources cannot be removed.")
            if source_type not in {"upload", "connector"}:
                raise WorkspaceValidationError("Unsupported source type.")
            if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
                raise WorkspaceValidationError("Invalid source ID.")

            manifest["sources"] = [item for item in manifest["sources"] if item.get("id") != source_id]
            manifest["updated_at"] = _now()
            self._write_manifest(manifest)

            if source_type == "connector":
                return source

            filename = self._filename(source.get("original_filename"))
            directory = self._workspace_path(workspace_id)
            originals = self._child_path(directory, "sources")
            documents = self._child_path(directory, "normalized")
            paths = [self._child_path(originals, source_id + Path(filename).suffix.lower()),
                     self._child_path(documents, source_id + ".json")]
            # Remove registration first: even a failed cleanup cannot make a deleted source searchable.
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return source
