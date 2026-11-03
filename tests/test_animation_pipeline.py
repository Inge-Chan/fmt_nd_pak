import contextlib
import io
import math
import os
import struct
import types
import unittest

from support import noesis_stub as harness
import inc_nd_anim as animation


class ByteReader:
    def __init__(self, data):
        self.data = data

    def u8(self, offset):
        return self.data[offset] if 0 <= offset < len(self.data) else None

    def u16(self, offset):
        if offset < 0 or offset + 2 > len(self.data):
            return None
        return struct.unpack_from('<H', self.data, offset)[0]

    def u32(self, offset):
        return struct.unpack_from('<I', self.data, offset)[0]


def packed_range(base, step):
    def bits(value):
        return struct.unpack('<I', struct.pack('<f', value))[0]

    step_word = ((bits(step[0]) & 0x7FFFFE00) << 33)
    step_word |= (bits(step[1]) & 0x7FFFFC00) << 11
    step_word |= (bits(step[2]) & 0x7FFFFC00) >> 10
    base_word = (bits(base[0]) & 0xFFFFFC00) << 32
    base_word |= (bits(base[1]) & 0xFFFFF800) << 10
    base_word |= (bits(base[2]) & 0xFFFFF800) >> 11
    return struct.pack('<QQ', step_word, base_word)


def fixture_clip(name='test', skeleton_id=1):
    clip = animation.NDAnimClip(name, animation.fnv1a64(name))
    clip.skelId = skeleton_id
    clip.numFrames = 1
    clip.clipOffset = 0
    clip._reader = ByteReader(bytes(16))
    segment = animation.NDAnimSegment(0, 1)
    clip.segments = [segment]
    return clip, segment


class CodecTests(unittest.TestCase):
    def test_uncompressed_constant_vec3_and_quaternion_layouts(self):
        data = bytearray(512)
        struct.pack_into('<4I', data, 0x30, 128, 2 << 16, 0, 0)
        commands = (0x0315, 8, 0, 64, 0x0317, 8, 0, 192, 0)
        struct.pack_into('<9H', data, 128, *commands)
        struct.pack_into('<8H', data, 192, 0, 32, 80, 96, 96, 96, 96, 96)
        vectors = [(0.5, 0.5, 0.5), (1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
        vectors.extend([(0.0, 0.0, 0.0)] * 5)
        for index, value in enumerate(vectors):
            struct.pack_into('<3f', data, 208 + 12 * index, *value)
        struct.pack_into('<8H', data, 320, 16, 64, 112, 112, 112, 112, 112, 112)
        for index in range(8):
            struct.pack_into('<4f', data, 336 + 16 * index, 0.0, 0.0, 0.0, 1.0)
        package = types.SimpleNamespace(bs=harness.FakeBitStream(bytes(data)))
        reader = animation.NDAnimReader(package)
        clip, unused = fixture_clip()
        clip.numSlots = 1
        segment = reader.readSegments(clip, 0)[0]
        self.assertFalse(segment.unsupportedOpcodes)
        self.assertEqual(segment.constPositions, [(0, (1.0, 2.0, 3.0)),
                                                 (1, (4.0, 5.0, 6.0))])
        self.assertEqual(segment.constScales, [(0, (0.5, 0.5, 0.5))])
        self.assertEqual(segment.constRots, [(0, (0.0, 0.0, 0.0, 1.0)),
                                           (1, (0.0, 0.0, 0.0, 1.0))])

    def test_range_record_matches_native_xyz_lane_order(self):
        base = (1.0, 2.0, 4.0)
        step = (0.5, 0.25, 0.125)
        actual = animation.decodeVec3Range(ByteReader(packed_range(base, step)), 0)
        self.assertEqual(actual, (base, step))

    def test_independent_component_widths(self):
        descriptor = 0x070F0807
        self.assertEqual(animation.vec3DescriptorWidths(descriptor), (7, 8, 7))
        window = ((3 << 15) | (129 << 7) | 7) << 42
        actual = animation.decodeRangeWindow(
            window, descriptor, ((1.0, 2.0, 3.0), (0.1, 0.01, 0.2)),
            (0.0, 0.0, 0.0))
        for value, expected in zip(actual, (1.3, 3.29, 4.4)):
            self.assertAlmostEqual(value, expected)

    def test_zero_width_components_use_previous_vector(self):
        descriptor = (2 << 6) | (1 << 14) | (3 << 30)
        actual = animation.decodeRangeWindow(
            0, descriptor, ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0)),
            (10.0, 20.0, 30.0))
        self.assertEqual(actual, (21.0, 12.0, 33.0))

    def test_invalid_descriptor_is_not_silently_decoded(self):
        with self.assertRaises(ValueError):
            animation.vec3DescriptorWidths(0x07000807)

    def test_bit_runs_include_zero_and_cross_byte_fields(self):
        reader = ByteReader(bytes.fromhex('ab cd ef 12 34 56 78 90 11'))
        self.assertEqual(animation.readBitRun(reader, 0, 0, 0), 0)
        self.assertEqual(animation.readBitRun(reader, 0, 4, 12), 0xBCD)
        self.assertEqual(animation.readBitRun(reader, 0, 0, 64), 0xABCDEF1234567890)
        self.assertIsNone(animation.readBitRun(reader, 1, 4, 64))

    def test_smallest_three_uses_swap_not_insertion(self):
        window = ((512 << 20) | (640 << 10) | 768) << 34
        quaternion = animation.decodeQuatSmallestThree(window, (10, 10, 10), 0)
        self.assertAlmostEqual(quaternion[3], 0.0)
        self.assertAlmostEqual(quaternion[1], math.sqrt(2.0) / 8.0)
        self.assertAlmostEqual(quaternion[2], math.sqrt(2.0) / 4.0)

    def test_pca_applies_left_and_right_references_in_order(self):
        clip, segment = fixture_clip()
        segment.kcWidths = [0]
        component = math.sqrt(0.5)
        references = ((component, 0.0, 0.0, component),
                      (0.0, component, 0.0, component))
        segment.decodeOperations = [
            ('range', [(0, 0, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))]),
            ('pca', [(0, references)]),
            ('rotation', [(0, 0)]),
        ]
        positions, rotations = animation.decodeSegmentFrames(clip._reader, clip, segment)
        self.assertFalse(positions)
        for component in rotations[0][0][1]:
            self.assertAlmostEqual(component, 0.5)

    def test_uncompressed_keycache_restores_native_word_order(self):
        clip, segment = fixture_clip()
        clip._reader = ByteReader(struct.pack('>7f', 4.0, 2.0, 1.0,
                                             1.0, 0.0, 0.0, 0.0))
        segment.bytesPerFrame = 28
        segment.kcWidths = [96, 128]
        segment.decodeOperations = [('translation', [(0, 0)]),
                                    ('rotation', [(1, 0)])]
        positions, rotations = animation.decodeSegmentFrames(clip._reader, clip, segment)
        self.assertEqual(positions[0][0][1], (1.0, 2.0, 4.0))
        self.assertEqual(rotations[0][0][1], (0.0, 0.0, 0.0, 1.0))

    def test_range_prediction_resets_between_blocks(self):
        clip, segment = fixture_clip()
        segment.kcWidths = [0, 0]
        segment.decodeOperations = [
            ('range', [(0, 0, ((10.0, 20.0, 30.0), (0.0, 0.0, 0.0)))]),
            ('range', [(1, 1 << 6, ((1.0, 2.0, 3.0), (0.0, 0.0, 0.0)))]),
            ('translation', [(1, 0)]),
        ]
        positions, rotations = animation.decodeSegmentFrames(clip._reader, clip, segment)
        self.assertEqual(positions[0][0][1], (1.0, 2.0, 3.0))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.bones = [harness.FakeBone('root')]

    def build(self, clips, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return animation.buildNoesisAnims(clips, self.bones, [0], **options)

    def test_different_skeleton_is_excluded(self):
        first, first_segment = fixture_clip('ellie', 1)
        second, second_segment = fixture_clip('other_actor', 2)
        first_segment.constPositions = [(0, (1.0, 2.0, 3.0))]
        second_segment.constPositions = [(0, (4.0, 5.0, 6.0))]
        result = self.build([first, second], targetSkelId=1)
        self.assertEqual([clip.name for clip in result], ['ellie'])

    def test_bad_clip_does_not_discard_valid_clip(self):
        first, first_segment = fixture_clip('unsupported')
        second, second_segment = fixture_clip('valid')
        first_segment.unsupportedOpcodes.add(26)
        second_segment.constPositions = [(0, (1.0, 2.0, 3.0))]
        result = self.build([first, second])
        self.assertEqual([clip.name for clip in result], ['valid'])

    def test_constant_scale_and_duration_are_preserved(self):
        clip, segment = fixture_clip()
        clip.numFrames = 31
        segment.constScales = [(0, (0.0, 0.0, 0.0))]
        result = self.build([clip])
        keys = result[0].kfBones[0].scaleKeys
        self.assertEqual(keys[0].value.v, [0.0, 0.0, 0.0])
        self.assertEqual(keys[-1].time, 1.0)

    def test_additive_preview_requires_explicit_enable_when_disabled(self):
        clip, segment = fixture_clip('pose-add')
        segment.constPositions = [(0, (0.0, 0.0, 0.0))]
        self.assertEqual(self.build([clip], allowAdditive=False), [])
        bind_pose = [((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0))]
        result = self.build([clip], allowAdditive=True, bindPose=bind_pose)
        self.assertEqual(result[0].kfBones[0].translationKeys[0].value.v,
                         [1.0, 2.0, 3.0])

    def test_clip_filter_and_deduplication(self):
        clip, segment = fixture_clip('wanted')
        other, other_segment = fixture_clip('other')
        segment.constPositions = [(0, (1.0, 2.0, 3.0))]
        other_segment.constPositions = [(0, (1.0, 2.0, 3.0))]
        result = self.build([clip, clip, other], clipName='wanted')
        self.assertEqual([entry.name for entry in result], ['wanted'])


@unittest.skipUnless(os.environ.get('ND_ANIM_TEST_ROOT'), 'real sample root not configured')
class RealSampleTests(unittest.TestCase):
    def test_native_t1x_inspect_regular_clips_decode(self):
        path = os.path.join(os.environ['ND_ANIM_TEST_ROOT'],
                            'anim-t1x-ellie-inspect.pak')
        reader = animation.NDAnimReader(harness.FakePak(path))
        clips = [clip for clip in reader.read()
                 if clip.skelId == 0x1AE94937 and not animation.clipIsAdditive(clip)]
        self.assertEqual(len(clips), 157)
        for clip in clips:
            self.assertFalse(clip.decodeIssues, clip.name)
            for segment in clip.segments:
                animation.decodeSegmentFrames(reader, clip, segment)

    def test_lose_balance_decodes_every_declared_motion_channel(self):
        path = os.path.join(os.environ['ND_ANIM_TEST_ROOT'],
                            'anim-ellie-lose-balance.pak')
        reader = animation.NDAnimReader(harness.FakePak(path))
        clips = reader.read()
        self.assertEqual(len(clips), 22)
        for clip in clips:
            self.assertFalse(clip.decodeIssues, clip.name)
            for segment in clip.segments:
                positions, rotations = animation.decodeSegmentFrames(reader, clip, segment)
                for entries, decoded in ((segment.posEntries, positions),
                                         (segment.rotEntries, rotations)):
                    for slot, joint in entries:
                        self.assertIn(joint, decoded, clip.name)
                        self.assertEqual(len(decoded[joint]), clip.numFrames, clip.name)
                for keys in rotations.values():
                    for frame, quaternion in keys:
                        norm = sum(component * component for component in quaternion)
                        self.assertAlmostEqual(norm, 1.0, delta=0.001, msg=clip.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
