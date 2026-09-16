# connectors/slack/connector.py
from __future__ import annotations
import json
import os
from typing import Any

from connectors import BaseConnector, SearchResult


DATA_PATH = os.path.join(os.path.dirname(__file__), "../../data/demo_project/slack_data.json")


class SlackConnector(BaseConnector):
    @property
    def name(self) -> str:
        return "slack"

    def _load(self) -> dict[str, Any]:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        data = self._load()
        query_l = query.lower()
        results: list[SearchResult] = []

        for channel, messages in data.get("channels", {}).items():
            for msg in messages:
                text = msg.get("text", "").lower()
                if query_l in text:
                    results.append(SearchResult(
                        source="slack",
                        title=f"#{channel} · {msg['user']}",
                        snippet=msg["text"][:400],
                        ref=f"slack:{channel}:{msg['timestamp']}",
                        timestamp=msg.get("timestamp"),
                        primary_links=[f"slack://channel/{channel}"],
                        raw={"type": "message", "channel": channel, **msg},
                    ))

        results.sort(key=lambda r: r.snippet.lower().count(query_l), reverse=True)
        return results[:limit]
