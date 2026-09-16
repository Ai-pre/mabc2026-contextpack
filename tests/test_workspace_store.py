import json
import os
import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.workspace_store import (
    DEMO_ROLE,
    DEMO_TASK,
    DEMO_WORKSPACE_ID,
    SourceNotFound,
    WorkspaceNotFound,
    WorkspaceStore,
    WorkspaceValidationError,
)


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspaces"
        self.store = WorkspaceStore(self.root)

    def make_source(self, workspace_id, filename="notes.md", body="A confirmed decision"):
        return self.store.add_source(workspace_id, filename, body.encode(), {
            "body": body, "metadata": {"pages": [{"page": 1, "text": body}]},
        })

    @unittest.skipUnless(os.name == "nt", "Windows file sharing behavior")
    def test_update_retries_while_another_process_reads_manifest(self):
        workspace = self.store.create_workspace()
        path = self.root / workspace["workspace_id"] / "manifest.json"
        reader = path.open("r", encoding="utf-8")
        close_reader = threading.Timer(0.06, reader.close)
        close_reader.start()
        try:
            updated = self.store.update_workspace(workspace["workspace_id"], name="After read")
            self.assertEqual(updated["name"], "After read")
        finally:
            close_reader.join()
            reader.close()

    def test_demo_is_lazy_and_keeps_fixture_metadata(self):
        self.assertFalse(self.root.exists())
        demo = self.store.list_workspaces()[0]
        self.assertEqual(demo["workspace_id"], DEMO_WORKSPACE_ID)
        self.assertEqual(demo["name"], "Partial Refund Demo")
        self.assertEqual((demo["role"], demo["task"]), (DEMO_ROLE, DEMO_TASK))
        self.assertTrue(demo["is_demo"])
        self.assertEqual([source["title"] for source in demo["sources"]], ["GitHub", "Jira", "Slack", "Notion"])
        self.assertEqual(self.store.list_documents(DEMO_WORKSPACE_ID), [])
        self.assertFalse((self.root / "demo" / "normalized").exists())
        self.assertEqual(self.store.get_workspace("demo"), demo)

    def test_workspace_create_update_and_reload(self):
        first = self.store.create_workspace("  Project A  ", "Reviewer", "Review decisions")
        second = self.store.create_workspace()
        self.assertRegex(first["workspace_id"], r"^ws_[0-9a-f]{32}$")
        self.assertNotEqual(first["workspace_id"], second["workspace_id"])
        self.assertEqual(first["name"], "Project A")
        self.assertFalse(first["is_demo"])
        self.assertEqual(first["sources"], [])
        updated = self.store.update_workspace(first["workspace_id"], role=" Engineer ", task="")
        self.assertEqual(updated["role"], "Engineer")
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertGreaterEqual(updated["updated_at"], first["updated_at"])
        self.assertTrue(updated["updated_at"].endswith("Z"))
        self.assertEqual(WorkspaceStore(self.root).get_workspace(first["workspace_id"]), updated)
        self.assertEqual(len(self.store.list_workspaces()), 3)
        updated["name"] = "Unsaved edit"
        self.assertEqual(self.store.get_workspace(first["workspace_id"])["name"], "Project A")

    def test_invalid_ids_cannot_escape_the_root(self):
        for workspace_id in ("../outside", "..\\outside", "C:\\outside", "ws_123", "DEMO", "", None):
            with self.subTest(workspace_id=workspace_id), self.assertRaises(WorkspaceValidationError):
                self.store.get_workspace(workspace_id)
        with self.assertRaises(WorkspaceNotFound):
            self.store.get_workspace("ws_" + "a" * 32)
        self.assertFalse(self.root.exists())

    def test_updates_cannot_change_identity_or_rename_demo(self):
        workspace = self.store.create_workspace()
        for changes in ({"workspace_id": "demo"}, {"is_demo": True}, {"sources": []}, {"name": " "}, {"role": None}):
            with self.subTest(changes=changes), self.assertRaises(WorkspaceValidationError):
                self.store.update_workspace(workspace["workspace_id"], **changes)
        with self.assertRaises(WorkspaceValidationError):
            self.store.update_workspace("demo", name="Renamed")
        self.assertEqual(self.store.update_workspace("demo", task="Review demo")["task"], "Review demo")

    def test_upload_preserves_metadata_and_overrides_untrusted_identity(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        source = self.store.add_source(workspace_id, "C:\\fakepath\\policy.MD", b"Policy body", {
            "id": "foreign", "workspace_id": "demo", "source_type": "demo", "title": "Foreign",
            "body": "Policy body", "metadata": {"pages": [1], "blocks": [{"text": "Policy body"}]},
        })
        self.assertRegex(source["id"], r"^src_[0-9a-f]{32}$")
        self.assertEqual(source["original_filename"], "policy.MD")
        self.assertEqual(source["mime_type"], "text/markdown")
        self.assertEqual(source["size_bytes"], 11)
        document = self.store.get_document(workspace_id, source["document_id"])
        self.assertEqual((document["id"], document["workspace_id"], document["source_type"]), (source["id"], workspace_id, "upload"))
        self.assertEqual(document["metadata"]["pages"], [1])
        self.assertEqual(document["metadata"]["blocks"], [{"text": "Policy body"}])
        self.assertIsNone(document["timestamp"])
        self.assertEqual((self.root / workspace_id / "sources" / (source["id"] + ".md")).read_bytes(), b"Policy body")
        self.assertEqual(self.store.list_documents(workspace_id), [document])
        self.assertFalse(list((self.root / workspace_id).rglob(".tmp-*")))

    def test_documents_are_isolated_and_only_registered_files_are_read(self):
        first = self.store.create_workspace()["workspace_id"]
        second = self.store.create_workspace()["workspace_id"]
        source = self.make_source(first)
        for target in (second, "demo"):
            with self.subTest(target=target), self.assertRaises(SourceNotFound):
                self.store.get_document(target, source["id"])
            self.assertEqual(self.store.list_documents(target), [])
        normalized = self.root / second / "normalized"
        normalized.mkdir()
        (normalized / (source["id"] + ".json")).write_text(json.dumps({"body": "Unregistered data"}))
        self.assertEqual(self.store.list_documents(second), [])
        with self.assertRaises(SourceNotFound):
            self.store.get_document(second, source["id"])
        with self.assertRaises(SourceNotFound):
            self.store.get_document(first, "../../manifest")

    def test_document_contents_cannot_claim_another_workspace(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        source = self.make_source(workspace_id)
        document_path = self.root / workspace_id / "normalized" / (source["id"] + ".json")
        document = json.loads(document_path.read_text(encoding="utf-8"))
        document["workspace_id"] = "demo"
        document_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(WorkspaceValidationError):
            self.store.get_document(workspace_id, source["id"])

    def test_remove_upload_cleans_files_and_protects_demo_fixtures(self):
        source = self.make_source("demo")
        removed = self.store.remove_source("demo", source["id"])
        self.assertEqual(removed, source)
        self.assertEqual(len(self.store.list_sources("demo")), 4)
        self.assertEqual(self.store.list_documents("demo"), [])
        self.assertFalse(list((self.root / "demo").rglob(source["id"] + ".*")))
        with self.assertRaises(SourceNotFound):
            self.store.get_document("demo", source["id"])
        with self.assertRaises(SourceNotFound):
            self.store.remove_source("demo", source["id"])
        with self.assertRaises(WorkspaceValidationError):
            self.store.remove_source("demo", "demo-github")

    def test_invalid_source_does_not_register_or_write_document(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        cases = [("bad.exe", b"data", {"body": "data"}), ("bad\x00.md", b"data", {"body": "data"}),
                 ("empty.txt", b"", {"body": "data"}), ("empty.txt", b" ", {"body": " "}),
                 ("bad.json", b"data", {"body": "data", "metadata": []})]
        for filename, raw, document in cases:
            with self.subTest(filename=filename), self.assertRaises(WorkspaceValidationError):
                self.store.add_source(workspace_id, filename, raw, document)
        self.assertEqual(self.store.list_sources(workspace_id), [])
        self.assertFalse((self.root / workspace_id / "normalized").exists())

    def test_failed_manifest_update_rolls_back_upload_files(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        with patch.object(self.store, "_write_manifest", side_effect=OSError("Simulated write failure")):
            with self.assertRaises(OSError):
                self.make_source(workspace_id)
        self.assertEqual(self.store.list_sources(workspace_id), [])
        self.assertEqual(list((self.root / workspace_id / "sources").iterdir()), [])
        self.assertEqual(list((self.root / workspace_id / "normalized").iterdir()), [])

    def test_parallel_uploads_keep_all_manifest_entries(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            sources = list(pool.map(lambda index: self.make_source(workspace_id, f"notes-{index}.txt"), range(8)))
        self.assertEqual(len({source["id"] for source in sources}), 8)
        self.assertEqual(len(self.store.list_sources(workspace_id)), 8)
        self.assertEqual(len(self.store.list_documents(workspace_id)), 8)

    def test_corrupt_manifest_is_reported(self):
        workspace_id = self.store.create_workspace()["workspace_id"]
        (self.root / workspace_id / "manifest.json").write_text("{invalid", encoding="utf-8")
        with self.assertRaises(WorkspaceValidationError):
            self.store.get_workspace(workspace_id)


if __name__ == "__main__":
    unittest.main()
