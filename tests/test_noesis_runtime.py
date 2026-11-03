import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from support.runtime_fixture import make_runtime_fixture, runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project, self.source = make_runtime_fixture(self.temporary.name)

    def prepare(self, **options):
        return runtime.prepare_runtime(self.project, **options)

    def test_install_copies_distribution_and_deploys_current_project(self):
        python = self.source / "plugins/python"
        (python / "fmt_nd_pak.py").write_text("obsolete source plugin", encoding="utf-8")
        (python / "NDTextureHashes.json").write_text("private cache", encoding="utf-8")
        (self.source / "private.pak").write_bytes(b"not a distribution file")
        result = self.prepare(source=self.source)
        destination = runtime.runtime_directory(self.project)
        self.assertTrue(result["installed"])
        self.assertEqual(len(result["deployment"]["changed"]), 6)
        self.assertEqual((destination / "plugins/python/fmt_nd_pak.py").read_bytes(),
                         (self.project / "fmt_nd_pak.py").read_bytes())
        self.assertFalse((destination / "private.pak").exists())
        self.assertFalse((destination / "plugins/python/NDTextureHashes.json").exists())
        self.assertEqual((destination / "ReadMe.txt").read_bytes(), (self.source / "ReadMe.txt").read_bytes())

    def test_repeated_setup_is_idempotent(self):
        self.prepare(source=self.source)
        executable = runtime.runtime_directory(self.project) / "Noesis64.exe"
        previous = executable.stat().st_mtime_ns
        result = self.prepare(source=self.source)
        self.assertFalse(result["installed"])
        self.assertEqual(result["deployment"]["changed"], [])
        self.assertEqual(executable.stat().st_mtime_ns, previous)

    def test_check_mode_does_not_write_local_state(self):
        self.prepare(source=self.source)
        state = self.project / "runtime/state.json"
        previous = state.read_bytes()
        self.prepare(check_only=True)
        self.assertEqual(state.read_bytes(), previous)

    def test_managed_bytecode_is_cleared_even_when_source_is_unchanged(self):
        self.prepare(source=self.source)
        python = runtime.runtime_directory(self.project) / "plugins/python"
        cache = python / "__pycache__"
        cache.mkdir()
        stale = cache / "inc_nd_anim.cpython-32.pyc"
        legacy = python / "fmt_nd_pak.pyc"
        unrelated = cache / "unrelated.cpython-32.pyc"
        for path in (stale, legacy, unrelated):
            path.write_bytes(b"cached bytecode")
        self.prepare(check_only=True)
        self.assertTrue(stale.exists())
        result = self.prepare()
        self.assertEqual(len(result["deployment"]["removed_bytecode"]), 2)
        self.assertFalse(stale.exists())
        self.assertFalse(legacy.exists())
        self.assertTrue(unrelated.exists())

    def test_project_changes_replace_only_previously_managed_copies(self):
        self.prepare(source=self.source)
        source = self.project / "fmt_nd_pak.py"
        source.write_text("new project revision", encoding="utf-8")
        with self.assertRaises(runtime.RuntimeSetupError):
            self.prepare(check_only=True)
        result = self.prepare()
        self.assertEqual(result["deployment"]["changed"], ["plugins/python/fmt_nd_pak.py"])
        self.assertEqual((runtime.runtime_directory(self.project) / "plugins/python/fmt_nd_pak.py").read_bytes(),
                         source.read_bytes())

    def test_local_edits_are_preserved_without_force(self):
        self.prepare(source=self.source)
        deployed = runtime.runtime_directory(self.project) / "plugins/python/fmt_nd_pak.py"
        deployed.write_bytes(b"local edits")
        with self.assertRaises(runtime.RuntimeSetupError):
            self.prepare()
        self.assertEqual(deployed.read_bytes(), b"local edits")

    def test_force_backs_up_conflicting_edits(self):
        self.prepare(source=self.source)
        deployed = runtime.runtime_directory(self.project) / "plugins/python/fmt_nd_pak.py"
        deployed.write_bytes(b"local edits")
        result = self.prepare(force=True)
        backup = Path(result["deployment"]["backup"]) / "plugins/python/fmt_nd_pak.py"
        self.assertEqual(backup.read_bytes(), b"local edits")
        self.assertEqual(deployed.read_bytes(), (self.project / "fmt_nd_pak.py").read_bytes())

    def test_mutated_source_is_rejected_before_installation(self):
        (self.source / "Noesis64.exe").write_bytes(b"wrong binary")
        with self.assertRaises(runtime.RuntimeSetupError):
            self.prepare(source=self.source)
        self.assertFalse((self.project / "runtime").exists())

    def test_mutated_runtime_cannot_be_bypassed_with_force(self):
        self.prepare(source=self.source)
        executable = runtime.runtime_directory(self.project) / "Noesis64.exe"
        executable.write_bytes(b"wrong binary")
        with self.assertRaises(runtime.RuntimeSetupError):
            self.prepare(force=True)
        self.assertEqual(executable.read_bytes(), b"wrong binary")

    def test_unpinned_plugin_and_root_extras_are_rejected(self):
        self.prepare(source=self.source)
        destination = runtime.runtime_directory(self.project)
        for relative in ("plugins/extra.dll", "extra.dll"):
            with self.subTest(relative=relative):
                path = destination / relative
                path.write_bytes(b"unexpected")
                with self.assertRaises(runtime.RuntimeSetupError):
                    self.prepare(check_only=True)
                path.unlink()

    def test_failed_copy_cleans_only_its_staging_directory(self):
        with mock.patch.object(runtime.shutil, "copy2", side_effect=OSError("simulated copy failure")):
            with self.assertRaises(OSError):
                self.prepare(source=self.source)
        self.assertFalse((self.project / "runtime/noesis").exists())
        self.assertEqual(list((self.project / "runtime").glob(".setup-*")), [])
        self.assertTrue((self.source / "Noesis64.exe").exists())

    def test_local_configuration_is_deployed_without_becoming_a_host_dependency(self):
        config = self.project / "NDConfig.json"
        config.write_text('{"version": 1}', encoding="utf-8")
        result = self.prepare(source=self.source)
        self.assertEqual(len(result["deployment"]["managed_files"]), 7)
        deployed = runtime.runtime_directory(self.project) / "plugins/python/NDConfig.json"
        self.assertEqual(deployed.read_bytes(), config.read_bytes())
        runtime.verify_runtime(self.project)

    def test_runtime_only_configuration_is_not_overwritten(self):
        self.prepare(source=self.source)
        config = runtime.runtime_directory(self.project) / "plugins/python/NDConfig.json"
        config.write_text('{"version": 1, "defaultGame": "TLOU2"}', encoding="utf-8")
        before = config.read_bytes()
        self.prepare()
        self.assertEqual(config.read_bytes(), before)

    def test_invalid_lock_schema_and_version_are_rejected(self):
        path = self.project / "runtime.lock.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for key, value in (("schema", 2), ("version", "4.999"), ("entrypoint", "../other.exe")):
            with self.subTest(key=key):
                changed = dict(original)
                changed[key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(runtime.RuntimeSetupError):
                    self.prepare(source=self.source)
        self.assertFalse((self.project / "runtime/noesis").exists())

    def test_workspace_escape_and_unowned_cleanup_are_rejected(self):
        with self.assertRaises(runtime.RuntimeSetupError):
            runtime.checked_child(self.project, "../outside")
        with self.assertRaises(runtime.RuntimeSetupError):
            runtime.remove_staging(self.source, self.project)
        self.assertTrue(self.source.is_dir())
        protected = self.project / ".setup-preserved"
        protected.mkdir()
        with self.assertRaises(runtime.RuntimeSetupError):
            runtime.remove_staging(self.project.parent / protected.name, self.project)
        self.assertTrue(protected.is_dir())

    def test_reparse_points_are_not_followed(self):
        path = self.source / "ReadMe.txt"
        with mock.patch.object(Path, "is_symlink", return_value=False), \
                mock.patch.object(Path, "lstat", return_value=types.SimpleNamespace(st_file_attributes=1024)):
            with self.assertRaises(runtime.RuntimeSetupError):
                runtime.reject_link(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
