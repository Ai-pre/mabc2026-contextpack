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
from hermes_runner import CliHermesRunner, HermesExecutionError, HermesTimeoutError

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
                                      mcp_tool_calls=saved["trace"]["mcp_tool_calls"])

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
        self.assertEqual(data["trace"], {"skill_used": self.result.skill_used, "mcp_tool_calls": self.result.mcp_tool_calls})
        self.assertEqual(run.call_args.kwargs, {"workspace_id": "demo", "github_enabled": False})

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
        self.assertIn("github-live", prompt)
        self.assertIn("다른 repository를 검색하거나 근거로 사용하지 않는다", prompt)

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
        self.assertIn("--toolsets", args)
        self.assertEqual(args[args.index("--toolsets") + 1], "skills,mcp-mabc-sources")
        self.assertEqual(args[args.index("--max-turns") + 1], "5")
        self.assertEqual(launch.call_args.kwargs["env"]["CONTEXTPACK_WORKSPACE_ID"], scope)
        self.assertEqual(os.environ.get("CONTEXTPACK_WORKSPACE_ID"), previous)
        self.assertTrue(result.handoff.startswith("[TASK]"))
        self.assertEqual(result.skill_used, CliHermesRunner._detect_skill(CliHermesRunner._clean_output(stdout)))
        self.assertEqual(result.mcp_tool_calls, CliHermesRunner._count_mcp_calls(CliHermesRunner._clean_output(stdout)))


if __name__ == "__main__":
    unittest.main()
