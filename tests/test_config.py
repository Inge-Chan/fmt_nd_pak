import json
import os
import tempfile
import unittest
from pathlib import Path

from support import ROOT
import inc_nd_config as config


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write_config(self, values):
        (self.directory / "NDConfig.json").write_text(json.dumps(values), encoding="utf-8")

    def test_no_local_configuration_contains_no_personal_paths(self):
        result = config.load_config(str(self.directory), {})
        self.assertEqual(result["defaultGame"], "TLOUP1")
        self.assertEqual(result["baseDirectories"], dict.fromkeys(config.GAME_IDS, ""))

    def test_relative_configuration_paths_resolve_beside_configuration(self):
        self.write_config({"version": 1, "baseDirectories": {"TLOUP1": "game"}})
        result = config.load_config(str(self.directory), {})
        self.assertEqual(result["baseDirectories"]["TLOUP1"], str(self.directory / "game") + os.sep)

    def test_environment_overrides_local_configuration(self):
        self.write_config({"version": 1, "defaultGame": "TLOUP1", "baseDirectories": {"TLOU2": "old"}})
        result = config.load_config(str(self.directory), {"ND_DEFAULT_GAME": "TLOU2", "ND_TLOU2_ROOT": "new"})
        self.assertEqual(result["defaultGame"], "TLOU2")
        self.assertEqual(result["baseDirectories"]["TLOU2"], str(self.directory / "new") + os.sep)

    def test_bad_configuration_is_not_silently_accepted(self):
        for values in ([], {"version": 2}, {"version": 1, "defaultGame": "unknown"},
                       {"version": 1, "baseDirectories": []},
                       {"version": 1, "baseDirectories": {"TLOUP1": 4}},
                       {"version": 1, "baseDirectories": {"other": ""}},
                       {"version": 1, "typo": True}):
            with self.subTest(values=values):
                self.write_config(values)
                with self.assertRaises(ValueError):
                    config.load_config(str(self.directory), {})

    def test_example_configuration_is_valid(self):
        example = (ROOT / "NDConfig.example.json").read_text(encoding="utf-8")
        (self.directory / "NDConfig.json").write_text(example, encoding="utf-8")
        self.assertEqual(config.load_config(str(self.directory), {})["defaultGame"], "TLOUP1")

    def test_malformed_json_raises(self):
        (self.directory / "NDConfig.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            config.load_config(str(self.directory), {})


class PathTests(unittest.TestCase):
    def test_part_ii_pc_path_does_not_match_part_i(self):
        for name in ("The Last of Us Part II", "The Last of Us Part 2"):
            path = "D:/Games/" + name + "/build/pc/main/sp-common/actor97/ellie.pak"
            self.assertEqual(config.detect_game(path), "TLOU2")

    def test_supported_game_paths(self):
        paths = (
            ("D:/Games/The Last of Us Part I/build/pc/main/a.pak", "TLOUP1"),
            ("D:/Games/build/ps4/main/a.pak", "TLOU2"),
            ("D:/Games/build/pc/uncharted4/a.pak", "U4"),
            ("D:/Games/build/pc/thelostlegacy/a.pak", "TLL"),
        )
        for path, expected in paths:
            with self.subTest(path=path):
                self.assertEqual(config.detect_game(path), expected)
                self.assertEqual(config.detect_game(path.replace("/", "\\")), expected)

    def test_configured_roots_take_priority_over_filename_hints(self):
        roots = {"TLOU2": "D:/renamed-data/", "TLOUP1": "D:/renamed-data/remake/"}
        self.assertEqual(config.detect_game("D:/renamed-data/part i.pak", roots), "TLOU2")
        self.assertEqual(config.detect_game("D:/renamed-data/remake/actor.pak", roots), "TLOUP1")
        self.assertEqual(config.detect_game("D:/renamed-data-other/actor.pak", roots, "U4"), "U4")

    def test_ps4_and_pc_roots_are_extracted_with_trailing_separator(self):
        for platform in ("ps4", "pc"):
            root = "D:\\Games\\build\\" + platform + "\\main\\"
            self.assertEqual(config.find_root_dir(root + "sp-common\\actor97\\actor.pak"), root)

    def test_uncharted_roots_are_preserved(self):
        for game in ("uncharted4", "thelostlegacy"):
            root = "D:/Games/build/pc/" + game + "/"
            self.assertEqual(config.find_root_dir(root + "actor77/actor.pak"), root)

    def test_unknown_path_uses_explicit_fallback(self):
        self.assertEqual(config.detect_game("actor.pak", fallback="TLOU2"), "TLOU2")
        with self.assertRaises(ValueError):
            config.detect_game("actor.pak", fallback="unknown")

    def test_relative_parts_remain_relative_to_main_package(self):
        root = os.path.abspath("local-game")
        self.assertEqual(config.resolve_asset_path("head.pak", {}, root), os.path.join(root, "head.pak"))

    def test_game_root_parts_resolve_without_absolute_manifest_paths(self):
        root = os.path.abspath("local-game")
        result = config.resolve_asset_path("${TLOU2}/sp-common/actor97/head.pak", {"TLOU2": root}, "")
        self.assertEqual(result, os.path.join(root, "sp-common", "actor97", "head.pak"))

    def test_unconfigured_unknown_and_escaping_game_roots_are_rejected(self):
        for path in ("${OTHER}/head.pak", "${TLOU2}/head.pak", "${TLOUP1}/../head.pak",
                     "${TLOUP1}/D:/head.pak", "${TLOUP1}//head.pak", "${TLOUP1}head.pak"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    config.resolve_asset_path(path, {"TLOUP1": os.path.abspath("local-game")}, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
