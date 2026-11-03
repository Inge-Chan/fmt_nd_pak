import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from support.runtime_fixture import make_runtime_fixture, runtime
import export_animation as exporter


def structure_report():
    return {"version": 7700, "objects": {"Geometry:Mesh": 1}, "weighted_skeleton_bones": 3,
            "animations": ["test-clip"], "curve_values": 12, "non_finite_values": 0}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project, self.source = make_runtime_fixture(self.temporary.name)
        runtime.prepare_runtime(self.project, source=self.source)
        self.input = self.project / "model with spaces.pak"
        self.input.write_bytes(b"synthetic model")
        self.arguments = types.SimpleNamespace(
            input=str(self.input), animation_pak=None, parts=None, clip="test-clip", game="TLOUP1",
            output=str(self.project / "artifacts/result.fbx"), expected_animations=1,
            timeout=10.0, additive=False, legacy_helpers=False,
        )

    def fake_export(self, command, directory, environment, console, timeout):
        Path(command[3]).write_bytes(b"synthetic fbx")
        Path(command[command.index("-logfile") + 1]).write_text(
            "[NDAnim] attached 1 animation(s)\n", encoding="utf-8")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertNotIn("PYTHONHOME", environment)
        self.assertNotIn("PYTHONPATH", environment)
        return 0

    def test_arguments_with_spaces_remain_separate_without_a_shell(self):
        paths = exporter.output_paths(self.project, self.arguments.output)
        command = exporter.build_command(runtime.runtime_directory(self.project), self.input, paths,
                                          clip='turn & "inspect"', game="TLOUP1")
        self.assertEqual(command[2], str(self.input))
        self.assertEqual(command[command.index("-animclip") + 1], 'turn & "inspect"')
        self.assertIn("-fbxmultitake", command)

    def test_existing_output_and_each_sidecar_are_protected(self):
        for key in ("fbx", "log", "console", "record"):
            with self.subTest(key=key):
                paths = exporter.output_paths(self.project, self.arguments.output)
                path = paths[key]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"existing data")
                with self.assertRaises(FileExistsError):
                    exporter.output_paths(self.project, self.arguments.output)
                self.assertEqual(path.read_bytes(), b"existing data")
                path.unlink()

    def test_runtime_outputs_and_wrong_extensions_are_rejected(self):
        for path in (self.project / "runtime/result.fbx", self.project / "result.pak"):
            with self.assertRaises(ValueError):
                exporter.output_paths(self.project, str(path))

    def test_declared_part_inputs_resolve_game_roots_and_are_hashed(self):
        parts = self.project / "parts.json"
        part = self.project / "part.pak"
        part.write_bytes(b"synthetic part")
        parts.write_text(json.dumps({"version": 1, "parts": ["${TLOU2}/part.pak"]}), encoding="utf-8")
        records = exporter.declared_inputs(self.input, None, parts,
                                            {"baseDirectories": {"TLOU2": str(self.project)}})
        self.assertEqual([record["role"] for record in records], ["input", "manifest", "part"])
        self.assertEqual(records[-1]["sha256"], runtime.file_record(part)["sha256"])

    def test_structure_pass_does_not_claim_timing_or_pose_validation(self):
        with mock.patch.dict(exporter.os.environ, {"PYTHONPATH": "not-for-embedded-python"}), \
                mock.patch.object(exporter, "run_noesis", side_effect=self.fake_export), \
                mock.patch.object(exporter, "inspect_fbx", return_value=structure_report()), \
                contextlib.redirect_stdout(io.StringIO()):
            result = exporter.export_animation(self.arguments, self.project)
        self.assertEqual(result["status"], "structure_passed")
        self.assertEqual(result["timing_validation"], "not_checked")
        self.assertEqual(result["pose_validation"], "not_checked")
        saved = json.loads(Path(result["outputs"]["record"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "structure_passed")
        self.assertIn("export_animation.py", saved["driver"]["files"])
        self.assertEqual(saved["declared_inputs"][0]["sha256"], runtime.file_record(self.input)["sha256"])

    def test_zero_exit_without_output_is_a_recorded_failure(self):
        with mock.patch.object(exporter, "run_noesis", return_value=0), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                exporter.export_animation(self.arguments, self.project)
        path = Path(self.arguments.output).with_suffix(".noesis-run.json")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertIn("non-empty FBX", record["error"])

    def test_nonzero_exit_is_not_accepted_as_an_export(self):
        with mock.patch.object(exporter, "run_noesis", return_value=5), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError):
                exporter.export_animation(self.arguments, self.project)
        record = json.loads(Path(self.arguments.output).with_suffix(".noesis-run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["return_code"], 5)
        self.assertEqual(record["status"], "failed")

    def test_timeout_is_recorded_as_failure(self):
        with mock.patch.object(exporter, "run_noesis",
                               side_effect=subprocess.TimeoutExpired(["Noesis64.exe"], 1)), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired):
                exporter.export_animation(self.arguments, self.project)
        record = json.loads(Path(self.arguments.output).with_suffix(".noesis-run.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertIn("TimeoutExpired", record["error"])

    def test_timeout_stops_only_the_owned_process(self):
        process = mock.Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired(["owned"], 1), 0]
        with mock.patch.object(exporter.subprocess, "Popen", return_value=process) as launch:
            with self.assertRaises(subprocess.TimeoutExpired):
                exporter.run_noesis(["owned"], self.project, {}, self.project / "capture.log", 1)
        process.kill.assert_called_once_with()
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_attachment_count_and_nonfinite_curves_are_rejected(self):
        paths = exporter.output_paths(self.project, self.arguments.output)
        paths["fbx"].parent.mkdir(parents=True)
        paths["fbx"].write_bytes(b"synthetic")
        paths["log"].write_text("[NDAnim] attached 2 animation(s)\n", encoding="utf-8")
        with mock.patch.object(exporter, "inspect_fbx", return_value=structure_report()):
            with self.assertRaises(ValueError):
                exporter.check_export(paths, expected=1, clip="test-clip")
        paths["log"].write_text("[NDAnim] attached 1 animation(s)\n", encoding="utf-8")
        report = structure_report()
        report["non_finite_values"] = 1
        with mock.patch.object(exporter, "inspect_fbx", return_value=report):
            with self.assertRaises(ValueError):
                exporter.check_export(paths, expected=1)

    def test_utf16_logs_and_case_insensitive_clip_names(self):
        paths = exporter.output_paths(self.project, self.arguments.output)
        paths["fbx"].parent.mkdir(parents=True)
        paths["fbx"].write_bytes(b"synthetic")
        paths["log"].write_text("[NDAnim] attached 1 animation(s)\n", encoding="utf-16")
        with mock.patch.object(exporter, "inspect_fbx", return_value=structure_report()):
            exporter.check_export(paths, expected=1, clip="TEST-CLIP")

    def test_stale_runtime_is_rejected_before_launch(self):
        (runtime.runtime_directory(self.project) / "Noesis64.exe").write_bytes(b"changed")
        with mock.patch.object(exporter, "run_noesis") as launch:
            with self.assertRaises(runtime.RuntimeSetupError):
                exporter.export_animation(self.arguments, self.project)
        launch.assert_not_called()
        self.assertFalse((self.project / "artifacts").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
