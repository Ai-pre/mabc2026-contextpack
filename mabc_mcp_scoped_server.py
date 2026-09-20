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


def _tool_names_for_workspace(workspace: dict) -> tuple[str, ...]:
    """Return the exact MCP surface for one bound workspace.

    Multi-source workspaces expose only workspace_retrieve. This makes the
    single aggregate call a runtime-enforced boundary instead of a prompt-only
    convention.
    """
    if workspace.get("is_demo"):
        return (
            "demo_context_retrieve",
            "github_search", "github_get",
            "jira_search", "jira_get",
            "slack_search", "slack_get",
            "notion_search", "notion_get",
        )

    sources = workspace.get("sources", [])
    has_documents = any(source.get("source_type") == "upload" for source in sources)
    connectors = {
        source.get("connector")
        for source in sources
        if source.get("source_type") == "connector"
    }

    active_kinds = (
        (1 if has_documents else 0)
        + (1 if "github" in connectors else 0)
        + (1 if "slack" in connectors else 0)
        + (1 if "notion" in connectors else 0)
    )

    if active_kinds > 1:
        return ("workspace_retrieve",)

    names: list[str] = []
    if has_documents:
        names.extend(("document_retrieve", "document_search", "document_get"))
    if "github" in connectors:
        names.append("github_retrieve")
    if "slack" in connectors:
        names.append("slack_retrieve")
    if "notion" in connectors:
        names.append("notion_retrieve")
    return tuple(names)


workspace_id = _scope()
workspace = impl._STORE.get_workspace(workspace_id)

for tool_name in _tool_names_for_workspace(workspace):
    _register(tool_name)


if __name__ == "__main__":
    server.run()
