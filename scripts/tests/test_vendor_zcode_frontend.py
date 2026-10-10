"""Small offline regressions for the native frontend provenance gate."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "vendor_zcode_frontend", Path(__file__).resolve().parents[1] / "vendor_zcode_frontend.py",
)
vendor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vendor)


class FrontendSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "native").mkdir()

    def source(self, content):
        return {"path": "packages/ui/src/example.ts", "sha256": vendor.sha256(content), "git_blob": vendor.git_blob_id(content)}

    def manifest(self, local, original, mode="exact-copy", **extra):
        (self.root / "native/example.ts").write_bytes(local)
        entry = {"path": "native/example.ts", "mode": mode, "expected_sha256": vendor.sha256(local), "sources": [self.source(original)], **extra}
        return {"schema_version": 1, "revision": "0" * 40, "coverage": {"roots": ["native"], "ignore": ["**/*.test.ts"]}, "files": [entry]}

    def test_binary_copy_and_complete_inventory(self):
        manifest = self.manifest(b"\x89PNG\0\xff\n", b"\x89PNG\0\xff\n")
        self.assertEqual(vendor.check(manifest, self.root), [])
        (self.root / "native/example.test.ts").write_text("host test")
        self.assertEqual(vendor.check(manifest, self.root), [])
        (self.root / "native/unrecorded.ts").write_text("uncategorized copy")
        self.assertEqual(vendor.check(manifest, self.root), ["unrecorded file: native/unrecorded.ts"])

    def test_target_change_cannot_be_blessed_as_an_exact_copy(self):
        manifest = self.manifest(b"native original", b"native original")
        changed = b"edited approximation"
        (self.root / "native/example.ts").write_bytes(changed)
        self.assertEqual(len(vendor.check(manifest, self.root)), 2)
        manifest["files"][0]["expected_sha256"] = vendor.sha256(changed)
        self.assertEqual(vendor.check(manifest, self.root), ["source-equivalence mismatch: native/example.ts"])

    def test_banner_only_copy_removes_only_the_declared_banner(self):
        original = b"/* Original Apache notice */\nconst text = 'native';\n"
        banner = "/* Vendored provenance */\n"
        manifest = self.manifest(banner.encode() + original, original, "banner-only-copy", provenance_banner=banner)
        self.assertEqual(vendor.check(manifest, self.root), [])
        entry = manifest["files"][0]
        changed = banner.encode() + b"/* hidden additional change */\n" + original
        (self.root / entry["path"]).write_bytes(changed)
        entry["expected_sha256"] = vendor.sha256(changed)
        self.assertEqual(vendor.check(manifest, self.root), ["source-equivalence mismatch: native/example.ts"])

    def test_css_gate_permits_only_declared_scan_paths(self):
        original = b'@source "upstream";\n:root { --background: #123; }\n'
        local = original.replace(b'@source "upstream";\n', b'@source "local";\n@source "host";\n')
        manifest = self.manifest(local, original, "css-path-adapted", path_replacements=[{"upstream": '@source "upstream";\n', "local": '@source "local";\n@source "host";\n'}])
        self.assertEqual(vendor.check(manifest, self.root), [])
        changed = local.replace(b"#123", b"#456")
        (self.root / "native/example.ts").write_bytes(changed)
        manifest["files"][0]["expected_sha256"] = vendor.sha256(changed)
        self.assertEqual(vendor.check(manifest, self.root), ["source-equivalence mismatch: native/example.ts"])

    def test_host_adaptation_is_checked_without_claiming_equivalence(self):
        manifest = self.manifest(b"browser host seam", b"desktop native source", "whole-file-adapted")
        self.assertEqual(vendor.check(manifest, self.root), [])
        (self.root / "native/example.ts").write_bytes(b"unreviewed adapter")
        self.assertEqual(vendor.check(manifest, self.root), ["reviewed target hash mismatch: native/example.ts"])

    def test_source_verification_checks_originals_and_literal_license(self):
        original = b"heading\nMIT license text\nfooter\n"
        local = b"MIT license text\n"
        manifest = self.manifest(local, original, "license-excerpt", literal_excerpt={"byte_start": 8, "byte_end": 8 + len(local)})
        source = manifest["files"][0]["sources"][0]
        with patch.object(vendor, "read_git_blobs", return_value={source["path"]: (source["git_blob"], original)}):
            self.assertEqual(vendor.check(manifest, self.root, self.root), [])
            manifest["files"][0]["literal_excerpt"]["byte_start"] = 0
            self.assertEqual(vendor.check(manifest, self.root, self.root), ["literal upstream license excerpt mismatch: native/example.ts"])

    def test_manifest_rejects_duplicate_targets_and_path_escapes(self):
        manifest = self.manifest(b"source", b"source")
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        self.assertEqual(vendor.load_manifest(path), manifest)
        manifest["files"].append(manifest["files"][0].copy())
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "duplicate target"):
            vendor.load_manifest(path)
        for name in ("../outside.ts", "/outside.ts", "native/../outside.ts", "native/newline\n.ts", "."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                vendor.relative_path(name)


if __name__ == "__main__":
    unittest.main()
