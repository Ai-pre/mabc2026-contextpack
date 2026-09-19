"""Scope the ContextPack MCP surface to sources registered in the current workspace.

Hermes 0.21 receives every tool advertised by an MCP server. Exposing demo tools
next to live connectors causes the model to retry unrelated tools after a
successful retrieval. This entrypoint imports the implementations from
mabc_mcp_server but advertises only tools that can actually serve the bound
workspace.
"""

from __future__ import annotations

import os

from mcp.server import MCPServer

import mabc_mcp_server as impl


server = MCPServer(
    "mabc-sources",
    "1.2.0",
    description=(
        "ContextPack workspace-scoped source connector. Only tools backed by "
        "sources registered in the current analysis workspace are advertised."
    ),
)


def _register(name: str) -> None:
    fn = getattr(impl, name)
    server.add_tool(fn, name=name, description=(fn.__doc__ or "").strip())


def _scope() -> str:
    value = os.environ.get("CONTEXTPACK_WORKSPACE_ID", "")
    if value in {"", "${CONTEXTPACK_WORKSPACE_ID}"}:
        return "demo"
    return value


workspace_id = _scope()
workspace = impl._STORE.get_workspace(workspace_id)

if workspace.get("is_demo"):
    for tool_name in (
        "demo_context_retrieve",
        "github_search", "github_get",
        "jira_search", "jira_get",
        "slack_search", "slack_get",
        "notion_search", "notion_get",
    ):
        _register(tool_name)
else:
    sources = workspace.get("sources", [])

    if any(source.get("source_type") == "upload" for source in sources):
        for tool_name in ("document_retrieve", "document_search", "document_get"):
            _register(tool_name)

    connectors = {
        source.get("connector")
        for source in sources
        if source.get("source_type") == "connector"
    }

    if "github" in connectors:
        _register("github_retrieve")
    if "slack" in connectors:
        _register("slack_retrieve")
    if "notion" in connectors:
        _register("notion_retrieve")


if __name__ == "__main__":
    server.run()
