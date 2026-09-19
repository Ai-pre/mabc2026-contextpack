"""API contract checks use isolated temporary stores; no server or model is launched."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.main as api
from backend.workspace_store import WorkspaceStore


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workspace_api_test_")
        self.store = WorkspaceStore(Path(self.temp.name))
        self.store_patch = patch.object(api, "workspace_store", self.store)
        self.store_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        self.store_patch.stop()
        self.temp.cleanup()

    def create(self):
        response = self.client.post("/workspaces", json={"name": "API test workspace"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["workspace_id"]

    def test_workspace_create_list_update_and_sources(self):
        demo = self.client.get("/workspaces").json()["workspaces"][0]
        self.assertEqual(demo["name"], "Partial Refund Demo")
        self.assertEqual(len(demo["sources"]), 4)
        workspace_id = self.create()
        response = self.client.patch(f"/workspaces/{workspace_id}", json={
            "name": "Renamed workspace", "role": "Developer", "task": "Review changes",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["task"], "Review changes")
        self.assertEqual(self.client.get(f"/workspaces/{workspace_id}/sources").json(), {"sources": []})
        self.assertEqual(len(self.client.get("/workspaces").json()["workspaces"]), 2)

    def test_invalid_api_input_and_unknown_routes_are_json(self):
        self.assertEqual(self.client.post("/workspaces", json={"name": "  "}).status_code, 422)
        workspace_id = self.create()
        self.assertEqual(self.client.patch(f"/workspaces/{workspace_id}", json={"name": None}).status_code, 400)
        self.assertEqual(self.client.patch(f"/workspaces/{workspace_id}", json={"sources": []}).status_code, 422)
        response = self.client.get("/workspaces/ws_" + "0" * 32)
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())
        response = self.client.get("/workspaces/demo/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())

    def test_fixture_sources_cannot_be_deleted(self):
        response = self.client.delete("/workspaces/demo/sources/demo-github")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(len(self.store.list_sources("demo")), 4)

    def test_upload_normalize_list_and_remove_existing_fixture(self):
        workspace_id = self.create()
        # Use an existing fixture through the public multipart API in the temporary test store.
        raw = (Path(__file__).resolve().parent.parent / "data/demo_project/notion.md").read_bytes()
        response = self.client.post(f"/workspaces/{workspace_id}/sources", files={
            "file": ("architecture.md", raw, "text/markdown"),
        })
        self.assertEqual(response.status_code, 201, response.text)
        source = response.json()["source"]
        self.assertEqual(source["original_filename"], "architecture.md")
        self.assertEqual(source["size_bytes"], len(raw))
        document = self.store.get_document(workspace_id, source["document_id"])
        self.assertEqual(document["body"].strip(), raw.decode("utf-8").strip())
        self.assertEqual(document["workspace_id"], workspace_id)
        self.assertIsNone(document["timestamp"])
        response = self.client.delete(f"/workspaces/{workspace_id}/sources/{source['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.list_sources(workspace_id), [])

    def test_connect_and_remove_github_source(self):
        workspace_id = self.create()
        response = self.client.post(
            f"/workspaces/{workspace_id}/connectors/github",
            json={"repository": "https://github.com/Ai-pre/mabc2026-contextpack"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        source = response.json()["source"]
        self.assertEqual(source["source_type"], "connector")
        self.assertEqual(source["connector"], "github")
        self.assertEqual(source["repository"], "Ai-pre/mabc2026-contextpack")

        duplicate = self.client.post(
            f"/workspaces/{workspace_id}/connectors/github",
            json={"repository": "Ai-pre/mabc2026-contextpack"},
        )
        self.assertEqual(duplicate.status_code, 400)

        response = self.client.delete(f"/workspaces/{workspace_id}/sources/{source['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.list_sources(workspace_id), [])

    def test_invalid_github_repository_is_rejected(self):
        workspace_id = self.create()
        for value in ("not-a-repo", "owner/repo/extra", "https://example.com/owner/repo"):
            with self.subTest(value=value):
                response = self.client.post(
                    f"/workspaces/{workspace_id}/connectors/github",
                    json={"repository": value},
                )
                self.assertEqual(response.status_code, 400)

    def test_rejected_upload_does_not_register_source(self):
        workspace_id = self.create()
        for filename, raw, status in (("empty.txt", b"", 400), ("bad.exe", b"test", 415),
                                      ("bad.json", b"{", 422), ("bad.pdf", b"not a pdf", 422)):
            with self.subTest(filename=filename):
                response = self.client.post(f"/workspaces/{workspace_id}/sources", files={"file": (filename, raw)})
                self.assertEqual(response.status_code, status, response.text)
                self.assertIn("detail", response.json())
        self.assertEqual(self.store.list_sources(workspace_id), [])

    def test_upload_size_limit(self):
        workspace_id = self.create()
        with patch.object(api, "MAX_FILE_BYTES", 8):
            response = self.client.post(f"/workspaces/{workspace_id}/sources", files={"file": ("large.txt", b"123456789")})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.store.list_sources(workspace_id), [])


if __name__ == "__main__":
    unittest.main()
