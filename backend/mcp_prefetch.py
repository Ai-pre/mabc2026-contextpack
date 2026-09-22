from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class McpPrefetchError(RuntimeError):
    pass


async def prefetch_workspace_evidence(
    workspace_id: str,
    query: str,
    source_types: str,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Call the real scoped MCP server before model generation.

    This preserves MCP as the retrieval boundary while removing the first LLM
    turn that previously existed only to select workspace_retrieve.
    """
    env = os.environ.copy()
    env["CONTEXTPACK_WORKSPACE_ID"] = workspace_id
    env.pop("CONTEXTPACK_PRELOADED_EVIDENCE", None)

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "mabc_mcp_scoped_server.py")],
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    started = time.perf_counter()
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "workspace_retrieve",
                    arguments={
                        "workspace_id": workspace_id,
                        "query": query,
                        "source_types": source_types,
                    },
                )
    except Exception as exc:
        raise McpPrefetchError(f"workspace_retrieve prefetch failed: {exc}") from exc

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    if getattr(result, "isError", False):
        raise McpPrefetchError("workspace_retrieve returned an MCP error.")

    chunks = [
        getattr(item, "text", "")
        for item in getattr(result, "content", [])
        if getattr(item, "text", "")
    ]
    if not chunks:
        raise McpPrefetchError("workspace_retrieve returned no text payload.")

    try:
        payload = json.loads("\n".join(chunks))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise McpPrefetchError("workspace_retrieve returned invalid JSON.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
        raise McpPrefetchError("workspace_retrieve returned an invalid evidence bundle.")

    tool_timing = payload.get("timing_ms", {})
    timing = {
        str(key): round(float(value), 2)
        for key, value in tool_timing.items()
        if isinstance(value, (int, float)) and value >= 0
    }
    aggregate_ms = timing.get("aggregate", 0.0)
    timing["before_retrieval"] = round(max(0.0, elapsed_ms - aggregate_ms), 2)

    return payload, timing
