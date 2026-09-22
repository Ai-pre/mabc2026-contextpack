from __future__ import annotations

from typing import Any

EVIDENCE_SCHEMA_VERSION = "1.0"


def make_evidence(
    *,
    evidence_id: str,
    source_type: str,
    source_ref: str,
    content: str,
    kind: str,
    timestamp: str | None = None,
    author: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the connector-neutral evidence record consumed by ContextPack.

    Provider-specific retrievers may keep their native response fields for
    debugging/backward compatibility, but semantic handoff compilation should
    use this common evidence shape.
    """
    required = {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "content": content,
        "kind": kind,
    }
    for name, value in required.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be non-empty text.")

    return {
        "evidence_id": evidence_id.strip(),
        "source_type": source_type.strip().casefold(),
        "kind": kind.strip(),
        "source_ref": source_ref.strip(),
        "content": content.strip(),
        "timestamp": timestamp if isinstance(timestamp, str) and timestamp.strip() else None,
        "author": author if isinstance(author, str) and author.strip() else None,
        "metadata": dict(metadata or {}),
    }
