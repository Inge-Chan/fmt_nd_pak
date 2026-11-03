import ast
import contextlib
import io
import json
import os
import tempfile
import types
import unittest
from pathlib import Path

from support import ROOT
from support import noesis_stub
import inc_nd_config
import inc_nd_modelparts


class PartPresetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        plugin_directory = self.directory / "python"
        plugin_directory.mkdir()
        (plugin_directory / "NDPartPresets.json").write_text(
            (ROOT / "NDPartPresets.json").read_text(encoding="utf-8"), encoding="utf-8")
        source_path = ROOT / "fmt_nd_pak.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "_presetModelPartSpecs")
        namespace = {
            "noesis": types.SimpleNamespace(getPluginsPath=lambda: str(self.directory)),
            "inc_nd_config": inc_nd_config, "inc_nd_modelparts": inc_nd_modelparts,
            "BaseDirectories": {}, "json": json, "os": os,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source_path), "exec"), namespace)
        self.load_preset = namespace["_presetModelPartSpecs"]
        self.main = str(self.directory / "game" / "ellie-costume-01-torso.pak")

    def load(self, main=None, existing=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.load_preset(main or self.main, existing or [{"file": self.main}])

    def test_main_model_adds_eleven_parts(self):
        self.assertEqual(len(self.load()), 11)

    def test_explicitly_loaded_part_is_not_duplicated(self):
        result = self.load(existing=[{"file": self.main}, {"file": "ELLIE-HAIR-01-U1-G1-V1.pak"}])
        self.assertEqual(len(result), 10)
        self.assertFalse(any("hair-01" in spec["file"] for spec in result))

    def test_unlisted_models_are_unchanged(self):
        self.assertEqual(self.load(self.main.replace("costume-01", "costume-02")), [])

    def test_main_model_name_is_case_insensitive(self):
        self.assertEqual(len(self.load(self.main.upper())), 11)

    def test_costume_parts_and_cloth_attachments_are_preserved(self):
        specs = self.load()
        cloth = [spec for spec in specs if spec.get("attachTo")]
        self.assertEqual(len(cloth), 4)
        self.assertTrue(all(spec["attachTo"] == "headb" and spec["boneSuffix"] for spec in cloth))
        filenames = {spec["file"] for spec in specs}
        self.assertTrue({"ellie-arms-01-u1-g1-v1.pak", "ellie-shirt-01-u1-g3-v1.pak",
                         "ellie-body-01-u1-g2-v1.pak"} <= filenames)
        self.assertNotIn("ellie-costume-07-torso.pak", filenames)
        self.assertFalse(any(spec["meshPrefixes"] == ("arms1_",) for spec in specs))

    def test_gui_preset_matches_command_line_manifest(self):
        specs = inc_nd_modelparts.readModelPartSpecs("@" + str(ROOT / "examples/t1x_ellie_parts.json"))
        self.assertEqual(self.load(), specs)

    def test_invalid_manifest_fields_are_rejected(self):
        for spec in ({"file": "part.pak", "typo": 1}, {"file": "part.pak", "boneSuffix": "_cloth"},
                     {"file": "part.pak", "meshPrefixes": "hair"}, {"file": ""}):
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    inc_nd_modelparts.readModelPartSpecs([spec])

    def test_lose_balance_manifest_uses_configured_game_roots(self):
        specs = inc_nd_modelparts.readModelPartSpecs(
            "@" + str(ROOT / "examples/ellie_lose_balance_parts.json"))
        self.assertEqual(len(specs), 4)
        self.assertTrue(all(spec["file"].startswith("${TLOU2}/") for spec in specs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
