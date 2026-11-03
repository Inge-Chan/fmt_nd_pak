import contextlib
import io
import math
import os
import struct
import unittest

from support import ROOT
import inc_nd_skeleton as skeleton


def fixture_rig():
    names = ['root', 'l_elbow', 'l_wrist', 'l_elbow_helper', 'l_twist_05',
             'l_twist_05_offset', 'l_wrist_helper', 'spinea', 'spineb',
             'spinec', 'spined', 'l_clavicle', 'strap_grp',
             'l_backpack_strap_01_base', 'l_backpack_strap_01_rbf',
             'l_backpack_strap_01_helper', 'mover_offset_jnts',
             'l_backpack_strap_01_mover_offset', 'm_twist_02']
    parents = [-1, 0, 1, 0, 3, 4, 3, 0, 7, 8, 9, 10, -1, 12, 13, 14, 0, 16, 7]
    positions = [(0, 0, 0), (0.3, 1, 0), (0.3, 1, 1), (0.3, 1, 0),
                 (0.3, 1, 0.5), (0.3, 1, 0.5), (0.3, 1, 1),
                 (0, 0.8, 0), (0, 1, 0), (0, 1.2, 0), (0, 1.4, 0),
                 (0.15, 1.5, 0), (0, 0, 0), (0.1, 1.45, -0.1),
                 (0.1, 1.45, -0.1), (0.1, 1.45, -0.1), (0, 0, 0),
                 (0, 0, 0), (0, 1, 0)]
    world = [skeleton.matrixFromTRS(position, (0, 0, 0, 1)) for position in positions]
    main = [0, 1, 2, 7, 8, 9, 10, 11, 16, 17]
    return skeleton.DeformationRig(names, parents, main,
                                    [skeleton.matrixInverse(matrix) for matrix in world])


class MatrixTests(unittest.TestCase):
    def test_affine_inverse_preserves_scale_rotation_and_translation(self):
        matrix = skeleton.matrixFromTRS((1, -2, 3), (0.1, 0.2, -0.3, 0.9), (0.8, 1.2, 0.9))
        inverse = skeleton.matrixInverse(matrix)
        for product in (skeleton.matrixMultiply(matrix, inverse), skeleton.matrixMultiply(inverse, matrix)):
            for actual, expected in zip(product, skeleton.IDENTITY):
                self.assertAlmostEqual(actual, expected, places=10)

    def test_quaternion_decomposition_including_half_turns(self):
        for quaternion in ((0, 0, 0, 1), (1, 0, 0, 0), (0, 1, 0, 0),
                           (0, 0, 1, 0), (0.1, 0.2, 0.3, 0.4)):
            matrix = skeleton.matrixFromTRS((1, 2, 3), quaternion, (0.8, 1.1, 1.2))
            restored = skeleton.matrixFromTRS(*skeleton.matrixToTRS(matrix))
            for actual, expected in zip(restored, matrix):
                self.assertAlmostEqual(actual, expected, places=10)

    def test_singular_matrix_is_rejected(self):
        with self.assertRaises(ValueError):
            skeleton.matrixInverse((0.0,) * 12)


class HelperTests(unittest.TestCase):
    def test_neutral_bake_preserves_all_helper_bind_transforms(self):
        rig = fixture_rig()
        for bone, samples in rig.bake(2, 30, {}).items():
            for time, position, rotation, scale in samples:
                for actual, expected in zip(skeleton.matrixFromTRS(position, rotation, scale), rig.localBind[bone]):
                    self.assertAlmostEqual(actual, expected, places=9)

    def test_strap_does_not_attach_to_similarly_named_offset_control(self):
        rig = fixture_rig()
        helper = rig.names.index('l_backpack_strap_01_helper')
        self.assertEqual(rig.names[rig.parents[helper]], 'l_backpack_strap_01_rbf')
        self.assertNotIn(helper, rig.followers)

    def test_spine_helper_follows_its_bind_position_not_the_lowest_spine(self):
        rig = fixture_rig()
        self.assertEqual(rig.parents[rig.names.index('m_twist_02')], rig.names.index('spineb'))

    def test_forearm_twist_distributes_wrist_roll(self):
        rig = fixture_rig()
        half = math.sqrt(0.5)
        tracks = {2: {'rotation': [(0, (0, 0, half, half))]}}
        sample = rig.bake(1, 30, tracks)[4][0]
        angle = 2 * math.atan2(sample[2][2], sample[2][3])
        self.assertAlmostEqual(angle, math.pi / 4, places=8)
        self.assertEqual(sample[1], (0.0, 0.0, 0.5))

    def test_twist_stays_continuous_across_quaternion_sign_boundary(self):
        rig = fixture_rig()
        keys = []
        for frame, degrees in enumerate((170, 179, 181, 190)):
            half_angle = math.radians(degrees) / 2
            quaternion = (0, 0, math.sin(half_angle), math.cos(half_angle))
            if frame >= 2:
                quaternion = tuple(-value for value in quaternion)
            keys.append((frame / 30, quaternion))
        samples = rig.bake(4, 30, {2: {'rotation': keys}})[4]
        for first, second in zip(samples, samples[1:]):
            angle = 2 * math.acos(min(1, abs(skeleton.dot(first[2], second[2]))))
            self.assertLess(angle, math.radians(5))

    def test_strap_moves_when_chest_moves_and_control_stays_static(self):
        rig = fixture_rig()
        tracks = {10: {'translation': [(0, (0, 0.2, 0)), (1 / 30, (0.1, 0.2, 0))]}}
        samples = rig.bake(2, 30, tracks)[13]
        self.assertGreater(samples[1][1][0] - samples[0][1][0], 0.01)

    def test_upper_arm_twist_fades_from_clavicle_to_elbow(self):
        names = ['root', 'l_clavicle', 'l_shoulder', 'l_elbow', 'l_shoulder_helper',
                 'l_twist_01', 'l_twist_04']
        parents = [-1, 0, 1, 2, 2, 4, 4]
        positions = [(0, 0, 0), (0, 1, 0), (0.2, 1, 0), (0.5, 1, 0), (0.2, 1, 0),
                     (0.2, 1, 0), (0.5, 1, 0)]
        world = [skeleton.matrixFromTRS(position, (0, 0, 0, 1)) for position in positions]
        rig = skeleton.DeformationRig(names, parents, [0, 1, 2, 3],
                                      [skeleton.matrixInverse(matrix) for matrix in world])
        half = math.radians(60) / 2
        tracks = {2: {'rotation': [(0, (math.sin(half), 0, 0, math.cos(half)))]}}
        baked = rig.bake(1, 30, tracks)
        near = 2 * math.atan2(baked[5][0][2][0], baked[5][0][2][3])
        far = 2 * math.atan2(baked[6][0][2][0], baked[6][0][2][3])
        # locals are relative to l_shoulder, which already carries the full 60 degrees
        self.assertAlmostEqual(near, -math.radians(60), places=6)
        self.assertAlmostEqual(far, 0, places=6)

    def test_strap_blend_does_not_drift_when_anchors_rotate_apart(self):
        names = ['root', 'a', 'b', 'strap_grp', 'l_backpack_strap_01_base']
        parents = [-1, 0, 0, -1, 3]
        positions = [(0, 0, 0), (0, 1, 0), (0, 1.2, 0), (0, 0, 0), (0, 1.1, 0)]
        world = [skeleton.matrixFromTRS(position, (0, 0, 0, 1)) for position in positions]
        rig = skeleton.DeformationRig(names, parents, [0, 1, 2],
                                      [skeleton.matrixInverse(matrix) for matrix in world])
        rig.constraints[4] = {'kind': 'strap', 'sources': [1, 2], 'weights': [0.5, 0.5]}
        rig.order = [0, 1, 2, 3, 4]
        rig.bindTRS = {bone: skeleton.matrixToTRS(rig.localBind[bone]) for bone in (0, 1, 2)}
        half = math.radians(90) / 2
        tracks = {1: {'rotation': [(0, (0, 0, math.sin(half), math.cos(half)))]},
                  2: {'rotation': [(0, (0, 0, -math.sin(half), math.cos(half)))]}}
        position = rig.bake(1, 30, tracks)[4][0][1]
        # each anchor carries the strap point to (-0.1, 1.1); blending the
        # anchors' correction offsets instead would have pushed it to y = 2.2
        self.assertAlmostEqual(position[0], -0.1, places=6)
        self.assertAlmostEqual(position[1], 1.1, places=6)

    def test_strap_anchors_follow_body_skin_under_the_strap(self):
        rig = fixture_rig()
        strap = rig.names.index('l_backpack_strap_01_base')
        clavicle, spined = rig.names.index('l_clavicle'), rig.names.index('spined')
        backpack = rig.names.index('l_backpack_strap_01_helper')
        under = [((0.1 + 0.001 * index, 1.45, -0.1), [(clavicle, 0.75), (spined, 0.25)])
                 for index in range(12)]
        ignored = [((0.1, 1.45, -0.1), [(backpack, 1.0)]), ((3, 3, 3), [(spined, 1.0)])]
        self.assertEqual(rig.useSkinStrapAnchors(under + ignored), 1)
        self.assertEqual(rig.constraints[strap]['sources'], [clavicle, spined])
        self.assertAlmostEqual(rig.constraints[strap]['weights'][0], 0.75)
        for bone, samples in rig.bake(1, 30, {}).items():
            actual = skeleton.matrixFromTRS(*samples[0][1:])
            self.assertLess(max(abs(left - right) for left, right in zip(actual, rig.localBind[bone])), 1e-9)

    def test_strap_anchor_needs_enough_nearby_body_vertices(self):
        rig = fixture_rig()
        strap = rig.names.index('l_backpack_strap_01_base')
        before = dict(rig.constraints[strap])
        far = [((0.1, 1.0, -0.1), [(rig.names.index('spinea'), 1.0)])] * 20
        self.assertEqual(rig.useSkinStrapAnchors(far), 0)
        self.assertEqual(rig.constraints[strap], before)

    def test_parent_cycle_is_rejected_even_without_constraints(self):
        with self.assertRaises(ValueError):
            skeleton.DeformationRig(['root', 'other'], [1, 0], [0], [skeleton.IDENTITY] * 2)


@unittest.skipUnless(os.environ.get('ND_ANIM_TEST_ROOT'), 'real sample root not configured')
class RealRigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from support.skeleton_reader import read_skeleton, read_skel_bonemap
        cls.root = os.environ['ND_ANIM_TEST_ROOT']
        path = os.path.join(cls.root, 't1x-ellie-skel.pak')
        cls.source = read_skeleton(path)
        cls.mapping = read_skel_bonemap(path)
        package = cls.source['pak']
        header = next(package.ptr(start + offset + 20 + package.padSz + 12)
                      for start, offset, identifier, unused in package.items()
                      if identifier == 0x50CAF5257D6A140B)
        cls.rig = skeleton.readDeformationRig(package.d, header, cls.source['names'],
                      [entry[1] for entry in cls.source['parents']], cls.mapping)

    def test_native_inverse_binds_match_every_primary_joint(self):
        world = {}
        for joint, bone in enumerate(self.mapping):
            record = self.source['xforms'][joint]
            local = skeleton.matrixFromTRS(record['pos'], record['quat'], record['scale'])
            parent = self.source['parents'][bone][1]
            world[bone] = skeleton.matrixMultiply(world[parent], local) if parent >= 0 else local
            for actual, expected in zip(self.rig.worldBind[bone], world[bone]):
                self.assertAlmostEqual(actual, expected, delta=1e-5)

    def test_real_neutral_bake_preserves_all_52_constrained_helpers(self):
        # 8 forearm twist + 8 upper-arm twist + 36 strap bases
        self.assertEqual(len(self.rig.constraints), 52)
        for bone, samples in self.rig.bake(1, 30, {}).items():
            actual = skeleton.matrixFromTRS(*samples[0][1:])
            self.assertLess(max(abs(left - right) for left, right in zip(actual, self.rig.localBind[bone])), 1e-6)

    def test_all_157_native_clips_bake_finite_helper_keys(self):
        from support import noesis_stub as harness
        import inc_nd_anim as animation
        reader = animation.NDAnimReader(harness.FakePak(os.path.join(self.root, 'anim-t1x-ellie-inspect.pak')))
        clips = [clip for clip in reader.read()
                 if clip.skelId == 0x1AE94937 and not animation.clipIsAdditive(clip)]
        bones = [harness.FakeBone(name) for name in self.rig.names]
        with contextlib.redirect_stdout(io.StringIO()):
            exported = animation.buildNoesisAnims(clips, bones, self.mapping,
                         scale=100, targetSkelId=0x1AE94937, allowAdditive=False,
                         deformationRig=self.rig)
        self.assertEqual(len(exported), 157)
        for clip in exported:
            helpers = [bone for bone in clip.kfBones if bone.boneIndex in self.rig.constraints]
            self.assertEqual(len(helpers), 52, clip.name)
            for bone in helpers:
                for keys in (bone.translationKeys, bone.rotationKeys, bone.scaleKeys):
                    for key in keys:
                        self.assertTrue(all(math.isfinite(value) for value in key.value.v), clip.name)
                for key in bone.rotationKeys:
                    self.assertAlmostEqual(skeleton.dot(key.value.v, key.value.v), 1, places=6)

    def test_helper_bake_does_not_change_primary_animation_keys(self):
        from support import noesis_stub as harness
        import inc_nd_anim as animation
        reader = animation.NDAnimReader(harness.FakePak(os.path.join(self.root, 'anim-t1x-ellie-inspect.pak')))
        wanted = {'ellie-inspect-comic-ground-turn-back',
                  'ellie-inspect-paper-ground-exit-2',
                  'ellie-inspect-recorder-ground-enter'}
        clips = [clip for clip in reader.read() if clip.name in wanted and clip.skelId == 0x1AE94937]
        bones = [harness.FakeBone(name) for name in self.rig.names]
        with contextlib.redirect_stdout(io.StringIO()):
            original = animation.buildNoesisAnims(clips, bones, self.mapping, scale=100)
            repaired = animation.buildNoesisAnims(clips, bones, self.mapping, scale=100,
                                                 deformationRig=self.rig)
        self.assertEqual(len(original), 3)
        for before, after in zip(original, repaired):
            actual = {bone.boneIndex: bone for bone in after.kfBones}
            for bone in before.kfBones:
                counterpart = actual[bone.boneIndex]
                for channel in ('rotationKeys', 'translationKeys', 'scaleKeys'):
                    expected_keys = [(key.time, key.value.v) for key in getattr(bone, channel)]
                    actual_keys = [(key.time, key.value.v) for key in getattr(counterpart, channel)]
                    self.assertEqual(actual_keys, expected_keys, before.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
