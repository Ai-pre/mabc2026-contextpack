"""
Mock source connectors for MABC 2026 MVP.

Interface contract (same for all sources,mock + real):
    search(query, limit)  -> list[dict]
    get_item(item_id)     -> dict | None
    list_recent(limit)    -> list[dict]
    source_name           -> str

All connectors load from data/demo_project/ by default.
Later: swap in real GitHub/Jira/Slack/Notion connectors without
changing the router code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import json
import pathlib
from typing import Any


@runtime_checkable
class SourceConnector(Protocol):
    """Interface all source connectors must implement."""

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...

    def get_item(self, item_id: str) -> dict[str, Any] | None: ...

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]: ...

    @property
    def source_name(self) -> str: ...


def _load_demo_data(filename: str) -> dict:
    """Load a mock data file from data/demo_project/."""
    base = pathlib.Path(__file__).resolve().parent.parent / "data" / "demo_project"
    path = base / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)
