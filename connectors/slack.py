"""
Mock Slack connector.

Loads from data/demo_project/slack.json.
Provides: messages by channel, search across channels.
"""

from __future__ import annotations

from typing import Any

from connectors import _load_demo_data, SourceConnector


class SlackConnector:
    """Mock Slack source connector."""

    def __init__(self, data_path: str | None = None):
        if data_path is None:
            self._data = _load_demo_data("slack.json")
        else:
            with open(data_path, encoding="utf-8") as f:
                self._data = json.load(f)

    @property
    def source_name(self) -> str:
        return "Slack"

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for channel, msgs in self._data.get("channels", {}).items():
            for m in msgs:
                messages.append({
                    "item_id": f"{channel}/{m['timestamp']}",
                    "type": "message",
                    "channel": channel,
                    "title": m["text"][:80],
                    "snippet": m["text"],
                })
        messages.sort(key=lambda x: x["item_id"], reverse=True)
        return messages[:limit]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = query.lower()
        results: list[dict[str, Any]] = []
        for channel, msgs in self._data.get("channels", {}).items():
            for m in msgs:
                if q in m["text"].lower():
                    results.append({
                        "item_id": f"{channel}/{m['timestamp']}",
                        "type": "message",
                        "channel": channel,
                        "title": m["text"][:80],
                        "snippet": m["text"],
                        "user": m.get("user", ""),
                        "timestamp": m.get("timestamp", ""),
                    })
                    if len(results) >= limit:
                        return results
        return results

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        # item_id format: channel/timestamp
        if "/" not in item_id:
            return None
        channel, ts = item_id.split("/", 1)
        for ch, msgs in self._data.get("channels", {}).items():
            if ch == channel:
                for m in msgs:
                    if m.get("timestamp") == ts:
                        return {"item_id": item_id, "channel": channel, "message": m}
        return None


def get_slack_connector() -> SlackConnector:
    return SlackConnector()
