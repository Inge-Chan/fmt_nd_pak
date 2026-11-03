import ast
import json
import math
import os
import struct
import unittest
from pathlib import Path

from support.pak_reader import Pak
from support.skeleton_reader import read_skel_names, read_skel_bonemap


WORKSPACE = Path(__file__).resolve().parents[1]
PLUGIN = WORKSPACE / 'fmt_nd_pak.py'
TREE = ast.parse(PLUGIN.read_text(encoding='utf-8'))
FUNCTION = next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == 'geometrySubmeshLayout')
NAMESPACE = {}
exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(PLUGIN), 'exec'), NAMESPACE)
layout = NAMESPACE['geometrySubmeshLayout']


def model_records(path, prefixes=()):
    package = Pak(str(path))
    records = []
    skeleton_ids = set()
    for start, offset, identifier, unused in package.items():
        header = start + offset
        kind = package.cstr(start + package.u64(header + 8))
        payload = header + package.padSz
        if kind == 'FOREGROUND_SECTION_2':
            skeleton_ids.add(package.u32(payload + 8))
        if kind != 'GEOMETRY_1':
            continue
        version = package.u32(payload)
        stride, extra = layout(version, True)
        table = package.ptr(payload + 40)
        for index in range(package.u32(payload + 8)):
            descriptor = table + stride * index
            name = package.cstr(package.ptr(descriptor + 32) or 0).rsplit('|', 1)[-1]
            if 'Shape0' not in name or prefixes and not any(name.startswith(prefix) for prefix in prefixes):
                continue
            records.append({'name': name, 'version': version,
                            'vertices': package.u32(descriptor + 128 + extra),
                            'indices': package.u32(descriptor + 132 + extra),
                            'streams': package.u32(descriptor + 136 + extra),
                            'bone_count': package.u32(descriptor + 144 + extra),
                            'skin': package.ptr(descriptor + 88),
                            'normals': package.ptr(descriptor + 112)})
    return package, skeleton_ids, records


class LayoutTests(unittest.TestCase):
    def test_version_55_keeps_existing_t1x_layout(self):
        self.assertEqual(layout(55, True), (176, 0))

    def test_version_57_uses_192_byte_descriptors(self):
        self.assertEqual(layout(57, True), (192, 8))

    def test_other_game_layout_is_unchanged(self):
        self.assertEqual(layout(57, False), (176, 0))


@unittest.skipUnless(os.environ.get('ND_ANIM_TEST_ROOT') and os.environ.get('ND_MODEL_TEST_ROOT'),
                     'native animation and model roots not configured')
class NativeSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.animation_root = Path(os.environ['ND_ANIM_TEST_ROOT'])
        cls.model_root = Path(os.environ['ND_MODEL_TEST_ROOT'])
        cls.skeleton_path = cls.animation_root / 'ellie-skel.pak'
        cls.names = read_skel_names(str(cls.skeleton_path))
        cls.mapping = read_skel_bonemap(str(cls.skeleton_path))
        skeleton_package = Pak(str(cls.skeleton_path))
        cls.skeleton_ids = {skeleton_package.u32(start + offset + 0x34)
                            for start, offset, identifier, unused in skeleton_package.items()
                            if identifier == 0x50CAF5257D6A140B}
        manifest = json.loads((WORKSPACE / 'examples/ellie_lose_balance_parts.json').read_text(encoding='utf-8'))
        cls.models = []
        for entry in manifest['parts']:
            spec = {'file': entry} if isinstance(entry, str) else entry
            path = cls.model_root / Path(spec['file']).name
            cls.models.append(model_records(path, spec.get('meshPrefixes', ())))

    def test_every_model_references_the_animation_skeleton(self):
        self.assertEqual(self.skeleton_ids, {0xAA644C88})
        self.assertEqual(len(self.names), 1074)
        self.assertEqual(len(self.mapping), 520)
        count = 0
        for package, skeleton_ids, records in self.models:
            self.assertEqual(skeleton_ids, {0xAA644C88}, package.path)
            self.assertTrue(records, package.path)
            for record in records:
                self.assertEqual(record['version'], 57)
                self.assertEqual(record['bone_count'], len(self.names), record['name'])
                self.assertGreater(record['vertices'], 0)
                self.assertGreater(record['indices'], 0)
                self.assertEqual(record['indices'] % 3, 0)
                self.assertTrue(1 <= record['streams'] <= 12, record['name'])
                count += 1
        self.assertEqual(count, 19)

    def test_native_skin_weights_are_normalized_and_in_range(self):
        weighted_bones = set()
        blended_vertices = 0
        for package, skeleton_ids, records in self.models:
            for record in records:
                self.assertIsNotNone(record['skin'], record['name'])
                table = package.ptr(record['skin'] + 16)
                weights = package.ptr(record['skin'] + 24)
                for vertex in range(record['vertices']):
                    count, offset = struct.unpack_from('<II', package.d, table + 8 * vertex)
                    self.assertTrue(1 <= count <= 12)
                    values = struct.unpack_from('<%dI' % count, package.d, weights + offset)
                    total = 0.0
                    positive = 0
                    for value in values:
                        bone, weight = value >> 22, (value & 0x3FFFFF) / float(0x3FFFFF)
                        self.assertLess(bone, len(self.names))
                        self.assertTrue(math.isfinite(weight))
                        if weight > 0:
                            positive += 1
                            weighted_bones.add(bone)
                        total += weight
                    self.assertAlmostEqual(total, 1.0, delta=0.00001)
                    blended_vertices += positive > 1
        self.assertGreater(len(weighted_bones), 600)
        self.assertGreater(blended_vertices, 10000)

    def test_normal_recalculation_pointer_does_not_use_string_field(self):
        found = 0
        for package, skeleton_ids, records in self.models:
            for record in records:
                if not record['normals']:
                    continue
                normal_table = record['normals']
                self.assertTrue(0 < package.u32(normal_table) <= record['vertices'])
                self.assertIsNotNone(package.ptr(normal_table + 8))
                found += 1
        self.assertGreater(found, 0)

    def test_all_22_clips_use_native_skeleton_and_original_frame_rates(self):
        from support import noesis_stub as harness
        import inc_nd_anim as animation
        reader = animation.NDAnimReader(harness.FakePak(str(self.animation_root / 'anim-ellie-lose-balance.pak')))
        clips = reader.read()
        self.assertEqual(len(clips), 22)
        self.assertEqual({clip.skelId for clip in clips}, {0xAA644C88})
        self.assertEqual(sum(clip.frameRate == 15 for clip in clips), 4)
        self.assertEqual(sum(clip.frameRate == 30 for clip in clips), 18)
        for clip in clips:
            self.assertFalse(clip.decodeIssues, clip.name)
            self.assertFalse(animation.clipIsAdditive(clip), clip.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
