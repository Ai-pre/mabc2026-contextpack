"""
Mock Notion connector.

Loads from data/demo_project/notion.md (markdown).
Provides: page search, page content retrieval.
Notion MVP는 markdown 파일 기반으로 동작. 실제 API 연동 시 교체.
"""

from __future__ import annotations

from typing import Any

import pathlib

from connectors import SourceConnector


class NotionConnector:
    """Mock Notion source connector (markdown file based for MVP)."""

    def __init__(self, data_path: str | None = None):
        if data_path is None:
            base = pathlib.Path(__file__).resolve().parent.parent / "data" / "demo_project"
            self._path = base / "notion.md"
        else:
            self._path = pathlib.Path(data_path)
        self._content = self._path.read_text(encoding="utf-8") if self._path.exists() else ""

    @property
    def source_name(self) -> str:
        return "Notion"

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        # notion.md를 섹션으로 나누어 페이지처럼 취급
        sections = self._split_sections()
        return [{"item_id": s["id"], "type": "page", "title": s["title"]} for s in sections[:limit]]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = query.lower()
        sections = self._split_sections()
        results: list[dict[str, Any]] = []
        for s in sections:
            if q in s["content"].lower():
                results.append({
                    "item_id": s["id"],
                    "type": "page",
                    "title": s["title"],
                    "snippet": self._snippet(s["content"], q),
                })
                if len(results) >= limit:
                    break
        return results

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        for s in self._split_sections():
            if s["id"] == item_id:
                return {"item_id": item_id, "type": "page", "title": s["title"], "content": s["content"]}
        return None

    def _split_sections(self) -> list[dict[str, str]]:
        """notion.md를 헤더 기준으로 섹션 분할."""
        sections: list[dict[str, str]] = []
        current_id: str | None = None
        current_title: str | None = None
        current_lines: list[str] = []
        for line in self._content.splitlines():
            if line.startswith("# "):
                if current_id is not None:
                    sections.append({"id": current_id, "title": current_title or "Untitled", "content": "\n".join(current_lines)})
                current_id = line.strip().replace("# ", "").replace(" ", "-").lower()
                current_title = line.strip().replace("# ", "")
                current_lines = []
            elif line.startswith("## "):
                # sub-section — attach to current
                current_lines.append(line)
            else:
                current_lines.append(line)
        if current_id is not None:
            sections.append({"id": current_id, "title": current_title or "Untitled", "content": "\n".join(current_lines)})
        return sections

    def _snippet(self, content: str, query: str) -> str:
        cl = content.lower()
        idx = cl.find(query)
        if idx == -1:
            return content[:200]
        start = max(0, idx - 60)
        end = min(len(content), idx + 200)
        return content[start:end]


def get_notion_connector() -> NotionConnector:
    return NotionConnector()
