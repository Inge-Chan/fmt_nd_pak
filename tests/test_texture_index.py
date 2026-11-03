import ast
import contextlib
import io
import json
import os
import struct
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT


class TextureIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.plugins = self.directory / "plugins"
        (self.plugins / "python").mkdir(parents=True)
        self.game = self.directory / "game"
        self.game.mkdir()
        self.cache = self.plugins / "python/NDTextureHashes.json"
        self.saved_offsets = []
        self.metadata_lengths = []
        source = ROOT / "fmt_nd_pak.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        package = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PakFile")
        functions = [node for node in package.body if isinstance(node, ast.FunctionDef)
                     and node.name in ("makeVramHashJson", "dumpGlobalVramHashes", "ensureTextureIndex")]
        functions.extend(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name in ("readTextureDictionaryMetadata", "loadTextureHashCache"))
        namespace = {
            "os": os, "json": json, "struct": struct, "gameName": "TLOUP1",
            "BaseDirectories": {"TLOUP1": str(self.game)}, "ResItemPaddingSz": 48,
            "gdRawDataStarts": {},
            "dialogOptions": types.SimpleNamespace(texDicts=None, textureIndexAttempts=set(),
                                                   isTLOUP1=True, isTLOU2=False),
            "noesis": types.SimpleNamespace(getPluginsPath=lambda: str(self.plugins)),
            "rapi": types.SimpleNamespace(getLocalFileName=lambda path: Path(path).name),
            "NoeBitStream": lambda data: data,
            "_saveRawDataStarts": lambda values: self.saved_offsets.append(json.loads(json.dumps(values))),
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
        metadata_lengths = self.metadata_lengths

        class DictionaryPak:
            def __init__(self, stream, options):
                metadata_lengths.append(len(stream))
                self.path = options["path"]
                self.pakPageEntries = [(128, 64, 0)]
                self.vrams = {123: [96, "synthetic.tga", [], None]}

            def readPakHeader(self):
                namespace["dialogOptions"].isTLOUP1 = False
                namespace["dialogOptions"].isTLOU2 = True
                namespace["ResItemPaddingSz"] = 32

            def makeVramHashJson(self, values):
                namespace["makeVramHashJson"](self, values)

        namespace["PakFile"] = DictionaryPak
        self.namespace = namespace

    def write_dictionary(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        contents = bytearray(256)
        struct.pack_into("<I", contents, 0, 2685)
        struct.pack_into("<II", contents, 16, 1, 64)
        struct.pack_into("<III", contents, 64, 128, 64, 0)
        contents[192:] = b"x" * 64
        path.write_bytes(contents)
        return contents

    def package(self):
        return types.SimpleNamespace(texDict={},
                                     dumpGlobalVramHashes=lambda: self.namespace["dumpGlobalVramHashes"](None))

    def rebuild(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.namespace["dumpGlobalVramHashes"](None)

    def test_modern_index_is_rebuilt_without_shipping_generated_cache(self):
        directory = self.game / "sp-common/texturedict3"
        self.write_dictionary(directory / "sp-common-dict-0.pak")
        (directory / "not-a-dictionary.txt").write_text("ignored", encoding="utf-8")
        (directory / "ignored-dict.pak").mkdir()
        self.cache.write_text(json.dumps({"TLOUP1": {"old-world": {}}, "U4": {"preserved": {}}}),
                              encoding="utf-8")
        self.rebuild()
        result = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(result["TLOUP1"], {"sp-common": {"sp-common-dict-0.pak": {"123": 96}}})
        self.assertEqual(result["U4"], {"preserved": {}})
        self.assertEqual(self.saved_offsets[0]["TLOUP1"]["sp-common"]["sp-common-dict-0.pak"], 192)
        self.assertEqual(self.namespace["dialogOptions"].texDicts["TLOUP1"], result["TLOUP1"])
        self.assertEqual(self.metadata_lengths, [192])

    def test_legacy_index_does_not_require_a_gui_dialog(self):
        directory = self.game / "texturedict2"
        self.write_dictionary(directory / "global-dict-0.pak")
        self.namespace["gameName"] = "U4"
        self.namespace["BaseDirectories"] = {"U4": str(self.game)}
        self.rebuild()
        result = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(result["U4"], {"global-dict-0.pak": {"123": 96}})
        self.assertEqual(self.saved_offsets[0]["U4"]["All"]["global-dict-0.pak"], 192)

    def test_unconfigured_root_does_not_create_a_cache(self):
        self.namespace["BaseDirectories"]["TLOUP1"] = ""
        with self.assertRaises(ValueError):
            self.rebuild()
        self.assertFalse(self.cache.exists())

    def test_missing_dictionary_directory_is_an_explicit_error(self):
        with self.assertRaises(ValueError):
            self.rebuild()
        self.assertFalse(self.cache.exists())

    def test_first_texture_load_builds_index_and_uses_string_hashes_immediately(self):
        self.write_dictionary(self.game / "sp-common/texturedict3/sp-common-dict-0.pak")
        package = self.package()
        with contextlib.redirect_stdout(io.StringIO()):
            self.namespace["ensureTextureIndex"](package)
        self.assertEqual(package.texDict["sp-common"]["sp-common-dict-0.pak"].get("123"), 96)
        self.assertTrue(self.cache.exists())

    def test_cached_game_is_not_rebuilt(self):
        cached = {"sp-common": {"sp-common-dict-0.pak": {"123": 96}}}
        self.namespace["dialogOptions"].texDicts = {"TLOUP1": cached}
        package = self.package()
        self.namespace["ensureTextureIndex"](package)
        self.assertIs(package.texDict, cached)
        self.assertEqual(self.metadata_lengths, [])
        self.assertFalse(self.cache.exists())

    def test_index_for_another_game_does_not_suppress_first_load_rebuild(self):
        self.write_dictionary(self.game / "sp-common/texturedict3/sp-common-dict-0.pak")
        self.cache.write_text(json.dumps({"TLOU2": {"preserved": {}}}), encoding="utf-8")
        self.namespace["dialogOptions"].texDicts = self.namespace["loadTextureHashCache"]()
        package = self.package()
        with contextlib.redirect_stdout(io.StringIO()):
            self.namespace["ensureTextureIndex"](package)
        self.assertIn("sp-common", package.texDict)
        self.assertEqual(self.namespace["dialogOptions"].texDicts["TLOU2"], {"preserved": {}})

    def test_failed_automatic_rebuild_warns_once_per_game_and_root(self):
        package = self.package()
        attempts = []

        def fail():
            attempts.append(True)
            raise ValueError("missing local dictionaries")

        package.dumpGlobalVramHashes = fail
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.namespace["ensureTextureIndex"](package)
            self.namespace["ensureTextureIndex"](package)
        self.assertEqual(len(attempts), 1)
        self.assertIn("using local textures", output.getvalue())
        self.assertIn("missing local dictionaries", output.getvalue())
        self.assertEqual(package.texDict, {})
        self.assertFalse(self.cache.exists())

    def test_rebuild_preserves_model_layout_flags(self):
        self.write_dictionary(self.game / "sp-common/texturedict3/sp-common-dict-0.pak")
        self.rebuild()
        self.assertTrue(self.namespace["dialogOptions"].isTLOUP1)
        self.assertFalse(self.namespace["dialogOptions"].isTLOU2)
        self.assertEqual(self.namespace["ResItemPaddingSz"], 48)

    def test_parse_failure_preserves_model_layout_and_previous_cache(self):
        self.write_dictionary(self.game / "sp-common/texturedict3/sp-common-dict-0.pak")
        previous_cache = '{"TLOUP1": {"old-world": {}}}'
        self.cache.write_text(previous_cache, encoding="utf-8")
        previous_offsets = {"TLOUP1": {"old-world": {"old.pak": 64}}}
        self.namespace["gdRawDataStarts"] = previous_offsets
        dictionary_type = self.namespace["PakFile"]
        original_read = dictionary_type.readPakHeader

        def fail(package):
            original_read(package)
            raise ValueError("broken dictionary")

        with mock.patch.object(dictionary_type, "readPakHeader", fail):
            with self.assertRaisesRegex(ValueError, "broken dictionary"):
                self.rebuild()
        self.assertTrue(self.namespace["dialogOptions"].isTLOUP1)
        self.assertFalse(self.namespace["dialogOptions"].isTLOU2)
        self.assertEqual(self.namespace["ResItemPaddingSz"], 48)
        self.assertEqual(self.cache.read_text(encoding="utf-8"), previous_cache)
        self.assertEqual(previous_offsets, {"TLOUP1": {"old-world": {"old.pak": 64}}})
        self.assertEqual(self.saved_offsets, [])

    def test_empty_dictionary_directory_does_not_create_a_false_success_cache(self):
        (self.game / "sp-common/texturedict3").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "no texture records"):
            self.rebuild()
        self.assertFalse(self.cache.exists())

    def test_invalid_cache_can_be_rebuilt(self):
        self.write_dictionary(self.game / "sp-common/texturedict3/sp-common-dict-0.pak")
        for invalid in ("{", "[]", "null"):
            with self.subTest(invalid=invalid):
                self.cache.write_text(invalid, encoding="utf-8")
                self.rebuild()
                self.assertIn("sp-common", json.loads(self.cache.read_text(encoding="utf-8"))["TLOUP1"])

    def test_metadata_reader_excludes_texture_payload(self):
        path = self.game / "synthetic-dict.pak"
        contents = self.write_dictionary(path)
        metadata = self.namespace["readTextureDictionaryMetadata"](str(path))
        self.assertEqual(metadata, contents[:192])
        self.assertEqual(len(metadata), 192)

    def test_metadata_reader_rejects_truncated_header(self):
        path = self.game / "synthetic-dict.pak"
        path.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "truncated.*header"):
            self.namespace["readTextureDictionaryMetadata"](str(path))

    def test_metadata_reader_rejects_invalid_magic(self):
        path = self.game / "synthetic-dict.pak"
        contents = self.write_dictionary(path)
        struct.pack_into("<I", contents, 0, 0)
        path.write_bytes(contents)
        with self.assertRaisesRegex(ValueError, "invalid.*magic"):
            self.namespace["readTextureDictionaryMetadata"](str(path))

    def test_metadata_reader_rejects_invalid_page_tables(self):
        path = self.game / "synthetic-dict.pak"
        for page_count, page_table in ((0, 64), (1, 8), (100, 64), (1, 250)):
            with self.subTest(page_count=page_count, page_table=page_table):
                contents = self.write_dictionary(path)
                struct.pack_into("<II", contents, 16, page_count, page_table)
                path.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, "invalid.*page table"):
                    self.namespace["readTextureDictionaryMetadata"](str(path))

    def test_metadata_reader_rejects_out_of_bounds_metadata(self):
        path = self.game / "synthetic-dict.pak"
        for page_offset, page_size in ((8, 16), (240, 64)):
            with self.subTest(page_offset=page_offset, page_size=page_size):
                contents = self.write_dictionary(path)
                struct.pack_into("<II", contents, 64, page_offset, page_size)
                path.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, "invalid.*metadata range"):
                    self.namespace["readTextureDictionaryMetadata"](str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
