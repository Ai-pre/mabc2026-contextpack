"""Exercise the analysis boundary without starting an HTTP server or an LLM run."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as api
from backend.document_parser import parse_document
from backend.workspace_store import WorkspaceStore
from backend.hermes_runner import CliHermesRunner, HermesExecutionError, HermesTimeoutError

ROOT = Path(__file__).resolve().parent.parent


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="analysis_test_")
        self.store = WorkspaceStore(Path(self.temp.name))
        self.store_patch = patch.object(api, "workspace_store", self.store)
        self.store_patch.start()
        self.client = TestClient(api.app)
        # Reuse the existing saved demo response; it is only a test double for the CLI boundary.
        saved = json.loads((ROOT / "response.json").read_text(encoding="utf-8-sig"))
        self.result = SimpleNamespace(duration_sec=saved["duration_sec"], handoff=saved["handoff"],
                                      skill_used=saved["trace"]["skill_used"],
                                      mcp_tool_calls=saved["trace"]["mcp_tool_calls"],
                                      mcp_tools=saved["trace"].get("mcp_tools", []))

    def tearDown(self):
        self.client.close()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_legacy_request_resolves_demo_and_preserves_trace_shape(self):
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={"role": "Developer", "task": "Modify partial refunds"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["workspace_id"], "demo")
        self.assertEqual(data["handoff"], self.result.handoff)
        self.assertEqual(data["duration_sec"], self.result.duration_sec)
        self.assertEqual(data["trace"], {
            "skill_used": self.result.skill_used,
            "mcp_tool_calls": self.result.mcp_tool_calls,
            "mcp_tools": self.result.mcp_tools,
        })
        self.assertEqual(run.call_args.kwargs, {"workspace_id": "demo", "github_enabled": False})
        prompt = run.call_args.args[0]
        self.assertIn("demo_context_retrieve", prompt)
        self.assertIn("충돌한 fact의 어느 한쪽 값도 MUST KNOW/CONSTRAINTS에 확정 사실로 쓰지 않는다", prompt)

    def test_uploaded_workspace_passes_scope_without_raw_documents(self):
        workspace_id = self.store.create_workspace("Upload test")["workspace_id"]
        raw = (ROOT / "data/demo_project/notion.md").read_bytes()
        self.store.add_source(workspace_id, "architecture.md", raw, parse_document("architecture.md", raw))
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={"workspace_id": workspace_id, "role": "Reviewer", "task": "Review changes"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(run.call_args.kwargs, {"workspace_id": workspace_id, "github_enabled": False})
        prompt = run.call_args.args[0]
        self.assertIn(workspace_id, prompt)
        self.assertIn("document_retrieve", prompt)
        self.assertIn("document_search", prompt)
        self.assertNotIn("github_search", prompt)
        self.assertNotIn(raw.decode("utf-8").strip(), prompt)
        self.assertEqual(self.store.get_workspace(workspace_id)["task"], "Review changes")

    def test_connected_github_repo_is_scoped_in_agent_prompt(self):
        workspace_id = self.store.create_workspace("GitHub test")["workspace_id"]
        self.store.add_github_source(workspace_id, "Ai-pre/mabc2026-contextpack")
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={
                "workspace_id": workspace_id,
                "role": "Developer",
                "task": "Review the latest repository changes",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(run.call_args.kwargs, {"workspace_id": workspace_id, "github_enabled": True})
        prompt = run.call_args.args[0]
        self.assertIn("Ai-pre/mabc2026-contextpack", prompt)
        self.assertIn("github_retrieve", prompt)
        self.assertIn("aggregate", prompt)
        self.assertIn("다른 repository를 근거로 사용하지 않는다", prompt)

    def test_slack_only_workspace_forces_one_retrieve_then_stop(self):
        workspace_id = self.store.create_workspace("Slack only")["workspace_id"]
        self.store.add_slack_source(workspace_id, "C012ABCDEF")
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={
                "workspace_id": workspace_id,
                "role": "Backend Developer",
                "task": "Summarize recent project decisions",
            })
        self.assertEqual(response.status_code, 200, response.text)
        prompt = run.call_args.args[0]
        self.assertIn("이 Workspace는 Slack-only다", prompt)
        self.assertIn("slack_retrieve를 필요한 channel당 정확히 1회", prompt)
        self.assertIn("다른 MCP tool을 탐색하거나 재호출하지 않는다", prompt)
        self.assertNotIn("demo_context_retrieve", prompt)
        self.assertNotIn("slack_search/get", prompt)

    def test_connected_slack_and_notion_sources_are_scoped_in_agent_prompt(self):
        workspace_id = self.store.create_workspace("Connector test")["workspace_id"]
        self.store.add_slack_source(workspace_id, "C012ABCDEF")
        self.store.add_notion_source(
            workspace_id,
            "12345678-1234-1234-1234-123456789abc",
        )
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={
                "workspace_id": workspace_id,
                "role": "Developer",
                "task": "Review the rollout discussion and spec",
            })
        self.assertEqual(response.status_code, 200, response.text)
        prompt = run.call_args.args[0]
        self.assertIn("C012ABCDEF", prompt)
        self.assertIn("12345678-1234-1234-1234-123456789abc", prompt)
        self.assertIn("slack_retrieve", prompt)
        self.assertIn("notion_retrieve", prompt)
        self.assertIn("등록되지 않은 채널", prompt)
        self.assertIn("등록되지 않은 페이지", prompt)

    def test_runtime_prompt_distinguishes_tentative_from_final_decisions(self):
        workspace_id = self.store.create_workspace("Slack finality")["workspace_id"]
        self.store.add_slack_source(workspace_id, "C012ABCDEF")
        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={
                "workspace_id": workspace_id,
                "role": "Backend Developer",
                "task": "Summarize deployment decisions",
            })
        self.assertEqual(response.status_code, 200, response.text)
        prompt = run.call_args.args[0]
        self.assertIn("논의 중/검토 중/제안/초안/예정/후보", prompt)
        self.assertIn("최종 결정/확정/승인/적용 결정/취소", prompt)
        self.assertIn("둘은 CONFLICT가 아니다", prompt)
        self.assertIn("Finality 보존", prompt)
        self.assertIn("일반적인 가능성만으로 그 결정을 VERIFY BEFORE USE에 다시 넣지 않는다", prompt)
        self.assertIn("그 topic은 VERIFY BEFORE USE에서 다시 언급하지 않는다", prompt)
        self.assertIn("VERIFY BEFORE USE는 다른 검증 필요 사실이 없으면 반드시", prompt)
        self.assertIn("확인되지 않은 세부사항만 DO NOT ASSUME", prompt)
        self.assertIn("final 결정에 의해 명시적으로 대체된 tentative/candidate 안은 MISSING이 아니다", prompt)
        self.assertIn("가상의 가능성을 새 MISSING으로 만들지 않는다", prompt)
        self.assertIn("Retrieval mechanics ≠ Handoff context", prompt)
        self.assertIn("공통 Evidence Contract", prompt)
        self.assertIn("evidence_schema_version", prompt)
        self.assertIn("source_type마다 별도의 Handoff 품질 규칙을 만들지 않는다", prompt)
        self.assertIn("ContextPack 내부 retrieval 제약은 넣지 않는다", prompt)
        self.assertIn("보편적인 불확실성/면책 문구는 MISSING이 아니므로", prompt)
        self.assertIn("정확한 MCP callable identifier", prompt)
        self.assertIn("reopening signal", prompt)

    def test_handoff_sanitizer_removes_retrieval_noise_and_superseded_verify(self):
        raw = """[TASK]
- Slack 논의 정리
[MUST KNOW]
- 다음 배포는 토요일로 최종 결정됐다.
[CONSTRAINTS]
- workspace_id ws_x에 등록된 Slack 채널의 slack_retrieve 결과로만 제한한다.
- 연결된 live Notion page가 없으므로 notion_retrieve는 사용하지 않는다.
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- "금요일로 논의 중" 메시지가 있었지만 이후 같은 발화자가 "토요일로 최종 결정"이라고 확정했다. 금요일 일정은 확정이 아니다.
[DO NOT ASSUME]
- 위 메시지 외에 채널 내 추가 논의가 있는지는 본 retrieve 결과에서 확인되지 않았다.
[SOURCE MAP]
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C012ABCDEF)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("[CONSTRAINTS]\n- None", cleaned)
        self.assertIn("[VERIFY BEFORE USE]\n- None", cleaned)
        self.assertIn("[DO NOT ASSUME]\n- None", cleaned)
        self.assertIn("토요일로 최종 결정", cleaned)
        self.assertIn("mcp__mabc_sources__slack_retrieve", cleaned)
        self.assertNotIn("위 메시지 외에 채널 내 추가 논의", cleaned)

    def test_handoff_sanitizer_drops_superseded_tentative_from_do_not_assume(self):
        raw = """[TASK]
- 프로젝트 결정 정리
[MUST KNOW]
- 릴리스 일정은 토요일로 최종 결정됐다.
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- 토요일 릴리스의 정확한 시각, 담당자, 대상 환경은 현재 근거에서 확인되지 않았다.
- "금요일로 논의 중"이라는 후보안은 토요일 최종 결정 전에 있었던 내용이며, 현재 상태는 토요일이다.
[SOURCE MAP]
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C012ABCDEF)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("정확한 시각, 담당자, 대상 환경", cleaned)
        self.assertNotIn("금요일로 논의 중", cleaned)
        self.assertIn("[DO NOT ASSUME]\n- 토요일 릴리스의 정확한 시각", cleaned)

    def test_mcp_tool_trace_prefers_full_identifier_from_source_map(self):
        stdout = """⚡ mcp__mabc
[TASK]
- x
[MUST KNOW]
- y
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C012ABCDEF)
"""
        self.assertEqual(CliHermesRunner._count_mcp_calls(stdout), 1)
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(stdout),
            ["mcp__mabc_sources__slack_retrieve"],
        )

    def test_mcp_tool_trace_recovers_slack_leaf_from_truncated_trace(self):
        stdout = """⚡ mcp__mabc
[TASK]
- x
[MUST KNOW]
- Slack retrieval은 search/get 대신 slack_retrieve 한 번으로 가져온다.
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- Slack channel C012ABCDEF, message ts 1789840639.185659
"""
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(stdout),
            ["mcp__mabc_sources__slack_retrieve"],
        )

    def test_invalid_or_empty_workspace_never_launches_runner(self):
        empty = self.store.create_workspace()["workspace_id"]
        requests = [({}, 400), ({"role": 123, "task": "x"}, 400),
                    ({"role": "  ", "task": "x"}, 400),
                    ({"role": "r", "task": "t", "workspace_id": "../outside"}, 400),
                    ({"role": "r", "task": "t", "workspace_id": "ws_" + "0" * 32}, 404),
                    ({"role": "r", "task": "t", "workspace_id": empty}, 400)]
        with patch.object(api.runner, "run") as run:
            for body, status in requests:
                with self.subTest(body=body):
                    response = self.client.post("/analyze", json=body)
                    self.assertEqual(response.status_code, status, response.text)
            response = self.client.post("/analyze", content="{", headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
            run.assert_not_called()

    def test_runtime_failures_keep_useful_status_and_detail(self):
        for error, status in ((HermesTimeoutError("Run timed out"), 504), (HermesExecutionError("CLI failed"), 502)):
            with self.subTest(status=status), patch.object(api.runner, "run", side_effect=error):
                response = self.client.post("/analyze", json={"role": "r", "task": "t"})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["detail"], str(error))

    def test_mcp_tool_trace_extracts_exact_tool_names(self):
        trace = """⚡ mcp__mabc_sources__document_retrieve
some output
⚡ mcp__github_live__get_file_contents
⚡ mcp__mabc_sources__document_get
"""
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(trace),
            [
                "mcp__mabc_sources__document_retrieve",
                "mcp__github_live__get_file_contents",
                "mcp__mabc_sources__document_get",
            ],
        )
        self.assertEqual(CliHermesRunner._count_mcp_calls(trace), 3)

    def test_handoff_parser_allows_mcp_tool_name_in_source_map(self):
        stdout = """[TASK]
- 결제 모듈의 부분환불 기능 수정

[MUST KNOW]
- PG API v3 사용.

[CONSTRAINTS]
- None

[USEFUL IF SPACE ALLOWS]
- None

[UNRESOLVED CONFLICTS]
- None

[VERIFY BEFORE USE]
- None

[DO NOT ASSUME]
- None

[SOURCE MAP]
- retrieval: mcp__mabc_sources__demo_context_retrieve
"""
        parsed = CliHermesRunner._extract_handoff(stdout)
        self.assertTrue(parsed.startswith("[TASK]"))
        self.assertIn("mcp__mabc_sources__demo_context_retrieve", parsed)

    def test_handoff_parser_cuts_runtime_trace_after_final_answer(self):
        stdout = """[TASK]
- x

[MUST KNOW]
- y

[CONSTRAINTS]
- None

[USEFUL IF SPACE ALLOWS]
- None

[UNRESOLVED CONFLICTS]
- None

[VERIFY BEFORE USE]
- None

[DO NOT ASSUME]
- None

[SOURCE MAP]
- github PR #152

⚡ mcp__mabc_sources__demo_context_retrieve
Resume this session with:
  hermes --resume abc
"""
        parsed = CliHermesRunner._extract_handoff(stdout)
        self.assertIn("- github PR #152", parsed)
        self.assertNotIn("⚡", parsed)
        self.assertNotIn("Resume this session", parsed)

    def test_runner_rejects_source_free_handoff(self):
        stdout = """[TASK]
- x

[MUST KNOW]
- x

[CONSTRAINTS]
- None

[USEFUL IF SPACE ALLOWS]
- None

[UNRESOLVED CONFLICTS]
- None

[VERIFY BEFORE USE]
- None

[DO NOT ASSUME]
- None

[SOURCE MAP]
- None
"""
        with patch("hermes_runner.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")):
            with self.assertRaises(HermesExecutionError) as caught:
                api.runner.run("Test prompt", workspace_id="demo")
        self.assertIn("without reading any registered Source", str(caught.exception))

    def test_cli_command_keeps_context_pack_and_isolates_child_environment(self):
        stdout = (ROOT / "cli_success_stdout.txt").read_text(encoding="utf-8-sig")
        scope = "ws_" + "a" * 32
        previous = os.environ.get("CONTEXTPACK_WORKSPACE_ID")
        with patch("hermes_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")) as launch:
            result = api.runner.run("Test prompt", workspace_id=scope)
        args = launch.call_args.args[0]
        self.assertEqual(args[1:5], ["chat", "--oneshot", "--skills", "context-pack"])
        self.assertNotIn("--toolsets", args)
        self.assertEqual(args[args.index("--max-turns") + 1], "8")
        self.assertEqual(launch.call_args.kwargs["env"]["CONTEXTPACK_WORKSPACE_ID"], scope)
        self.assertEqual(os.environ.get("CONTEXTPACK_WORKSPACE_ID"), previous)
        self.assertTrue(result.handoff.startswith("[TASK]"))
        self.assertEqual(result.skill_used, CliHermesRunner._detect_skill(CliHermesRunner._clean_output(stdout)))
        self.assertEqual(result.mcp_tool_calls, CliHermesRunner._count_mcp_calls(CliHermesRunner._clean_output(stdout)))


if __name__ == "__main__":
    unittest.main()
