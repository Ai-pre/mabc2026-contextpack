"""Exercise the analysis boundary without starting an HTTP server or an LLM run."""
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
        # Keep the API boundary test self-contained. Do not depend on an
        # untracked/local response.json being present in the Docker image.
        handoff = """[TASK]
- Test task
[MUST KNOW]
- Test evidence
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
- mcp__mabc_sources__demo_context_retrieve(query=test)
"""
        self.result = SimpleNamespace(
            duration_sec=1.25,
            handoff=handoff,
            skill_used=True,
            mcp_tool_calls=1,
            mcp_tools=["mcp__mabc_sources__demo_context_retrieve"],
            retrieval_timing_ms={"aggregate": 250.0},
        )

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
            "retrieval_timing_ms": self.result.retrieval_timing_ms,
        })
        self.assertEqual(run.call_args.kwargs, {"workspace_id": "demo", "github_enabled": False})
        prompt = run.call_args.args[0]
        self.assertIn("demo_context_retrieve", prompt)
        self.assertIn("context-pack Skill을 single source of truth", prompt)
        self.assertLess(len(prompt), 5000)

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
        self.assertNotIn("document_search", prompt)
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
        self.assertIn("GitHub-only", prompt)
        self.assertIn("github_retrieve", prompt)
        self.assertIn("Ai-pre/mabc2026-contextpack", prompt)
        self.assertIn("등록된 Workspace Source만 근거로 사용", prompt)

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
        self.assertIn("slack_retrieve", prompt)
        self.assertIn("정확히 1회 호출하고 즉시 Handoff", prompt)
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
        self.assertIn("workspace_retrieve", prompt)
        self.assertIn("source_types", prompt)
        self.assertIn("C012ABCDEF", prompt)
        self.assertIn("12345678-1234-1234-1234-123456789abc", prompt)
        self.assertIn("정확히 1회 호출", prompt)
        self.assertNotIn("live GitHub repository가 연결되어 있지 않다", prompt)

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
        self.assertIn("context-pack Skill을 single source of truth", prompt)
        self.assertIn("finality preflight", prompt)
        self.assertIn("tentative→명시적 final 대체는 conflict가 아니고", prompt)
        self.assertIn("Source silence도 conflict가 아니다", prompt)
        self.assertIn("final↔final만 UNRESOLVED CONFLICTS", prompt)
        self.assertIn("실제 reopening evidence가 없으면", prompt)
        self.assertIn("workspace/source 연결 상태와 tool scope는 semantic section에서 제외", prompt)
        self.assertIn("정확한 MCP callable identifier", prompt)
        self.assertIn("numbered report", prompt)
        self.assertIn("[SCRIPT_MAP]", prompt)
        self.assertNotIn("## 확인 요구사항", prompt)
        self.assertNotIn("## 공통 Evidence Contract", prompt)
        self.assertLess(len(prompt), 5000)

    def test_context_pack_skill_has_single_exact_output_contract(self):
        skill_text = (ROOT / "deploy/hermes/skills/context-pack/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("### 최종 Handoff Context 형식 (고정)", skill_text)
        self.assertIn("[TASK]", skill_text)
        self.assertIn("[SOURCE MAP]", skill_text)
        self.assertNotIn("\n## 최종 출력 형식\n", skill_text)
        self.assertNotIn("## 1. Task", skill_text)
        self.assertNotIn("[SCRIPT_MAP]", skill_text)
        self.assertNotIn("[호출 MCP tool]", skill_text)

    def test_runtime_prompt_hard_stops_with_one_aggregate_call_in_multi_source_workspace(self):
        workspace_id = self.store.create_workspace("GitHub + Slack")["workspace_id"]
        self.store.add_github_source(workspace_id, "Ai-pre/mabc2026-contextpack")
        self.store.add_slack_source(workspace_id, "C012ABCDEF")

        with patch.object(api.runner, "run", return_value=self.result) as run:
            response = self.client.post("/analyze", json={
                "workspace_id": workspace_id,
                "role": "Backend Developer",
                "task": "Summarize recent MCP and deployment decisions from GitHub and Slack",
            })

        self.assertEqual(response.status_code, 200, response.text)
        prompt = run.call_args.args[0]
        self.assertIn("workspace_retrieve", prompt)
        self.assertIn("source_types", prompt)
        self.assertIn("Workspace의 MCP source surface는 workspace_retrieve 하나", prompt)
        self.assertIn("workspace_retrieve(workspace_id, query, source_types)", prompt)
        self.assertIn("정확히 1회 호출", prompt)
        self.assertIn("추가 retrieval 없이 즉시 Handoff", prompt)
        self.assertNotIn("Finality 보존 규칙", prompt)
        self.assertLess(len(prompt), 5000)

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

    def test_handoff_sanitizer_removes_current_analysis_workspace_metadata_across_sections(self):
        raw = """[TASK]
- 최근 MCP 및 배포 변경 정리
[MUST KNOW]
- PR #2는 open draft이며 Slack/Notion connector 변경을 포함한다.
- 이 workspace는 현재 GitHub-only이며 live Slack/Notion은 연결되지 않았다.
[CONSTRAINTS]
- PR #2는 아직 open draft이므로 현재 적용 완료 상태로 간주하지 않는다.
- 이 workspace에 Slack/Notion source가 연결되어 있지 않다.
[USEFUL IF SPACE ALLOWS]
- README에는 retrieve-first architecture가 설명되어 있다.
- 현재 workspace에서는 github_retrieve만 유효한 근거다.
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- PR #2는 open draft이므로 실제 운영 반영 여부를 확정하지 않는다.
- 현재 이 workspace에 Slack source가 없으므로 Slack 적용 여부를 확인할 수 없다.
[DO NOT ASSUME]
- Cloud Run 실제 대상 환경과 trigger는 commit message만으로 확정하지 않는다.
- Slack/Notion이 이 workspace에서 현재 사용 가능한지는 추정하지 않는다.
[SOURCE MAP]
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("PR #2는 open draft", cleaned)
        self.assertIn("Cloud Run 실제 대상 환경", cleaned)
        self.assertIn("README에는 retrieve-first architecture", cleaned)
        self.assertNotIn("GitHub-only", cleaned)
        self.assertNotIn("현재 workspace에서는 github_retrieve만", cleaned)
        self.assertNotIn("현재 이 workspace에 Slack source", cleaned)
        self.assertNotIn("Slack/Notion이 이 workspace에서 현재 사용 가능한지", cleaned)

    def test_handoff_sanitizer_strips_runtime_registration_clause_but_keeps_project_fact(self):
        raw = """[TASK]
- MCP 결정 정리
[MUST KNOW]
- Slack connector 관련: Workspace에는 라이브 Slack 채널 C012ABCDEF가 등록되어 있으며 retrieval은 slack_retrieve 단일 호출로 수행한다.
[CONSTRAINTS]
- live Notion page는 현재 연결되어 있지 않다. notion_retrieve를 사용하지 않는다.
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__workspace_retrieve(workspace_id=ws_x, source_types=github,slack)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("retrieval은 slack_retrieve 단일 호출로 수행한다", cleaned)
        self.assertNotIn("C012ABCDEF가 등록되어", cleaned)
        self.assertIn("[CONSTRAINTS]\n- None", cleaned)
        self.assertNotIn("live Notion page는 현재 연결되어 있지", cleaned)

    def test_handoff_sanitizer_removes_source_allowlist_constraints(self):
        raw = """[TASK]
- 최근 변경 정리
[MUST KNOW]
- PR #1은 merged됐다.
[CONSTRAINTS]
- GitHub 근거는 연결된 repository(Ai-pre/mabc2026-contextpack)만 사용하며, 다른 repository를 근거로 쓰지 않는다.
- Slack 근거는 등록된 channel만 사용한다.
- Notion 근거는 연결된 page만 사용한다.
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=Ai-pre/mabc2026-contextpack)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("[CONSTRAINTS]\n- None", cleaned)
        self.assertNotIn("다른 repository를 근거로 쓰지", cleaned)
        self.assertNotIn("등록된 channel만 사용", cleaned)
        self.assertNotIn("연결된 page만 사용", cleaned)

    def test_handoff_sanitizer_drops_source_silence_as_conflict(self):
        raw = """[TASK]
- 배포 결정 정리
[MUST KNOW]
- Slack에서 다음 배포는 토요일로 최종 결정됐다.
- 별도 충돌 근거는 확인되지 않았다.
[CONSTRAINTS]
- Slack 근거는 한 개 메시지에만 존재하며 다른 채널은 확인하지 않았다.
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- Slack의 토요일 최종 결정은 GitHub에서 확인되지 않아 교차 검증이 되지 않는다. 따라서 확정 여부를 GitHub 근거로 검증할 수 없다.
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C1)
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("토요일로 최종 결정", cleaned)
        self.assertIn("[UNRESOLVED CONFLICTS]\n- None", cleaned)
        self.assertIn("[CONSTRAINTS]\n- None", cleaned)
        self.assertNotIn("별도 충돌 근거는 확인되지", cleaned)
        self.assertNotIn("교차 검증이 되지 않는다", cleaned)

    def test_handoff_sanitizer_preserves_true_cross_source_conflict(self):
        raw = """[TASK]
- 배포 결정 정리
[MUST KNOW]
- None
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- Slack은 토요일로 최종 결정했고 GitHub release note는 일요일로 최종 확정해 서로 충돌한다.
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C1)
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("토요일로 최종 결정", cleaned)
        self.assertIn("일요일로 최종 확정", cleaned)
        self.assertNotIn("[UNRESOLVED CONFLICTS]\n- None", cleaned)

    def test_handoff_sanitizer_drops_hypothetical_reopening_gap(self):
        raw = """[TASK]
- 배포 결정 정리
[MUST KNOW]
- 다음 배포는 토요일로 최종 결정됐다.
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- None
[DO NOT ASSUME]
- 토요일 배포의 정확한 실행 시각과 대상 환경은 현재 근거에 없다.
- Slack에서 언급된 최종 결정 외에 추가 재조정/번복이 있었는지 여부.
[SOURCE MAP]
- mcp__mabc_sources__workspace_retrieve(workspace_id=ws_x, source_types=github,slack)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("정확한 실행 시각과 대상 환경", cleaned)
        self.assertNotIn("추가 재조정/번복이 있었는지 여부", cleaned)

    def test_handoff_sanitizer_keeps_real_reopening_evidence(self):
        raw = """[TASK]
- 배포 결정 정리
[MUST KNOW]
- None
[CONSTRAINTS]
- None
[USEFUL IF SPACE ALLOWS]
- None
[UNRESOLVED CONFLICTS]
- None
[VERIFY BEFORE USE]
- 토요일 최종 결정 이후 Slack에서 일정 재검토를 시작했다는 메시지가 확인됐다.
[DO NOT ASSUME]
- None
[SOURCE MAP]
- mcp__mabc_sources__workspace_retrieve(workspace_id=ws_x, source_types=github,slack)
"""
        cleaned = CliHermesRunner._sanitize_handoff(raw)
        self.assertIn("일정 재검토를 시작했다는 메시지가 확인됐다", cleaned)

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
        handoff = CliHermesRunner._extract_handoff(stdout)
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(stdout, handoff=handoff),
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
        handoff = CliHermesRunner._extract_handoff(stdout)
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(stdout, handoff=handoff),
            ["mcp__mabc_sources__slack_retrieve"],
        )

    def test_mcp_trace_supports_workspace_retrieve(self):
        trace = "⚡ mcp__mabc_sources__workspace_retrieve\n"
        self.assertEqual(CliHermesRunner._count_mcp_calls(trace), 1)
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(trace),
            ["mcp__mabc_sources__workspace_retrieve"],
        )

    def test_mcp_trace_ignores_prompt_examples_not_actually_called(self):
        trace = """prompt example: mcp__mabc_sources__slack_retrieve(...)
⚡ mcp__mabc
"""
        handoff = """[TASK]
- GitHub 변경 정리
[MUST KNOW]
- PR #1 merged
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
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
"""
        self.assertEqual(
            CliHermesRunner._extract_mcp_tools(trace, handoff=handoff),
            ["mcp__mabc_sources__github_retrieve"],
        )

    def test_handoff_parser_rejects_placeholder_template(self):
        stdout = """[TASK]
- ...

[MUST KNOW]
- ...

[CONSTRAINTS]
- None

[USEFUL IF SPACE ALLOWS]
- ...

[UNRESOLVED CONFLICTS]
- None

[VERIFY BEFORE USE]
- ...

[DO NOT ASSUME]
- ...

[SOURCE MAP]
- source
"""
        self.assertEqual(CliHermesRunner._extract_handoff(stdout), "")

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

    def test_runner_accepts_final_handoff_emitted_on_stderr(self):
        stderr = """[TASK]
- Cross-source 정리
[MUST KNOW]
- GitHub와 Slack 근거를 종합했다.
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
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
"""
        stdout = "⚡ mcp__mabc_sources__github_retrieve\n"
        with patch(
            "backend.hermes_runner.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr),
        ):
            result = api.runner.run("Test prompt", workspace_id="demo")
        self.assertIn("GitHub와 Slack 근거를 종합했다", result.handoff)
        self.assertEqual(result.mcp_tool_calls, 1)

    def test_runner_recovers_complete_handoff_after_iteration_budget(self):
        stdout = """⚡ mcp__mabc_sources__github_retrieve
⚡ mcp__mabc_sources__slack_retrieve
[TASK]
- Cross-source 정리
[MUST KNOW]
- 토요일 배포가 최종 결정됐다.
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
- mcp__mabc_sources__github_retrieve(workspace_id=ws_x, repository=owner/repo)
- mcp__mabc_sources__slack_retrieve(workspace_id=ws_x, channel=C1)

⚠ Iteration budget reached (8/8) — response may be incomplete
"""
        with patch(
            "backend.hermes_runner.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout=stdout, stderr=""),
        ):
            result = api.runner.run("Test prompt", workspace_id="demo")
        self.assertIn("토요일 배포가 최종 결정", result.handoff)
        self.assertEqual(result.mcp_tool_calls, 2)
        self.assertEqual(
            result.mcp_tools,
            [
                "mcp__mabc_sources__github_retrieve",
                "mcp__mabc_sources__slack_retrieve",
            ],
        )

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
        with patch("backend.hermes_runner.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")):
            with self.assertRaises(HermesExecutionError) as caught:
                api.runner.run("Test prompt", workspace_id="demo")
        self.assertIn("without reading any registered Source", str(caught.exception))

    def test_cli_command_keeps_context_pack_and_isolates_child_environment(self):
        stdout = """⚡ mcp__mabc_sources__demo_context_retrieve
[TASK]
- Test task

[MUST KNOW]
- Test evidence

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
- mcp__mabc_sources__demo_context_retrieve(query=test)
"""
        scope = "ws_" + "a" * 32
        previous = os.environ.get("CONTEXTPACK_WORKSPACE_ID")
        with patch("backend.hermes_runner.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")) as launch:
            result = api.runner.run("Test prompt", workspace_id=scope)
        args = launch.call_args.args[0]
        self.assertEqual(args[1:5], ["chat", "--oneshot", "--skills", "context-pack"])
        self.assertNotIn("--toolsets", args)
        self.assertEqual(
            args[args.index("--max-turns") + 1],
            str(api.runner.max_turns),
        )
        self.assertEqual(launch.call_args.kwargs["env"]["CONTEXTPACK_WORKSPACE_ID"], scope)
        self.assertEqual(os.environ.get("CONTEXTPACK_WORKSPACE_ID"), previous)
        self.assertTrue(result.handoff.startswith("[TASK]"))
        self.assertTrue(result.skill_used)
        self.assertEqual(
            result.mcp_tool_calls,
            CliHermesRunner._count_mcp_calls(CliHermesRunner._clean_output(stdout)),
        )
        self.assertEqual(
            result.mcp_tools,
            ["mcp__mabc_sources__demo_context_retrieve"],
        )


if __name__ == "__main__":
    unittest.main()
