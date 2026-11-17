"""Exercise source-release privacy and failure safety using real Git archives."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("package_submission", Path(__file__).parents[1] / "package_submission.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class SubmissionArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="matopt-package-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "release.zip"
        self.run_git("init", "-q", "-b", package.BRANCH)
        self.run_git("config", "user.name", "Package test")
        self.run_git("config", "user.email", "package-test@example.invalid")
        for name in package.REQUIRED_FILES:
            self.write(name, "source fixture\n")
        self.write("CITATION.cff", 'title: MatOpt\nrepository-code: "https://github.com/ericyerongjun/matopt"\n')
        self.write(".github/workflows/ci.yml", "private workflow\n")
        self.write("docs/SUBMISSION.md", "codex/comp4431-assignment\n")
        self.write("docs/reference/internal-runs.jsonl", "private traces\n")
        self.write("output/doc/MatOpt_Presentation_Handoff.docx", "private handoff\n")
        self.write("evals/results/private.json", "private results\n")
        self.commit()
        self.write("backend/.env", "DEEPSEEK_API_KEY=private-test-credential-123456\n")
        self.write("backend/data/chat.sqlite3", "private runtime\n")

    def run_git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.STDOUT)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "Test source snapshot")

    def package(self):
        with patch.object(package, "ROOT", self.root), patch.object(sys, "argv", ["package", "--output", str(self.output)]), redirect_stdout(io.StringIO()):
            package.main()

    def commit_tracked(self):
        self.run_git("add", "-u")
        self.run_git("commit", "-qm", "Change test source")

    def test_release_has_source_and_not_private_material(self):
        self.package()
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
            self.assertNotIn("MatOpt/.git/config", names)
            for excluded in (".github/workflows/ci.yml", "docs/SUBMISSION.md", "docs/reference/internal-runs.jsonl", "output/doc/MatOpt_Presentation_Handoff.docx", "evals/results/private.json", "backend/.env", "backend/data/chat.sqlite3"):
                self.assertNotIn("MatOpt/" + excluded, names)
            self.assertNotIn(b"repository-code", archive.read("MatOpt/CITATION.cff"))
            manifest = json.loads(archive.read("MatOpt/SUBMISSION_MANIFEST.json"))
            self.assertEqual(set(manifest), {"schema_version", "package", "files"})
            for name in package.REQUIRED_FILES:
                self.assertIn(name, manifest["files"])
            for name, entry in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read("MatOpt/" + name)).hexdigest(), entry["sha256"])

    def test_secret_failure_preserves_previous_release(self):
        self.package()
        previous = self.output.read_bytes()
        self.write("README.md", "accidental credential: private-test-credential-123456\n")
        self.commit_tracked()
        with self.assertRaisesRegex(ValueError, "Credential material"):
            self.package()
        self.assertEqual(self.output.read_bytes(), previous)

    def test_private_commit_reference_is_rejected(self):
        old_commit = self.run_git("rev-parse", "HEAD").decode().strip()
        self.write("README.md", "Internal commit " + old_commit[:8] + "\n")
        self.commit_tracked()
        with self.assertRaisesRegex(ValueError, "Internal commit reference"):
            self.package()
        self.assertFalse(self.output.exists())

    def test_tracked_runtime_file_is_rejected(self):
        self.run_git("add", "backend/data/chat.sqlite3")
        self.run_git("commit", "-qm", "Accidentally track data")
        with self.assertRaisesRegex(ValueError, "runtime file|runtime data"):
            self.package()

    def test_source_symlink_is_rejected(self):
        (self.root / "backend/leak").symlink_to(self.root / "backend/.env")
        self.run_git("add", "backend/leak")
        self.run_git("commit", "-qm", "Accidentally track symlink")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.package()


if __name__ == "__main__":
    unittest.main()
