"""Workspace retrieval contracts, with temporary stores and no model/backend process."""
import asyncio
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mabc_mcp_server as sources
from backend.document_parser import parse_document
from backend.workspace_store import SourceNotFound, WorkspaceStore, WorkspaceValidationError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEMO_TOOL_NAMES = {f"{connector}_{action}" for connector in ("github", "jira", "slack", "notion")
                   for action in ("search", "get")}
TOOL_NAMES = DEMO_TOOL_NAMES | {"demo_context_retrieve", "github_retrieve", "slack_retrieve", "notion_retrieve", "document_retrieve", "document_search", "document_get"}


class DocumentMcpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="document_mcp_test_")
        self.addCleanup(self.temporary.cleanup)
        self.store = WorkspaceStore(Path(self.temporary.name))
        self.store_patch = patch.object(sources, "_STORE", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.scope_patch = patch.dict(os.environ, {"CONTEXTPACK_WORKSPACE_ID": "demo"})
        self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)

    def upload_fixture(self, workspace_id, filename="notion.md"):
        raw = (PROJECT_ROOT / "data" / "demo_project" / filename).read_bytes()
        return self.store.add_source(workspace_id, filename, raw, parse_document(filename, raw))

    def workspace(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        os.environ["CONTEXTPACK_WORKSPACE_ID"] = workspace_id
        return workspace_id

    def search(self, workspace_id, query="", **arguments):
        return json.loads(sources.document_search(workspace_id, query, **arguments))

    def test_demo_context_retrieve_returns_compact_conflict_preserving_bundle(self):
        raw = sources.demo_context_retrieve("결제 모듈 부분환불 기능 수정")
        response = json.loads(raw)
        self.assertEqual(response["workspace_id"], "demo")
        self.assertEqual(response["query"], "결제 모듈 부분환불 기능 수정")
        self.assertLess(len(raw), 7000)
        self.assertTrue(response["rules"]["preserve_conflicts"])
        self.assertEqual(response["rules"]["do_not_assume_missing_policy"], ["overseas partial refund"])
        self.assertEqual(response["evidence_schema_version"], "1.0")
        self.assertTrue(all({
            "evidence_id", "source_type", "kind", "source_ref", "content",
            "timestamp", "author", "metadata",
        }.issubset(item) for item in response["evidence"]))
        refs = {item["ref"] for item in response["evidence"]}
        self.assertIn("PR #148", refs)
        self.assertIn("PR #152", refs)
        self.assertIn("PAY-179 / comment by po-jang", refs)
        self.assertIn("payment-eng / legal-minsu", refs)
        claims = "\n".join(item["claim"] for item in response["evidence"])
        self.assertIn("14 days", claims)
        self.assertIn("keep the general-payment refund window at 7 days", claims)
        self.assertIn("not defined", claims)
        evidence_text = "\n".join(
            f"{item.get('source_ref', '')}\n{item.get('content', '')}"
            for item in response["evidence"]
        )
        self.assertNotIn("PR #160", evidence_text)
        self.assertNotIn("MKT-42", evidence_text)
        self.assertNotIn("site-reliability", evidence_text)

    def test_demo_search_and_get_contracts_remain_unchanged(self):
        github = json.loads(sources.github_search("pr-148"))
        self.assertEqual(github, [{
            "item_id": "pr-148", "type": "pull_request", "title": "PG API v3 migration",
            "status": "merged", "snippet": sources._GITHUB["items"][0]["body"][:200],
        }])
        self.assertEqual(json.loads(sources.github_get("pr-148")), sources._GITHUB["items"][0])
        file_result = json.loads(sources.github_get("payment_service.py"))
        self.assertNotIn("content", file_result)
        self.assertEqual(file_result["content_preview"], sources._GITHUB["items"][3]["content"][:1500])

        jira = json.loads(sources.jira_search("PAY-183"))
        self.assertEqual(len(jira), 1)
        self.assertEqual(set(jira[0]), {"item_id", "type", "summary", "status", "priority", "snippet"})
        self.assertEqual(jira[0]["item_id"], "PAY-183")
        self.assertEqual(json.loads(sources.jira_get("PAY-183")), sources._JIRA["issues"][0])

        slack = json.loads(sources.slack_search("PARTIAL_REFUND"))
        message = sources._SLACK["channels"]["payment-eng"][1]
        item_id = "payment-eng/2026-09-09T09:15:00Z"
        self.assertEqual(slack, [{"item_id": item_id, "type": "message", "channel": "payment-eng",
                                  "user": message["user"], "timestamp": message["timestamp"],
                                  "text": message["text"]}])
        self.assertEqual(json.loads(sources.slack_get(item_id)), {
            "item_id": item_id, "channel": "payment-eng", "message": message,
        })

        notion = json.loads(sources.notion_search("미지원"))
        self.assertEqual(len(notion), 1)
        self.assertEqual(set(notion[0]), {"item_id", "type", "title", "snippet"})
        section = sources._NOTION_SECTIONS[0]
        self.assertEqual(json.loads(sources.notion_get(section["id"])), {
            "item_id": section["id"], "type": "page", "title": section["title"], "content": section["content"],
        })
        for name in DEMO_TOOL_NAMES:
            fn = getattr(sources, name)
            parameters = inspect.signature(fn).parameters
            self.assertEqual(list(parameters), ["query", "limit"] if name.endswith("search") else ["item_id"])
            if name.endswith("search"):
                self.assertEqual(parameters["limit"].default, 10)
            else:
                self.assertIn("error", json.loads(fn("missing")))

    def test_all_ten_tools_have_stable_schemas_in_each_scope(self):
        for scope in ("demo", self.store.create_workspace()["workspace_id"]):
            with self.subTest(scope=scope), patch.dict(os.environ, {"CONTEXTPACK_WORKSPACE_ID": scope}):
                tools = {tool.name: tool for tool in asyncio.run(sources.server.list_tools())}
                self.assertEqual(set(tools), TOOL_NAMES)
                for name in DEMO_TOOL_NAMES:
                    expected = {"query", "limit"} if name.endswith("search") else {"item_id"}
                    self.assertEqual(set(tools[name].input_schema["properties"]), expected)
                self.assertEqual(set(tools["demo_context_retrieve"].input_schema["properties"]),
                                 {"query"})
                self.assertEqual(set(tools["github_retrieve"].input_schema["properties"]),
                                 {"workspace_id", "query", "repository", "recent_limit"})
                self.assertEqual(set(tools["slack_retrieve"].input_schema["properties"]),
                                 {"workspace_id", "query", "channel", "message_limit"})
                self.assertEqual(set(tools["notion_retrieve"].input_schema["properties"]),
                                 {"workspace_id", "query", "page_id"})
                self.assertEqual(set(tools["document_retrieve"].input_schema["properties"]),
                                 {"workspace_id", "query", "top_k", "max_chars_per_doc"})
                self.assertEqual(set(tools["document_search"].input_schema["properties"]),
                                 {"workspace_id", "query", "limit", "offset"})
                self.assertEqual(set(tools["document_get"].input_schema["properties"]),
                                 {"workspace_id", "document_id", "offset", "max_chars"})

    def test_absent_empty_and_unexpanded_scope_use_demo(self):
        for value in (None, "", "${CONTEXTPACK_WORKSPACE_ID}"):
            with self.subTest(value=value):
                if value is None:
                    os.environ.pop("CONTEXTPACK_WORKSPACE_ID", None)
                else:
                    os.environ["CONTEXTPACK_WORKSPACE_ID"] = value
                self.assertTrue(json.loads(sources.github_search("pr-148")))
                self.assertEqual(self.search("demo")["results"], [])

    def test_non_demo_scope_cannot_read_any_demo_tool_or_other_workspace(self):
        first = self.workspace()
        own_source = self.upload_fixture(first)
        second = self.store.create_workspace()["workspace_id"]
        other_source = self.upload_fixture(second, "github.json")
        for name in DEMO_TOOL_NAMES:
            with self.subTest(tool=name), self.assertRaises(WorkspaceValidationError):
                getattr(sources, name)("v3")
        for workspace_id in (second, "demo", "../outside"):
            with self.subTest(workspace_id=workspace_id), self.assertRaises(WorkspaceValidationError):
                sources.document_search(workspace_id, "v3")
            with self.subTest(workspace_id=workspace_id), self.assertRaises(WorkspaceValidationError):
                sources.document_get(workspace_id, own_source["id"])
        with self.assertRaises(SourceNotFound):
            sources.document_get(first, other_source["id"])
        self.assertEqual([item["document_id"] for item in self.search(first)["results"]], [own_source["id"]])

    def test_invalid_bound_scope_never_falls_back_to_demo(self):
        for value in ("../outside", "DEMO", " ", "${OTHER_WORKSPACE}"):
            with self.subTest(value=value), patch.dict(os.environ, {"CONTEXTPACK_WORKSPACE_ID": value}):
                with self.assertRaises(WorkspaceValidationError):
                    sources.github_search("pr-148")
                with self.assertRaises(WorkspaceValidationError):
                    sources.document_search("demo", "")

    def test_live_github_retrieve_aggregates_registered_repository(self):
        workspace_id = self.workspace()
        self.store.add_github_source(workspace_id, "Ai-pre/mabc2026-contextpack")

        def fake_api(path):
            if path == "/repos/Ai-pre/mabc2026-contextpack":
                return {
                    "description": "Context handoff",
                    "default_branch": "main",
                    "updated_at": "2026-09-20T00:00:00Z",
                    "pushed_at": "2026-09-20T00:00:00Z",
                }
            if "/commits?" in path:
                return [{
                    "sha": "abcdef1234567890",
                    "commit": {
                        "message": "perf: route live GitHub through aggregate MCP retrieval",
                        "author": {"date": "2026-09-20T00:00:00Z"},
                    },
                }]
            if "/pulls?state=all" in path:
                return [{
                    "number": 1,
                    "state": "open",
                    "draft": True,
                    "updated_at": "2026-09-20T00:00:00Z",
                    "merged_at": None,
                    "title": "feat: add live GitHub MCP connector",
                    "body": "MCP deployment changes",
                }]
            if "/pulls/1/files" in path:
                return [{"filename": "backend/main.py", "status": "modified", "additions": 10, "deletions": 2}]
            if path.endswith("/readme"):
                import base64
                return {"content": base64.b64encode(b"# ContextPack\nLive GitHub MCP").decode("ascii")}
            if "/git/trees/" in path:
                return {"tree": [
                    {"path": "README.md"}, {"path": "backend/main.py"},
                    {"path": "deploy/hermes/config.yaml"},
                ]}
            raise AssertionError(path)

        with patch.dict(os.environ, {"GITHUB_MCP_TOKEN": "test-token"}), \
             patch.object(sources, "_github_api", side_effect=fake_api):
            raw = sources.github_retrieve(
                workspace_id,
                "ContextPack MCP deployment changes deploy hermes config",
                "Ai-pre/mabc2026-contextpack",
            )

        response = json.loads(raw)
        self.assertEqual(response["repository"], "Ai-pre/mabc2026-contextpack")
        self.assertEqual(response["recent_commits"][0]["sha"], "abcdef123456")
        self.assertEqual(response["relevant_recent_pull_requests"][0]["number"], 1)
        self.assertEqual(
            response["relevant_recent_pull_requests"][0]["files"][0]["filename"],
            "backend/main.py",
        )
        self.assertIn("deploy/hermes/config.yaml", response["tree_summary"]["query_paths"])
        self.assertIn("Live GitHub MCP", response["readme_preview"])
        self.assertEqual(response["evidence_schema_version"], "1.0")
        self.assertTrue(any(item["kind"] == "commit" for item in response["evidence"]))
        self.assertTrue(any(item["kind"] == "pull_request" for item in response["evidence"]))
        self.assertTrue(all(item["source_type"] == "github" for item in response["evidence"]))

        with patch.dict(os.environ, {"GITHUB_MCP_TOKEN": "test-token"}):
            with self.assertRaises(WorkspaceValidationError):
                sources.github_retrieve(workspace_id, "x", "other/repo")

    def test_live_slack_retrieve_ranks_registered_channel_messages(self):
        workspace_id = self.workspace()
        self.store.add_slack_source(workspace_id, "C012ABCDEF")

        def fake_slack(method, **params):
            if method == "conversations.info":
                return {
                    "ok": True,
                    "channel": {
                        "id": "C012ABCDEF",
                        "name": "payment-eng",
                        "topic": {"value": "Payments"},
                        "purpose": {"value": "Payment engineering"},
                    },
                }
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": [
                        {"ts": "1789830000.000001", "user": "U1",
                         "text": "배포는 내일 진행합니다.", "reply_count": 0},
                        {"ts": "1789830100.000002", "user": "U2",
                         "text": "ContextPack Slack connector 배포 변경 확인 필요", "reply_count": 2},
                    ],
                }
            raise AssertionError(method)

        with patch.object(sources, "_slack_api", side_effect=fake_slack):
            raw = sources.slack_retrieve(
                workspace_id, "Slack connector 배포", "C012ABCDEF"
            )

        response = json.loads(raw)
        self.assertEqual(response["channel"], "C012ABCDEF")
        self.assertEqual(response["channel_name"], "payment-eng")
        self.assertEqual(response["messages"][0]["user"], "U2")
        self.assertIn("Slack connector", response["messages"][0]["text"])
        self.assertEqual(response["evidence_schema_version"], "1.0")
        self.assertEqual(response["evidence"][0]["source_type"], "slack")
        self.assertEqual(response["evidence"][0]["kind"], "message")
        self.assertEqual(response["evidence"][0]["author"], "U2")
        self.assertIn("Slack connector", response["evidence"][0]["content"])

        second = self.store.create_workspace()["workspace_id"]
        os.environ["CONTEXTPACK_WORKSPACE_ID"] = second
        self.store.add_slack_source(second, "C099ZZZZZZ")
        with self.assertRaises(WorkspaceValidationError):
            sources.slack_retrieve(second, "test", "C012ABCDEF")

    def test_live_notion_retrieve_reads_recursive_registered_page(self):
        workspace_id = self.workspace()
        page_id = "12345678-1234-1234-1234-123456789abc"
        self.store.add_notion_source(workspace_id, page_id)

        def rich(text):
            return [{"plain_text": text}]

        def fake_notion(path):
            if path == f"/pages/{page_id}":
                return {
                    "id": page_id,
                    "url": "https://www.notion.so/test",
                    "created_time": "2026-09-01T00:00:00.000Z",
                    "last_edited_time": "2026-09-20T00:00:00.000Z",
                    "properties": {
                        "title": {"type": "title", "title": rich("ContextPack Spec")}
                    },
                }
            if path == f"/blocks/{page_id}/children?page_size=100":
                return {
                    "results": [
                        {
                            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                            "type": "heading_1",
                            "heading_1": {"rich_text": rich("Slack connector")},
                            "has_children": False,
                            "last_edited_time": "2026-09-20T00:00:00.000Z",
                        },
                        {
                            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                            "type": "toggle",
                            "toggle": {"rich_text": rich("배포 체크리스트")},
                            "has_children": True,
                            "last_edited_time": "2026-09-20T00:00:00.000Z",
                        },
                    ],
                    "has_more": False,
                    "next_cursor": None,
                }
            if path == "/blocks/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/children?page_size=100":
                return {
                    "results": [
                        {
                            "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                            "type": "paragraph",
                            "paragraph": {"rich_text": rich("Notion API key 설정 필요")},
                            "has_children": False,
                            "last_edited_time": "2026-09-20T00:00:00.000Z",
                        }
                    ],
                    "has_more": False,
                    "next_cursor": None,
                }
            raise AssertionError(path)

        with patch.object(sources, "_notion_api", side_effect=fake_notion):
            raw = sources.notion_retrieve(
                workspace_id, "Notion API 배포", page_id
            )

        response = json.loads(raw)
        self.assertEqual(response["page_id"], page_id)
        self.assertEqual(response["page_title"], "ContextPack Spec")
        self.assertEqual(response["total_blocks_read"], 3)
        texts = [item["text"] for item in response["blocks"]]
        self.assertIn("Notion API key 설정 필요", texts)
        self.assertEqual(response["evidence_schema_version"], "1.0")
        self.assertTrue(response["evidence"])
        self.assertTrue(all(item["source_type"] == "notion" for item in response["evidence"]))
        self.assertTrue(any("Notion API key 설정 필요" in item["content"] for item in response["evidence"]))

        second = self.store.create_workspace()["workspace_id"]
        os.environ["CONTEXTPACK_WORKSPACE_ID"] = second
        other_page = "aaaaaaaa-1234-1234-1234-123456789abc"
        self.store.add_notion_source(second, other_page)
        with self.assertRaises(WorkspaceValidationError):
            sources.notion_retrieve(second, "test", page_id)

    def test_multi_term_korean_english_search_and_exact_snippet_offsets(self):
        workspace_id = self.workspace()
        uploaded = [self.upload_fixture(workspace_id, filename) for filename in ("notion.md", "github.json", "jira.json")]
        response = self.search(workspace_id, "부분환불 IDEMPOTENCY")
        self.assertEqual(response["total_matches"], 3)
        self.assertNotEqual(response["results"][0]["title"], "notion.md")
        self.assertEqual([item["score"] for item in response["results"]],
                         sorted((item["score"] for item in response["results"]), reverse=True))
        for result in response["results"]:
            document = self.store.get_document(workspace_id, result["document_id"])
            self.assertEqual(result["snippet"], document["body"][result["offset"]:result["offset"] + 400])
            fetched = json.loads(sources.document_get(workspace_id, result["document_id"], result["offset"], 400))
            self.assertEqual(fetched["body"], result["snippet"])
            self.assertEqual(set(result), {"item_id", "document_id", "title", "source_type", "timestamp",
                                           "snippet", "offset", "score"})
        self.assertEqual({item["document_id"] for item in response["results"]}, {item["id"] for item in uploaded})
        self.assertEqual(self.search(workspace_id, "unfindablequeryxyz")["total_matches"], 0)

    def test_document_retrieve_combines_search_and_bounded_reads(self):
        workspace_id = self.workspace()
        uploaded = [self.upload_fixture(workspace_id, filename)
                    for filename in ("notion.md", "github.json", "jira.json")]
        response = json.loads(sources.document_retrieve(
            workspace_id, "부분환불 IDEMPOTENCY", top_k=2, max_chars_per_doc=1200
        ))
        self.assertEqual(response["total_matches"], 3)
        self.assertEqual(len(response["results"]), 2)
        self.assertTrue(all(item["body"] for item in response["results"]))
        self.assertTrue(all(len(item["body"]) <= 1200 for item in response["results"]))
        self.assertTrue({item["document_id"] for item in response["results"]}
                        .issubset({item["id"] for item in uploaded}))
        self.assertEqual(set(response["results"][0]),
                         {"document_id", "title", "timestamp", "score", "offset",
                          "body", "next_offset", "truncated"})
        self.assertEqual(response["evidence_schema_version"], "1.0")
        self.assertEqual(len(response["evidence"]), len(response["results"]))
        self.assertTrue(all(item["source_type"] == "document" for item in response["evidence"]))
        self.assertTrue(all(item["kind"] == "document_excerpt" for item in response["evidence"]))

    def test_unicode_casefold_offsets_still_use_original_characters(self):
        # Use existing fixture text; helper offsets also cover case-fold expansions without storing test documents.
        self.assertEqual(sources._original_offset("ß" * 100 + "환불", 200), 100)
        self.assertEqual(sources._original_offset("İ" + "refund", 2), 1)

    def test_search_result_pagination_and_removed_documents(self):
        workspace_id = self.workspace()
        originals = [self.upload_fixture(workspace_id, filename) for filename in ("notion.md", "github.json", "jira.json")]
        result_ids = []
        offset = 0
        while offset is not None:
            page = self.search(workspace_id, limit=1, offset=offset)
            self.assertEqual(page["total_matches"], 3)
            self.assertEqual(page["results"][0]["snippet"], "")
            result_ids.extend(item["document_id"] for item in page["results"])
            offset = page["next_offset"]
        self.assertEqual(set(result_ids), {source["id"] for source in originals})
        self.assertEqual(len(result_ids), 3)
        self.assertEqual(self.search(workspace_id, offset=3)["results"], [])
        with self.assertRaises(WorkspaceValidationError):
            self.search(workspace_id, offset=4)
        self.store.remove_source(workspace_id, originals[0]["id"])
        self.assertEqual(self.search(workspace_id)["total_matches"], 2)
        with self.assertRaises(SourceNotFound):
            sources.document_get(workspace_id, originals[0]["id"])
        # A source in another store directory cannot become visible through its identifier.
        with self.assertRaises(SourceNotFound):
            sources.document_get(workspace_id, "../../manifest")

    def test_get_body_pagination_preserves_metadata_and_warnings(self):
        workspace_id = self.workspace()
        source = self.upload_fixture(workspace_id, "jira.json")
        original = self.store.get_document(workspace_id, source["id"])
        parts = []
        offset = 0
        while offset is not None:
            response = json.loads(sources.document_get(workspace_id, source["id"], offset, 250))
            self.assertEqual(response["total_chars"], len(original["body"]))
            self.assertEqual(response["offset"], offset)
            self.assertEqual(response["metadata"]["original_filename"], "jira.json")
            self.assertTrue(response["truncated"])
            for block in response["metadata"]["blocks"]:
                self.assertLess(block["start"], offset + len(response["body"]))
                self.assertGreater(block["end"], offset)
            parts.append(response["body"])
            offset = response["next_offset"]
        self.assertEqual("".join(parts), original["body"])
        full = json.loads(sources.document_get(workspace_id, source["id"], max_chars=20000))
        self.assertFalse(full["truncated"])
        self.assertIsNone(full["next_offset"])
        self.assertEqual(full["body"], original["body"])
        ending = json.loads(sources.document_get(workspace_id, source["id"], len(original["body"])))
        self.assertEqual(ending["body"], "")
        self.assertIsNone(ending["next_offset"])
        with self.assertRaises(WorkspaceValidationError):
            sources.document_get(workspace_id, source["id"], len(original["body"]) + 1)
        # Metadata filtering uses a read mock, leaving the fixture corpus unchanged.
        metadata = {**original["metadata"], "warnings": [{"code": "pdf_pages_without_text", "pages": [2]}],
                    "pages": [{"page": 1, "start": 0, "end": 100}, {"page": 3, "start": 102, "end": 300}]}
        with patch.object(self.store, "get_document", return_value={**original, "metadata": metadata}):
            response = json.loads(sources.document_get(workspace_id, source["id"], 120, 30))
        self.assertEqual(response["metadata"]["warnings"], metadata["warnings"])
        self.assertEqual(response["metadata"]["pages"], [metadata["pages"][1]])

    def test_invalid_document_tool_arguments_are_rejected(self):
        workspace_id = self.workspace()
        source = self.upload_fixture(workspace_id)
        for arguments in ({"query": "x" * 501}, {"query": "\x00"}, {"query": "!!!"}, {"query": None},
                          {"limit": 0}, {"limit": 51}, {"limit": True}, {"offset": -1}, {"offset": 0.5}):
            with self.subTest(arguments=arguments), self.assertRaises(WorkspaceValidationError):
                sources.document_search(workspace_id, **{"query": "", **arguments})
        for arguments in ({"max_chars": 0}, {"max_chars": 20001}, {"max_chars": True},
                          {"offset": -1}, {"offset": "0"}):
            with self.subTest(arguments=arguments), self.assertRaises(WorkspaceValidationError):
                sources.document_get(workspace_id, source["id"], **arguments)
        for arguments in ({"top_k": 0}, {"top_k": 6}, {"top_k": True},
                          {"max_chars_per_doc": 499}, {"max_chars_per_doc": 6001}):
            with self.subTest(arguments=arguments), self.assertRaises(WorkspaceValidationError):
                sources.document_retrieve(workspace_id, "refund", **arguments)

    def test_actual_stdio_tool_listing_retrieval_and_scope_errors(self):
        workspace_id = self.workspace()
        source = self.upload_fixture(workspace_id)
        second_workspace = self.store.create_workspace()["workspace_id"]
        bootstrap = (
            "import sys; from pathlib import Path; import mabc_mcp_server as module; "
            "from backend.workspace_store import WorkspaceStore; "
            "module._STORE = WorkspaceStore(Path(sys.argv[1])); module.server.run()"
        )

        async def exercise():
            parameters = StdioServerParameters(
                command=sys.executable, args=["-c", bootstrap, str(self.store.root)],
                env={**os.environ, "CONTEXTPACK_WORKSPACE_ID": workspace_id}, cwd=PROJECT_ROOT,
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    listed = await client.list_tools()
                    self.assertEqual({tool.name for tool in listed.tools}, TOOL_NAMES)
                    result = await client.call_tool("document_retrieve", {
                        "workspace_id": workspace_id, "query": "부분환불", "top_k": 1
                    })
                    self.assertFalse(result.is_error)
                    response = json.loads(result.content[0].text)
                    self.assertEqual(response["results"][0]["document_id"], source["id"])
                    self.assertTrue(response["results"][0]["body"])
                    result = await client.call_tool("document_search", {"workspace_id": workspace_id, "query": "부분환불"})
                    self.assertFalse(result.is_error)
                    response = json.loads(result.content[0].text)
                    self.assertEqual(response["results"][0]["document_id"], source["id"])
                    result = await client.call_tool("document_get", {"workspace_id": workspace_id, "document_id": source["id"]})
                    self.assertFalse(result.is_error)
                    self.assertEqual(json.loads(result.content[0].text)["workspace_id"], workspace_id)
                    denied = await client.call_tool("github_search", {"query": "pr-148"})
                    self.assertTrue(denied.is_error)
                    denied = await client.call_tool("document_search", {"workspace_id": second_workspace, "query": ""})
                    self.assertTrue(denied.is_error)

        asyncio.run(asyncio.wait_for(exercise(), timeout=30))


if __name__ == "__main__":
    unittest.main()
