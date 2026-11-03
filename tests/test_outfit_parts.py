import math
import os
import struct
import unittest
from pathlib import Path

from support.pak_reader import Pak
from support.skeleton_reader import read_skel_names


def buffer_pointer(package, address):
    fixed = package.ptr(address)
    return package.i64(address) if fixed is None else fixed


def lod_zero_model(path):
    package = Pak(str(path))
    skeleton_ids = set()
    records = []
    for start, offset, identifier, kind in package.items():
        payload = start + offset + package.padSz
        if kind == 'FOREGROUND_SECTION_2':
            skeleton_ids.add(package.u32(payload + 8))
        if kind != 'GEOMETRY_1':
            continue
        if package.u32(payload) != 55:
            raise ValueError('this fixture reader expects P1 geometry version 55')
        table = package.ptr(payload + 40)
        for index in range(package.u32(payload + 8)):
            descriptor = table + 176 * index
            name = package.cstr(package.ptr(descriptor + 32) or 0).rsplit('|', 1)[-1]
            if 'LODShape0_' not in name:
                continue
            records.append({
                'name': name,
                'bounds': struct.unpack_from('<8f', package.d, descriptor),
                'vertices': package.u32(descriptor + 128),
                'indices': package.u32(descriptor + 132),
                'bone_count': package.u32(descriptor + 144),
                'skin': package.ptr(descriptor + 88),
            })
    return package, skeleton_ids, records


@unittest.skipUnless(os.environ.get('ND_ANIM_TEST_ROOT'), 'P1 model root not configured')
class Costume01AssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ['ND_ANIM_TEST_ROOT'])
        cls.bone_count = len(read_skel_names(str(root / 't1x-ellie-skel.pak')))
        cls.models = {
            role: lod_zero_model(root / filename)
            for role, filename in (
                ('shirt', 'ellie-costume-01-torso.pak'),
                ('sleeves', 'ellie-shirt-01-u1-g3-v1.pak'),
                ('hands', 'ellie-arms-01-u1-g1-v1.pak'),
                ('waist', 'ellie-body-01-u1-g2-v1.pak'))
        }

    def test_all_selected_parts_use_native_t1x_skeleton(self):
        self.assertEqual(self.bone_count, 1199)
        for role, (package, skeleton_ids, records) in self.models.items():
            self.assertEqual(skeleton_ids, {0x1AE94937}, role)
            self.assertTrue(records, role)
            for record in records:
                self.assertEqual(record['bone_count'], self.bone_count, record['name'])
                self.assertGreater(record['vertices'], 0)
                self.assertGreater(record['indices'], 0)
                self.assertEqual(record['indices'] % 3, 0)
                self.assertIsNotNone(record['skin'], record['name'])

    def test_costume01_shirt_has_separate_inner_sleeves(self):
        shirt = self.models['shirt'][2]
        sleeves = self.models['sleeves'][2]
        self.assertEqual(len(shirt), 2)
        self.assertTrue(all(record['name'].startswith('shirt_ellie_shirt_01_u1_g1_') for record in shirt))
        self.assertEqual(len(sleeves), 1)
        self.assertTrue(sleeves[0]['name'].startswith('long_sleeves_ellie_shirt_01_u1_g3_'))
        self.assertEqual(sleeves[0]['vertices'], 5513)
        self.assertLess(sleeves[0]['bounds'][0], -0.4)
        self.assertGreater(sleeves[0]['bounds'][4], 0.4)
        self.assertLess(sleeves[0]['bounds'][5], 1.21)

    def test_g1_arms_package_is_hands_only(self):
        hands = self.models['hands'][2]
        self.assertEqual(len(hands), 2)
        for record in hands:
            self.assertTrue(record['name'].startswith('hands_ellie_arms_01_u1_g1_'))
            self.assertEqual(record['vertices'], 5202)
            self.assertLess(record['bounds'][5], 0.92)
            self.assertGreater(min(abs(record['bounds'][0]), abs(record['bounds'][4])), 0.35)

    def test_body_g2_is_a_small_waist_patch(self):
        waist = self.models['waist'][2]
        self.assertEqual(len(waist), 1)
        self.assertTrue(waist[0]['name'].startswith('ellie_torso_ellie_body_01_u1_g2_'))
        self.assertEqual(waist[0]['vertices'], 106)
        self.assertGreater(waist[0]['bounds'][1], 0.88)
        self.assertLess(waist[0]['bounds'][5], 1.0)

    def test_original_skin_weights_are_normalized_and_in_range(self):
        blended_vertices = 0
        for package, skeleton_ids, records in self.models.values():
            for record in records:
                skin = record['skin']
                uncompressed = package.u32(skin + 8) > 0
                table = buffer_pointer(package, skin + 16)
                weights = buffer_pointer(package, skin + 24)
                for vertex in range(record['vertices']):
                    count, offset = struct.unpack_from('<II', package.d, table + 8 * vertex)
                    self.assertTrue(1 <= count <= 12)
                    total = 0.0
                    positive = 0
                    for influence in range(count):
                        if uncompressed:
                            weight, bone = struct.unpack_from('<fI', package.d, weights + offset + 8 * influence)
                        else:
                            value = package.u32(weights + offset + 4 * influence)
                            bone, weight = value >> 22, (value & 0x3FFFFF) / float(0x3FFFFF)
                        self.assertTrue(math.isfinite(weight) and weight >= 0)
                        self.assertLess(bone, self.bone_count)
                        total += weight
                        positive += weight > 0
                    self.assertAlmostEqual(total, 1.0, delta=0.00001)
                    blended_vertices += positive > 1
        self.assertGreater(blended_vertices, 20000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
