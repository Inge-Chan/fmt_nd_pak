"""Decode supported Naughty Dog animation segments for the Noesis importer.

The active path is readSegments -> decodeSegmentFrames -> buildNoesisAnims.
Format rules, evidence and unresolved layouts are documented in
docs/animation-format.md and docs/status.md. Host FBX timing is a separate
validation layer, not a property inferred from successful keyframe decoding.
"""

from inc_noesis import *
import struct

ANIM_CLIP_MAGIC = 0x41430102
ANIM_DECODER_VERSION = '2026-11-03-cleanup'
SENTINEL = 74565            # 0x12345
RES_HDR_SIZE = 0x30
_FNV_BASIS = 0xcbf29ce484222325
_FNV_PRIME = 0x100000001b3
_U64 = (1 << 64) - 1


def fnv1a64(text):
    """The 64-bit string hash used for every ND StringID."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    h = _FNV_BASIS
    for b in text:
        h = ((h ^ b) * _FNV_PRIME) & _U64
    return h


TYPE_ANIM = fnv1a64("ANIM")
TYPE_ANIM_GROUP = fnv1a64("ANIM_GROUP")
TYPE_ANIM_RAWCHANNELS = fnv1a64("ANIM_RAWCHANNELS")


def halfToFloat(u16):
    """Decode an IEEE 754 binary16 value.

    Implemented by hand rather than with struct's 'e' format: Noesis embeds
    Python 3.2 (see plugins/python/core321.zip) and 'e' was only added in
    Python 3.6, so struct.unpack('<e', ...) raises
    "bad char in struct format" inside Noesis even though it works on a
    modern interpreter.
    """
    bits = u16 & 0xFFFF
    sign = -1.0 if (bits >> 15) else 1.0
    exponent = (bits >> 10) & 0x1F
    mantissa = bits & 0x3FF
    if exponent == 0:
        if mantissa == 0:
            return sign * 0.0
        # subnormal: 2^-24 * mantissa
        return sign * mantissa * 5.9604644775390625e-08
    if exponent == 0x1F:
        if mantissa:
            return float('nan')
        return sign * float('inf')
    return sign * (1.0 + mantissa / 1024.0) * (2.0 ** (exponent - 15))


SQRT1_2 = 0.7071067811865476


def quatScaleForWidth(width):
    """The per-component scale OrbisAnim derives from a field width.

    Verified against live hardware: for width 14 the engine's scale register held
    0x38B504F3 = 8.631674427e-05, and sqrt(2)/2^14 = 8.63167458e-05.
    """
    if width <= 0 or width > 32:
        return 0.0
    return 1.4142135623730951 / float(1 << width)


def decodeQuatSmallestThree(window, widths, index, scales=None):
    """OrbisAnim DecompressQuatSmallestThree - recovered from TLOU-I.exe and
    verified exactly against live register state (max error 1.8e-08, i.e.
    float32 rounding).

    `window` is a 64-bit integer holding the bit window; the three quantised
    components are packed contiguously from its TOP bit. `widths` is their bit
    widths, `index` (2 bits) says which quaternion component was dropped and is
    rebuilt from the others. `scales` may be supplied when they are read from the
    stream; otherwise they are derived from the widths.

    Field positions were confirmed live: with widths (14,14,14) the fields sit at
    bit 50, 36 and 22 of the window - spacing exactly equal to the width.
    """
    w0, w1, w2 = widths
    if min(widths) < 0 or max(widths) > 32 or sum(widths) > 62:
        raise ValueError('unsupported SmallestThree widths')
    mask64 = (1 << 64) - 1
    raw = (
        window >> (64 - w0),
        ((window << w0) & mask64) >> (64 - w1),
        ((window << (w0 + w1)) & mask64) >> (64 - w2),
    )
    if scales is None:
        scales = (quatScaleForWidth(w0), quatScaleForWidth(w1),
                  quatScaleForWidth(w2))
    comps = [raw[i] * scales[i] - SQRT1_2 for i in range(3)]
    remainder = 1.0 - (comps[0] * comps[0] + comps[1] * comps[1]
                       + comps[2] * comps[2])
    dropped = remainder ** 0.5 if remainder > 0.0 else 0.0
    # the 2-bit index selects the slot the rebuilt component goes back into
    slot = index & 3
    quaternion = comps + [dropped]
    quaternion[slot], quaternion[3] = quaternion[3], quaternion[slot]
    return NoeQuat(quaternion)


class NDAnimChannel:
    """One ANIM_RAWCHANNELS channel: a locator/float track, keyed per frame."""

    def __init__(self, nameHash, numFrames):
        self.nameHash = nameHash
        self.name = None            # resolved later if the hash is known
        self.numFrames = numFrames
        self.isCompressed = False   # True when the payload is not plain fp16
        self.rotations = []         # NoeQuat per frame
        self.translations = []      # NoeVec3 per frame
        self.scales = []            # NoeVec3 per frame


class NDAnimClip:
    """A single ANIM resource item."""

    def __init__(self, name, nameHash):
        self.name = name
        self.nameHash = nameHash
        self.skelId = None            # u32 at ANIM payload +0x20: target skeleton
        self.numFrames = 0
        self.frameRate = 30.0
        self.channels = []          # list of NDAnimChannel (locators)
        self.numSlots = 0
        self.clipOffset = None
        self.animatedJointCount = 0
        self.animatedJoints = []      # joint indices from the presence bitmask
        self._reader = None           # set by readClipHeader, for lazy decode
        self.segments = []            # NDAnimSegment per clip slot (see below)
        self.decodeIssues = []


class NDAnimSegment:
    """One clip slot = one OrbisAnim processing segment.

    A clip is split into slots along the skeleton's hierarchy groups; the tag
    byte in each slot header carries the segment's joint count, and segments
    cover the animated joints CONSECUTIVELY: segment s spans global joints
    [jbase, jbase + tagCount) where jbase is the sum of earlier tag counts.
    Global joint j maps to a bone through the skeleton's boneMap (the xform
    record order of JOINT_HIERARCHY).

    Verified by known plaintext: op22 constant translations against the bind
    pose translations of ellie-skel match with median error 1e-05 m and 95.4%
    under 1 mm across 43722 records (random pairing: 2.1%).

    All per-frame data is addressed by KEYCACHE SLOT (32 bytes each in the
    runtime scratch buffer): a command's operand[3] is its first slot * 32,
    and its table entry k lives at KeyCache slot operand[3]//32 + k. The op11
    width table is indexed by KeyCache slot as well, INCLUDING hole slots
    that no decompress command fills.
    """

    def __init__(self, jbase, tagCount):
        self.jbase = jbase            # first global joint of this segment
        self.tagCount = tagCount      # joints in this segment (slot tag byte)
        self.animOffset = 0           # per-frame data offset (slot u32[2])
        self.bytesPerFrame = 0        # frame stride (slot u32[3] & 0xFFFF)
        self.commands = []
        self.kcWidths = []            # op11: bit width per KeyCache slot
        self.kcRange = {}             # op27: kcSlot -> (base vec3, step vec3)
        self.kcRef = {}               # op29/op30: kcSlot -> reference quat
        self.posEntries = []          # op15: (kcSlot, global joint)
        self.rotEntries = []          # op16: (kcSlot, global joint)
        self.constRots = []           # op24: (global joint, quat)
        self.constPositions = []      # op22 mod48==32: (global joint, vec3)
        self.decodeOperations = []
        self.kcPca = {}
        self.unsupportedOpcodes = set()
        self.constScales = []
        self.frameDataOffset = 0


def _isFinite(pos):
    """Reject non-finite decoded values before they reach animation keys."""
    for v in pos:
        if v != v or v == float('inf') or v == float('-inf'):
            return False
    return True


# ---------------------------------------------------------------------------
# OrbisAnim command stream (verified: 3115/3115 clips parse to End)
#
#   位置    clip + slot[0].u32[0]      (槽表在 clip+0x30, 每项 16 字节)
#   格式    16 位命令字 (count << 8) | opcode, 后跟 count 个 u16 操作数
#   操作数  低 2 位是段索引, 高位是段内偏移
#
# 常量旋转 (opcode 24 ConstDecompressQuat48SmallestThree):
#   tableBase = clip + cmdOff + (operand[2] & ~3)
#   nj        = operand[0] & 0x7FF          含 8 路填充
#   u16 表    nj 项, 每项是输出偏移
#   dataBase  = tableBase + nj*2            6 字节/骨骼
#   真实骨骼数 = 表中末尾重复值之前的项数   解码器一次处理 8 个, 不足补齐
#
# 排除填充后在 1682 个 clip / 31837 条记录上合法率 100%(随机偏移对照 59.3%),
# 角度中位 0 度、单位四元数占 52.4% —— 与真实骨架吻合。
# ---------------------------------------------------------------------------

OP_CONST_QUAT48 = 24
OP_CONST_VEC3_F32 = 21
OP_CONST_QUAT_F32 = 23
OP_CONST_VEC3_FP16 = 22  # constant vec3, fp16 triplets: scale (out mod48==0)
                         # or translation (out mod48==32)
OP_VEC3_RANGE = 27
OP_QUAT_SMALLEST_THREE = 28
OP_KC_LERP = 15          # positions
OP_KC_SLERP = 16         # rotations
OP_POST_QUAT_LOG = 29    # reference quaternions (s16 interleaved)
OP_POST_QUAT_LOG_PCA = 30
OP_COPY_UNIFORM = 11     # per-joint bit-width table
_QUAT_SCALE15 = 1.4142135623730951 / float(1 << 15)


def parseCommandStream(reader, start, limit=96):
    """Walk the 16-bit command stream; returns [(opcode, operands), ...]."""
    out = []
    pos = start
    for _ in range(limit):
        word = reader.u16(pos)
        if word is None:
            break
        opcode = word & 0xFF
        count = word >> 8
        if opcode == 0:
            break
        if count > 12:
            break
        ops = []
        ok = True
        for k in range(count):
            v = reader.u16(pos + 2 + 2 * k)
            if v is None:
                ok = False
                break
            ops.append(v)
        if not ok:
            break
        out.append((opcode, ops))
        pos += 2 + 2 * count
    return out


def decodeConstQuat(reader, offset):
    """One 6-byte Quat48SmallestThree record -> (x, y, z, w) or None.

    Two subtleties, both proven against ground truth (the 75 const rotations
    of ellie's bindpose-anim clip vs the bind quaternions, max error < 0.1
    deg; the naive readings scored 9.9 deg median / 180 deg max):
      * field order is REVERSED - the field at shift 33 is x, shift 2 is z;
      * (v & 3) is a SWAP index, not an insert position: the encoder swapped
        component idx with w before storing (x, y, z), so decode rebuilds
        (x, y, z, sqrt) and swaps idx <-> w back. idx == 3 is the no-op case.
    """
    lo = reader.u32(offset)
    hi = reader.u16(offset + 4)
    if lo is None or hi is None:
        return None
    v = lo | (hi << 32)
    index = v & 3
    comps = []
    for shift in (33, 17, 2):
        comps.append(((v >> shift) & 0x7FFF) * _QUAT_SCALE15 - SQRT1_2)
    rem = 1.0 - (comps[0] * comps[0] + comps[1] * comps[1] + comps[2] * comps[2])
    if rem < 0.0:
        return None
    q = [comps[0], comps[1], comps[2], rem ** 0.5]
    if index != 3:
        q[index], q[3] = q[3], q[index]
    return tuple(q)


def _bitsToFloat(bits):
    """Reinterpret a 32-bit pattern as float32 without struct's 'f' on bytes."""
    bits &= 0xFFFFFFFF
    sign = -1.0 if (bits >> 31) else 1.0
    exp = (bits >> 23) & 0xFF
    man = bits & 0x7FFFFF
    if exp == 0:
        return sign * man * (2.0 ** -149)
    if exp == 255:
        return float('nan') if man else sign * float('inf')
    return sign * (8388608 + man) * (2.0 ** (exp - 150))


def decodeVec3Range(reader, offset):
    """op27 range params: 16 bytes -> (base vec3, step vec3).

    The three components are packed in two u64 and extracted with shifts and
    masks that clear the low mantissa bits, i.e. truncated float bit patterns
    (masks 0x7FFFFE00 / 0x7FFFFC00 / 0xFFFFFC00 / 0xFFFFF800, from 0x142875C48).
    """
    a0 = reader.u32(offset)
    a1 = reader.u32(offset + 4)
    b0 = reader.u32(offset + 8)
    b1 = reader.u32(offset + 12)
    if a0 is None or a1 is None or b0 is None or b1 is None:
        return None
    a = a0 | (a1 << 32)
    b = b0 | (b1 << 32)
    step = (_bitsToFloat((a >> 33) & 0x7FFFFE00),
            _bitsToFloat((a >> 11) & 0x7FFFFC00),
            _bitsToFloat((a << 10) & 0x7FFFFC00))
    base = (_bitsToFloat((b >> 32) & 0xFFFFFC00),
            _bitsToFloat((b >> 10) & 0xFFFFF800),
            _bitsToFloat((b << 11) & 0xFFFFFFFF))
    for v in base + step:
        if v != v or v in (float('inf'), float('-inf')):
            return None
    return base, step


def readBitRun(reader, byteBase, bitPos, nbits):
    """Big-endian bit run of nbits starting at bitPos within a frame.

    Mirrors the game's bit-shifted copy (0x142877E37 / 0x1428756C4):
        dst[i] = (src[i] << off) | (src[i+1] >> (8 - off))
    """
    if nbits == 0:
        return 0
    if nbits < 0 or nbits > 128:
        return None
    start = byteBase + (bitPos >> 3)
    off = bitPos & 7
    need = (off + nbits + 7) >> 3
    v = 0
    for k in range(need):
        byte = reader.u8(start + k)
        if byte is None:
            return None
        v = (v << 8) | byte
    v >>= (need * 8 - off - nbits)
    return v & ((1 << nbits) - 1)


def vec3DescriptorWidths(descriptor):
    widths = tuple((descriptor >> shift) & 0x3F for shift in (0, 8, 24))
    prefix = (descriptor >> 16) & 0xFF
    if prefix != widths[0] + widths[1]:
        raise ValueError('invalid Vec3 descriptor prefix')
    if max(widths) > 32 or sum(widths) > 64:
        raise ValueError('unsupported Vec3 descriptor widths')
    return widths


def decodeRangeWindow(window, descriptor, parameters, previous):
    widths = vec3DescriptorWidths(descriptor)
    base, step = parameters
    controls = tuple((descriptor >> shift) & 3 for shift in (6, 14, 30))
    consumed = 0
    result = []
    for axis, width in enumerate(widths):
        consumed += width
        quantised = (window >> (64 - consumed)) & ((1 << width) - 1)
        if quantised >= 0x80000000:
            quantised -= 0x100000000
        reference = previous[controls[axis] - 1] if controls[axis] else 0.0
        result.append(base[axis] + quantised * step[axis] + reference)
    return tuple(result)


_S16_SCALE = 1.0 / float(1 << 15)


def _s16(value):
    return value - 0x10000 if value >= 0x8000 else value


def decodeRefQuats(reader, offset, count):
    """op29's reference-quaternion table.

    Layout (solved from a live dump, then confirmed on files): every 16 bytes
    holds TWO interleaved quaternions, s16 scaled by 1/2**15 --
        q0 = (s16[0], s16[2], s16[4], s16[6])
        q1 = (s16[1], s16[3], s16[5], s16[7])
    Verified: 24132/24132 quaternions are unit (|q| ~ 1) across 1378 clips,
    versus 6.8% for a random offset. The same location read as consecutive
    8-byte quaternions scores only 5.8% - the layout is what matters.
    """
    out = []
    blocks = (count + 1) // 2
    for k in range(blocks):
        base = offset + 16 * k
        raw = []
        for j in range(8):
            v = reader.u16(base + 2 * j)
            if v is None:
                return out[:count]
            raw.append(_s16(v))
        out.append((raw[0] * _S16_SCALE, raw[2] * _S16_SCALE,
                    raw[4] * _S16_SCALE, raw[6] * _S16_SCALE))
        out.append((raw[1] * _S16_SCALE, raw[3] * _S16_SCALE,
                    raw[5] * _S16_SCALE, raw[7] * _S16_SCALE))
    return out[:count]


def expQuat(v):
    """exp of a log quaternion: q = (v*sin(t)/t, cos(t)), t = |v|."""
    t = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if t < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    import math as _m
    s = _m.sin(t) / t
    return (v[0] * s, v[1] * s, v[2] * s, _m.cos(t))


def quatMul(a, b):
    """Hamilton product, xyzw order."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def decodeSegmentFrames(reader, clip, seg):
    """Decode one segment's per-frame positions and rotations.

    Returns (posKeys, rotKeys), indexed by global joint. Each key is a
    (frame, value) pair. Decompression and scatter follow command order;
    range prediction resets for each op27 block. Both compressed windows
    and native 96/128-bit float values are supported.
    """
    posKeys = {}
    rotKeys = {}
    if seg.unsupportedOpcodes:
        raise ValueError('unsupported skeletal opcodes: %s' %
                         sorted(seg.unsupportedOpcodes))
    if not seg.decodeOperations:
        return posKeys, rotKeys
    if clip.numFrames < 1 or not seg.kcWidths:
        raise ValueError('missing frame count or KeyCache width table')
    bitOffsets = [0]
    for width in seg.kcWidths:
        bitOffsets.append(bitOffsets[-1] + width)
    if seg.frameDataOffset * 8 + bitOffsets[-1] > seg.bytesPerFrame * 8:
        raise ValueError('KeyCache bit widths exceed the frame stride')

    for frame in range(clip.numFrames):
        frameBase = (clip.clipOffset + seg.animOffset +
                     frame * seg.bytesPerFrame + seg.frameDataOffset)
        cache = {}
        for slot, width in enumerate(seg.kcWidths):
            if width in (96, 128):
                packed = readBitRun(reader, frameBase, bitOffsets[slot], width)
                if packed is None:
                    raise ValueError('truncated uncompressed KeyCache value')
                cache[slot] = tuple(_bitsToFloat((packed >> (32 * axis)) & 0xFFFFFFFF)
                                    for axis in range(width // 32))

        def bitWindow(slot):
            if not 0 <= slot < len(seg.kcWidths):
                raise ValueError('KeyCache slot %d has no width' % slot)
            width = seg.kcWidths[slot]
            if width > 64:
                raise ValueError('compressed operation uses an uncompressed slot')
            packed = readBitRun(reader, frameBase, bitOffsets[slot], width)
            if packed is None:
                raise ValueError('truncated or unsupported KeyCache bit window')
            return packed << (64 - width)

        for operation, entries in seg.decodeOperations:
            if operation == 'range':
                previous = (0.0, 0.0, 0.0)
                for slot, descriptor, parameters in entries:
                    previous = decodeRangeWindow(bitWindow(slot), descriptor,
                                                 parameters, previous)
                    cache[slot] = previous
            elif operation == 'smallest':
                for slot, descriptor in entries:
                    widths = tuple((descriptor >> shift) & 0xFF
                                   for shift in (0, 8, 24))
                    prefix = (descriptor >> 16) & 0xFF
                    if prefix != widths[0] + widths[1]:
                        raise ValueError('invalid SmallestThree descriptor')
                    window = bitWindow(slot)
                    total = sum(widths) + 2
                    if total > 64:
                        raise ValueError('SmallestThree descriptor exceeds 64 bits')
                    index = (window >> (64 - total)) & 3
                    quaternion = decodeQuatSmallestThree(window, widths, index)
                    cache[slot] = tuple(quaternion[axis] for axis in range(4))
            elif operation in ('log', 'pca'):
                for slot, reference in entries:
                    value = cache.get(slot)
                    if value is None or len(value) != 3:
                        raise ValueError('rotation slot %d has no decoded log vector' % slot)
                    if operation == 'pca':
                        cache[slot] = quatMul(quatMul(reference[0], expQuat(value)),
                                              reference[1])
                    else:
                        cache[slot] = quatMul(reference, expQuat(value))
            elif operation in ('translation', 'rotation'):
                destination = posKeys if operation == 'translation' else rotKeys
                dimensions = 3 if operation == 'translation' else 4
                for slot, joint in entries:
                    value = cache.get(slot)
                    if value is None or len(value) != dimensions or not _isFinite(value):
                        raise ValueError('joint %d has no valid %s value' %
                                         (joint, operation))
                    destination.setdefault(joint, []).append((frame, value))
    return posKeys, rotKeys


class NDAnimReader:
    """Parses animation resources out of an already-header-parsed PakFile.

    `pak` must be a fmt_nd_pak.PakFile whose readPakHeader() has run, so that
    pakPageEntries / pointerPageIds are populated.
    """

    def __init__(self, pak):
        self.pak = pak
        self.bs = pak.bs
        self.data = None
        self.clips = []

    # ---------------------------------------------------------------- helpers
    def _bytes(self):
        if self.data is None:
            here = self.bs.tell()
            self.bs.seek(0)
            self.data = self.bs.readBytes(self.bs.getSize())
            self.bs.seek(here)
        return self.data

    def u8(self, offset):
        data = self._bytes()
        if offset < 0 or offset >= len(data):
            return None
        b = data[offset]
        return b if isinstance(b, int) else ord(b)

    def u16(self, offset):
        data = self._bytes()
        if offset < 0 or offset + 2 > len(data):
            return None
        return struct.unpack_from("<H", data, offset)[0]

    def u32(self, offset):
        return struct.unpack_from("<I", self._bytes(), offset)[0]

    def u64(self, offset):
        return struct.unpack_from("<Q", self._bytes(), offset)[0]

    def f32(self, offset):
        return struct.unpack_from("<f", self._bytes(), offset)[0]

    def cstr(self, offset):
        data = self._bytes()
        if offset <= 0 or offset >= len(data):
            return ""
        end = data.find(b"\0", offset)
        if end < 0:
            end = len(data)
        return data[offset:end].decode("utf-8", "replace")

    def ptrAt(self, address):
        """Resolve a pointer-fixup'd 64-bit offset stored at `address`.

        A stored value of 0 is LEGAL when the address has a pointer-fixup entry:
        it means "offset 0 within the target page", i.e. the very start of that
        page. Treating 0 as null loses real clips - e.g.
        soldier-loses-balance-big-in-place-02 lives at page offset 0 and was
        being dropped, which is what made its frame count read as 0. This
        mirrors the plugin's own readPointerFixup(TP1ZeroCondition=True).
        """
        raw = struct.unpack_from("<q", self._bytes(), address)[0]
        if raw < 0:
            return None
        entry = self.pak.pointerPageIds.get(address)
        if entry is None:
            # Without a fixup record a bare 0 really is a null pointer.
            return None
        pageIdx = entry[0]
        if pageIdx >= len(self.pak.pakPageEntries):
            return None
        return raw + self.pak.pakPageEntries[pageIdx][0]

    # ------------------------------------------------------------- res items
    def findResItems(self):
        """Locate every resource item header via the doubled 0x12345 sentinel.

        The pak login table only exposes top-level items; animation items are
        nested, so a sentinel scan is what actually finds them all.
        """
        data = self._bytes()
        pattern = struct.pack("<II", SENTINEL, SENTINEL)
        found = []
        pos = 0
        while True:
            idx = data.find(pattern, pos)
            if idx < 0:
                break
            pos = idx + 1
            base = idx - 8
            if base < 0 or base + RES_HDR_SIZE > len(data):
                continue
            if data[base + 0x28:base + 0x30] != pattern:
                continue
            if struct.unpack_from("<I", data, base + 0x14)[0] != SENTINEL:
                continue
            found.append(base)
        return found

    def resItemInfo(self, base):
        nameRel = self.u64(base)
        name = ""
        if 0 < nameRel < len(self._bytes()) - base:
            name = self.cstr(base + nameRel)
        return {
            "base": base,
            "data": base + RES_HDR_SIZE,
            "dataSize": self.u32(base + 0x10),
            "nameHash": self.u64(base + 0x18),
            "typeHash": self.u64(base + 0x20),
            "name": name,
        }

    # ---------------------------------------------------------- raw channels
    def readRawChannels(self, payload, numFramesHint=0):
        """Parse an ANIM_RAWCHANNELS payload into NDAnimChannel objects."""
        out = []
        count = self.u32(payload)
        if count <= 0 or count > 4096:
            return out
        hashesPtr = self.ptrAt(payload + 8)
        listPtr = self.ptrAt(payload + 16)
        if hashesPtr is None or listPtr is None:
            return out

        for i in range(count):
            try:
                nameHash = self.u64(hashesPtr + 8 * i)
                chPtr = self.ptrAt(listPtr + 8 * i)
            except Exception:
                break
            if chPtr is None:
                continue
            numFrames = self.u16(chPtr)
            if numFrames <= 0 or numFrames > 0x8000:
                numFrames = numFramesHint or 1
            dataPtr = self.ptrAt(chPtr + 8)
            channel = NDAnimChannel(nameHash, numFrames)
            if dataPtr is not None:
                self._readChannelFrames(channel, dataPtr, numFrames)
            out.append(channel)
        return out

    def _readChannelFrames(self, channel, dataPtr, numFrames):
        """Decode locator frames.

        For single-frame channels the payload is a plain fp16 record:
        quat(xyzw), translation(xyz), scale(xyz) - verified to give
        unit-length quaternions on every bindpose clip tested.

        Multi-frame channels are NOT a plain fp16 array: testing candidate
        strides of 6..48 bytes over thousands of channels never exceeded a
        ~22% unit-quaternion rate, so those are compressed too and are left
        undecoded rather than emitting wrong values.
        """
        limit = len(self._bytes())
        if numFrames != 1:
            channel.isCompressed = True
            return
        if dataPtr + 20 > limit:
            return
        v = [halfToFloat(self.u16(dataPtr + 2 * k)) for k in range(10)]
        quat = NoeQuat((v[0], v[1], v[2], v[3]))
        length = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2] + v[3] * v[3]) ** 0.5
        if not (0.9 < length < 1.1):
            # layout assumption does not hold here - do not emit garbage
            channel.isCompressed = True
            return
        channel.rotations.append(quat)
        channel.translations.append(NoeVec3((v[4], v[5], v[6])))
        channel.scales.append(NoeVec3((v[7], v[8], v[9])))

    # ----------------------------------------------------------------- clips
    def readClipHeader(self, clip, clipOffset):
        """Fill frame count / rate / slot count from a clip block header."""
        if self.u32(clipOffset) != ANIM_CLIP_MAGIC:
            return False
        clip.clipOffset = clipOffset
        clip.numSlots = self.u16(clipOffset + 0x0C)
        clip.numFrames = self.u16(clipOffset + 0x0E)
        clip.frameRate = self.f32(clipOffset + 0x1C)
        if not (0.0 < clip.frameRate <= 1000.0):
            clip.frameRate = 30.0
        clip.animatedJointCount = self._countAnimatedJoints(clipOffset, clip.numSlots)
        clip.animatedJoints = self._maskedJoints(clipOffset, clip.numSlots)
        clip._reader = self
        self.readSegments(clip, clipOffset)
        return True

    def readSegments(self, clip, clipOffset):
        """Parse EVERY slot's command stream into an NDAnimSegment.

        Slot table at clip+0x30, 16 bytes per slot:
            u32 cmdOffset, u32 constAux, u32 animOffset, u32 animAux
        (constAux >> 16) & 0xFF is the segment's JOINT COUNT; segments cover
        the animated joints consecutively (see NDAnimSegment docstring).
        Slots whose block is not an OrbisAnim command stream (const-only or
        empty blocks) simply produce a segment with no commands.
        """
        clip.segments = []
        jbase = 0
        for s in range(clip.numSlots):
            so = clipOffset + 0x30 + 16 * s
            cmdOff = self.u32(so)
            constAux = self.u32(so + 4)
            animOff = self.u32(so + 8)
            animAux = self.u32(so + 12)
            if constAux is None or animAux is None:
                break
            seg = NDAnimSegment(jbase, (constAux >> 16) & 0xFF)
            jbase += seg.tagCount
            seg.animOffset = animOff or 0
            seg.bytesPerFrame = (animAux or 0) & 0xFFFF
            clip.segments.append(seg)
            if not cmdOff:
                continue
            try:
                cmds = parseCommandStream(self, clipOffset + cmdOff)
            except Exception:
                cmds = []
            seg.commands = cmds
            cmdBase = clipOffset + cmdOff
            for opcode, ops in cmds:
                if len(ops) < 3:
                    continue
                n = ops[0] & 0x7FF
                if not (1 <= n <= 512):
                    continue
                tableBase = cmdBase + (ops[2] & ~3)
                kc0 = (ops[3] >> 5) if len(ops) >= 4 else 0
                if opcode == OP_COPY_UNIFORM:
                    widths = []
                    widthStride = 1 if ops[0] & 0x8000 else 0
                    for j in range(n):
                        w = self.u8(tableBase + widthStride * j)
                        if w is None or w > 128:
                            widths = []
                            break
                        widths.append(w)
                    if widths and not seg.kcWidths:
                        seg.kcWidths = widths
                        seg.frameDataOffset = ops[4] if len(ops) > 4 else 0
                    elif widths:
                        seg.unsupportedOpcodes.add(OP_COPY_UNIFORM)
                elif opcode == OP_VEC3_RANGE:
                    records = []
                    descriptorBase = tableBase + 16 * n
                    descriptorStride = 4 if ops[0] & 0x8000 else 0
                    for j in range(n):
                        rp = decodeVec3Range(self, tableBase + 16 * j)
                        if rp is None:
                            raise ValueError('invalid Vec3 range parameters')
                        seg.kcRange[kc0 + j] = rp
                        descriptor = self.u32(descriptorBase + descriptorStride * j)
                        vec3DescriptorWidths(descriptor)
                        records.append((kc0 + j, descriptor, rp))
                    seg.decodeOperations.append(('range', records))
                elif opcode == OP_QUAT_SMALLEST_THREE:
                    stride = 4 if ops[0] & 0x8000 else 0
                    records = [(kc0 + index, self.u32(tableBase + stride * index))
                               for index in range(n)]
                    seg.decodeOperations.append(('smallest', records))
                elif opcode == OP_POST_QUAT_LOG:
                    quats = decodeRefQuats(self, tableBase, n)
                    if len(quats) != n:
                        raise ValueError('truncated reference quaternion table')
                    for j, q in enumerate(quats):
                        seg.kcRef[kc0 + j] = q
                    seg.decodeOperations.append(('log', [(kc0 + index, quaternion)
                                                        for index, quaternion in enumerate(quats)]))
                elif opcode == OP_POST_QUAT_LOG_PCA:
                    quats = decodeRefQuats(self, tableBase, 2 * n)
                    if len(quats) != 2 * n:
                        raise ValueError('truncated PCA reference quaternion table')
                    records = []
                    for index in range(n):
                        pair = (quats[2 * index], quats[2 * index + 1])
                        seg.kcPca[kc0 + index] = pair
                        records.append((kc0 + index, pair))
                    seg.decodeOperations.append(('pca', records))
                elif opcode == OP_KC_LERP:
                    entries = []
                    for k in range(n):
                        v = self.u16(tableBase + 2 * k)
                        # entries not pointing at a translation field (+32) are
                        # hole/padding slots - keep the KeyCache slot number
                        # right by skipping just the entry, never the offset
                        if v is None or (v % 48) != 32 or (v // 48) >= seg.tagCount:
                            continue
                        seg.posEntries.append((kc0 + k, seg.jbase + v // 48))
                        entries.append((kc0 + k, seg.jbase + v // 48))
                    seg.decodeOperations.append(('translation', entries))
                elif opcode == OP_KC_SLERP:
                    entries = []
                    for k in range(n):
                        v = self.u16(tableBase + 2 * k)
                        if v is None or (v % 48) != 16 or (v // 48) >= seg.tagCount:
                            continue
                        seg.rotEntries.append((kc0 + k, seg.jbase + v // 48))
                        entries.append((kc0 + k, seg.jbase + v // 48))
                    seg.decodeOperations.append(('rotation', entries))
                elif opcode in (OP_CONST_VEC3_F32, OP_CONST_QUAT_F32):
                    dimensions = 3 if opcode == OP_CONST_VEC3_F32 else 4
                    dataBase = tableBase + 2 * n
                    for index in range(n):
                        output = self.u16(tableBase + 2 * index)
                        if output is None or output // 48 >= seg.tagCount:
                            continue
                        field = output % 48
                        if dimensions == 4 and field != 16:
                            continue
                        if dimensions == 3 and field not in (0, 32):
                            continue
                        value = tuple(self.f32(dataBase + dimensions * 4 * index + axis * 4)
                                      for axis in range(dimensions))
                        if not _isFinite(value):
                            raise ValueError('non-finite uncompressed constant')
                        if dimensions == 4:
                            destination = seg.constRots
                        else:
                            destination = seg.constScales if field == 0 else seg.constPositions
                        destination.append((seg.jbase + output // 48, value))
                elif opcode == OP_CONST_QUAT48:
                    dataBase = tableBase + 2 * n
                    for j in range(n):
                        v = self.u16(tableBase + 2 * j)
                        if v is None or (v % 48) != 16 or (v // 48) >= seg.tagCount:
                            continue
                        q = decodeConstQuat(self, dataBase + 6 * j)
                        if q is not None:
                            seg.constRots.append((seg.jbase + v // 48, q))
                elif opcode == OP_CONST_VEC3_FP16:
                    dataBase = tableBase + 2 * n
                    for j in range(n):
                        v = self.u16(tableBase + 2 * j)
                        if v is None or (v % 48) not in (0, 32) or (v // 48) >= seg.tagCount:
                            continue
                        raw = (self.u16(dataBase + 6 * j),
                               self.u16(dataBase + 6 * j + 2),
                               self.u16(dataBase + 6 * j + 4))
                        if None in raw:
                            continue
                        vec = (halfToFloat(raw[0]), halfToFloat(raw[1]),
                               halfToFloat(raw[2]))
                        if _isFinite(vec):
                            destination = seg.constScales if v % 48 == 0 else seg.constPositions
                            destination.append((seg.jbase + v // 48, vec))
                elif opcode not in (12, 17, 25):
                    seg.unsupportedOpcodes.add(opcode)
        return clip.segments

    def _maskedJoints(self, clipOffset, numSlots):
        """Joint indices from the presence bitmask (LSB-first within each byte).

        Verified on bindpose-anim: 77 set bits, all inside the skeleton's joint
        range, forming contiguous groups - i.e. this really is a joint list.
        """
        if numSlots <= 0:
            return []
        tableEnd = 0x30 + 16 * numSlots
        offsets = [self.u32(clipOffset + 0x30 + 16 * i) for i in range(numSlots)]
        offsets = [o for o in offsets if o >= tableEnd]
        if not offsets:
            return []
        first = min(offsets)
        data = self._bytes()
        lo, hi = clipOffset + tableEnd, clipOffset + first
        if hi > len(data) or hi <= lo:
            return []
        out = []
        for byteIdx, byte in enumerate(data[lo:hi]):
            for bit in range(8):
                if (byte >> bit) & 1:
                    out.append(byteIdx * 8 + bit)
        return out

    def _countAnimatedJoints(self, clipOffset, numSlots):
        """popcount of the joint-presence bitmask between the slot table and
        the first data block. Verified to give ~2-8 bytes/joint/frame."""
        if numSlots <= 0:
            return 0
        tableEnd = 0x30 + 16 * numSlots
        offsets = []
        for i in range(numSlots):
            off = self.u32(clipOffset + 0x30 + 16 * i)
            if off >= tableEnd:
                offsets.append(off)
        if not offsets:
            return 0
        first = min(offsets)
        lo = clipOffset + tableEnd
        hi = clipOffset + first
        data = self._bytes()
        if hi > len(data) or hi <= lo:
            return 0
        return sum(bin(b).count("1") for b in data[lo:hi])

    # ------------------------------------------------------------------ main
    def read(self):
        """Parse every ANIM item in the pak. Returns the list of NDAnimClip."""
        self.clips = []
        for base in self.findResItems():
            info = self.resItemInfo(base)
            if info["typeHash"] != TYPE_ANIM:
                continue
            clip = NDAnimClip(info["name"] or ("anim_%016X" % info["nameHash"]),
                              info["nameHash"])
            payload = info["data"]
            # +0x20 of the ANIM payload is the target skeleton's id; the same
            # 32-bit value appears inside the matching *-skel.pak. Clips in
            # one pak can target different skeletons (props, other actors).
            clip.skelId = self.u32(payload + 0x20)
            clipOffset = self.ptrAt(payload + 8)
            if clipOffset is not None and clipOffset + 0x30 < len(self._bytes()):
                try:
                    self.readClipHeader(clip, clipOffset)
                except Exception as e:
                    clip.decodeIssues.append(str(e))
                    print("  [NDAnim] clip header failed for", clip.name, e)
            rawPtr = self.ptrAt(payload + 16)
            if rawPtr is not None:
                try:
                    clip.channels = self.readRawChannels(rawPtr, clip.numFrames)
                except Exception as e:
                    print("  [NDAnim] rawchannels failed for", clip.name, e)
            self.clips.append(clip)
        return self.clips

    # ------------------------------------------------------------- reporting
    def printSummary(self, maxRows=40):
        """Report parsed headers without claiming export or pose correctness."""
        print("[NDAnim] parsed %d clip headers" % len(self.clips))
        print(" %-40s %-8s %-7s %-6s %-8s %s"
              % ("clip", "SkelID", "frames", "fps", "segments", "issues"))
        for clip in self.clips[:maxRows]:
            print(" %-40s %08X %-7d %-6.4g %-8d %d"
                  % (clip.name[:40], clip.skelId or 0, clip.numFrames,
                     clip.frameRate, len(clip.segments), len(clip.decodeIssues)))
        if len(self.clips) > maxRows:
            print(" ... and %d more" % (len(self.clips) - maxRows))
        print("[NDAnim] header scan does not validate keyframes or FBX timing")


def bindPoseFromJointHierarchy(pak, reader, padSize=48):
    """Per-joint bind pose ((tx,ty,tz), (qx,qy,qz,qw)) in xform-record order,
    i.e. ANIM JOINT order - raw file space (parent-local, meters).

    Needed to compose ADDITIVE clips: their channels store deltas relative to
    the bind pose, so played absolute they collapse the skeleton onto the
    floor (identity rotations, near-zero translations)."""
    for source in (pak, getattr(pak, "basePak", None)):
        if source is None:
            continue
        jointOffset = getattr(source, "jointOffset", None)
        if not jointOffset:
            continue
        sourceReader = reader if source is pak else NDAnimReader(source)
        try:
            resItemOffset, start = jointOffset
            cursor = resItemOffset + start + 20 + padSize
            cursor += 4 + 8
            xformsOffset = sourceReader.ptrAt(cursor)
            if xformsOffset is None:
                continue
            xformCount = sourceReader.u16(xformsOffset + 18)
            headerSize = sourceReader.u32(xformsOffset + 32)
            transformsStart = xformsOffset + headerSize
            out = []
            for b in range(xformCount):
                base = transformsStart + 48 * b
                out.append(((sourceReader.f32(base + 32),
                             sourceReader.f32(base + 36),
                             sourceReader.f32(base + 40)),
                            (sourceReader.f32(base + 16),
                             sourceReader.f32(base + 20),
                             sourceReader.f32(base + 24),
                             sourceReader.f32(base + 28))))
            if out:
                return out
        except Exception as e:
            print("[NDAnim] bind-pose read failed:", e)
    return []


def clipIsAdditive(clip):
    """Additive clips carry DELTAS from the bind pose, not absolute values.

    Data rule: a real clip's constant translations are bone lengths (95% of
    them match the bind pose), an additive clip's are near-zero deltas. Name
    fallback for clips without constant positions: ND names additive clips
    with an -add suffix ('bindpose-add', 'facial-half-blink--add')."""
    n = 0
    z = 0
    for seg in clip.segments:
        for _, v in seg.constPositions:
            n += 1
            if abs(v[0]) < 1e-3 and abs(v[1]) < 1e-3 and abs(v[2]) < 1e-3:
                z += 1
    if n >= 8:
        return z >= 0.7 * n
    nm = clip.name or ""
    return nm.endswith("-add") or "--add" in nm or "-add-" in nm


def resolveChannelNames(clips, boneList=None):
    """Resolve channel name hashes using the known locator vocabulary plus,
    when available, the model's own bone names."""
    vocab = ["align", "apReference", "apReference2", "apReference3",
             "apReferenceGround", "alignGround", "mover", "root", "world"]
    lookup = {fnv1a64(v): v for v in vocab}
    if boneList:
        for bone in boneList:
            lookup[fnv1a64(bone.name)] = bone.name
    for clip in clips:
        for channel in clip.channels:
            channel.name = lookup.get(channel.nameHash)
    return clips


def _keyFramedBonesFromDecode(clip, boneList, boneMap=None, scale=1.0,
                            bindPose=None, deformationRig=None):
    """Turn the decoded per-segment streams into NoeKeyFramedBone objects.

    Global joint j (segment jbase + local slot) indexes the skeleton's xform
    records; boneMap translates that to a bone-list index (records follow the
    hierarchy's "main bone" order, which is what fmt_nd_pak stores in
    pak.boneMap). Without a boneMap the joint index is used directly.

    Keyframe values must live in the SAME convention the bone matrices were
    built with (fmt_nd_pak: mat = NoeQuat(fileQuat).transpose().toMat43(),
    mat[3] = filePos * GlobalScale). Raw file values sent as keys collapse
    the whole skeleton into a ball at 1/GlobalScale size with inverted
    rotations - hence the transpose() and the scale factor here.

    ADDITIVE clips (deltas from the bind pose) are composed onto bindPose
    (t = t_bind + t_delta, q = q_bind * q_delta) so they play as poses
    instead of collapsing the skeleton onto the floor.

    Constant channels (op22 translations / op24 rotations) are emitted as a
    single key so constant-posed joints hold their pose instead of the bind
    pose. Bones without any data keep their bind pose untouched - filling
    them from boneList matrices is wrong (those are world-space).
    """
    reader = getattr(clip, '_reader', None)
    if getattr(clip, 'decodeIssues', None):
        raise ValueError('; '.join(clip.decodeIssues))
    if reader is None or not clip.segments:
        return []
    nBones = len(boneList)
    dt = 1.0 / clip.frameRate if clip.frameRate > 0 else 1.0 / 30.0
    addMode = bool(bindPose) and clipIsAdditive(clip)

    def toBone(joint):
        if boneMap is not None:
            if 0 <= joint < len(boneMap):
                b = boneMap[joint]
            else:
                raise ValueError('joint %d is outside the skeleton mapping' % joint)
        else:
            b = joint
        if not 0 <= b < nBones:
            raise ValueError('joint %d has no matching target bone' % joint)
        return b

    def keyVec(t, joint, p):
        if addMode and joint < len(bindPose):
            bt = bindPose[joint][0]
            p = (bt[0] + p[0], bt[1] + p[1], bt[2] + p[2])
        return NoeKeyFramedValue(t, NoeVec3((p[0] * scale, p[1] * scale,
                                             p[2] * scale)))

    def keyQuat(t, joint, q):
        if addMode and joint < len(bindPose):
            q = quatMul(bindPose[joint][1], q)
        return NoeKeyFramedValue(t, NoeQuat(q).transpose())

    trans = {}
    rots = {}
    scales = {}
    for seg in clip.segments:
        posKeys, rotKeys = decodeSegmentFrames(reader, clip, seg)
        for joint, keys in posKeys.items():
            b = toBone(joint)
            if b < 0:
                continue
            trans.setdefault(b, []).extend(
                keyVec(f * dt, joint, p) for f, p in keys)
        for joint, keys in rotKeys.items():
            b = toBone(joint)
            if b < 0:
                continue
            rots.setdefault(b, []).extend(
                keyQuat(f * dt, joint, q) for f, q in keys)
        for joint, vec in seg.constPositions:
            b = toBone(joint)
            if b < 0 or b in trans:
                continue
            trans[b] = [keyVec(0.0, joint, vec)]
        for joint, q in seg.constRots:
            b = toBone(joint)
            if b < 0 or b in rots:
                continue
            rots[b] = [keyQuat(0.0, joint, q)]
        for joint, value in seg.constScales:
            if value == (1.0, 1.0, 1.0):
                continue
            bone = toBone(joint)
            if bone not in scales:
                scales[bone] = [NoeKeyFramedValue(0.0, NoeVec3(value))]

    out = []
    for b in sorted(set(trans) | set(rots) | set(scales)):
        kf = NoeKeyFramedBone(b)
        if b in rots:
            kf.setRotation(rots[b])
        if b in trans:
            kf.setTranslation(trans[b])
        if b in scales:
            kf.setScale(scales[b], noesis.NOEKF_SCALE_VECTOR_3)
        out.append(kf)
    if out and clip.numFrames > 1:
        endTime = (clip.numFrames - 1) * dt
        keyLists = [keys for bone in out for keys in
                    (bone.rotationKeys, bone.translationKeys, bone.scaleKeys) if keys]
        if keyLists and max(keys[-1].time for keys in keyLists) < endTime:
            keyLists[0].append(NoeKeyFramedValue(endTime, keyLists[0][-1].value))
    if out and deformationRig is not None:
        tracks = {}
        for bone in out:
            tracks[bone.boneIndex] = {
                'translation': [(key.time, tuple(key.value[axis] / scale for axis in range(3)))
                                for key in bone.translationKeys],
                'rotation': [(key.time, (-key.value[0], -key.value[1], -key.value[2], key.value[3]))
                             for key in bone.rotationKeys],
                'scale': [(key.time, tuple(key.value[axis] for axis in range(3)))
                          for key in bone.scaleKeys]}
        for boneIndex, samples in sorted(deformationRig.bake(clip.numFrames, clip.frameRate, tracks).items()):
            bone = NoeKeyFramedBone(boneIndex)
            bone.setTranslation([NoeKeyFramedValue(time, NoeVec3(tuple(value * scale for value in position)))
                                 for time, position, rotation, size in samples])
            bone.setRotation([NoeKeyFramedValue(time, NoeQuat(rotation).transpose())
                              for time, position, rotation, size in samples])
            bone.setScale([NoeKeyFramedValue(time, NoeVec3(size))
                           for time, position, rotation, size in samples], noesis.NOEKF_SCALE_VECTOR_3)
            out.append(bone)
    return out


def buildNoesisAnims(clips, boneList, boneMap=None, scale=1.0,
                     bindPose=None, targetSkelId=None, clipName=None,
                     allowAdditive=False, deformationRig=None):
    """Build NoeKeyFramedAnim objects for the clips whose channels map onto
    real bones. Locator-only clips cannot animate a skeleton, so they are
    skipped rather than emitted as empty animations.

    boneMap: list translating a clip's global joint index (xform record
    order of the target skeleton's JOINT_HIERARCHY) to a bone-list index;
    fmt_nd_pak's PakFile.boneMap is exactly that.
    scale: fmt_nd_pak's GlobalScale - bone matrices carry filePos * scale,
    so keyframe translations must too.
    bindPose: list of ((tx,ty,tz),(qx,qy,qz,qw)) in anim-joint order (from
    bindPoseFromJointHierarchy); used to compose additive clips."""
    if not boneList:
        return []
    boneIndexByName = {}
    for i, bone in enumerate(boneList):
        boneIndexByName[bone.name] = i

    anims = []
    mismatched = 0
    skipped = []
    seen = set()
    for clip in clips:
        if targetSkelId is not None and clip.skelId != targetSkelId:
            mismatched += 1
            continue
        if clipName and clip.name.lower() != clipName.lower():
            continue
        identity = (clip.skelId, clip.nameHash)
        if identity in seen:
            continue
        seen.add(identity)
        if not allowAdditive and clipIsAdditive(clip):
            skipped.append((clip.name, 'additive preview is disabled; use -animadditive'))
            continue
        kfBones = []
        # Per-joint decoded motion takes priority: positions and rotations come
        # straight out of the clip's own bit stream, mapped to bones through
        # the per-segment KeyCache tables and the skeleton's boneMap.
        try:
            decoded = _keyFramedBonesFromDecode(clip, boneList, boneMap, scale,
                                               bindPose, deformationRig)
        except Exception as error:
            skipped.append((clip.name, str(error)))
            continue
        if decoded:
            anims.append(NoeKeyFramedAnim(clip.name, boneList, decoded,
                                          clip.frameRate))
            continue
        for channel in clip.channels:
            if not channel.name or channel.name not in boneIndexByName:
                continue
            if not channel.rotations and not channel.translations:
                continue
            kfBone = NoeKeyFramedBone(boneIndexByName[channel.name])
            dt = 1.0 / clip.frameRate if clip.frameRate > 0 else 1.0 / 30.0
            if channel.rotations:
                kfBone.setRotation([NoeKeyFramedValue(f * dt, q.transpose())
                                    for f, q in enumerate(channel.rotations)])
            if channel.translations:
                kfBone.setTranslation([
                    NoeKeyFramedValue(f * dt, NoeVec3((t[0] * scale,
                                                       t[1] * scale,
                                                       t[2] * scale)))
                    for f, t in enumerate(channel.translations)])
            kfBones.append(kfBone)
        if kfBones:
            anims.append(NoeKeyFramedAnim(clip.name, boneList, kfBones,
                                          clip.frameRate))
    if mismatched:
        print('[NDAnim] excluded %d clip(s) for a different skeleton; target=%08X' %
              (mismatched, targetSkelId))
    for name, reason in skipped[:12]:
        print('[NDAnim] skipped %s: %s' % (name, reason))
    if len(skipped) > 12:
        print('[NDAnim] ... %d additional rejected clip(s)' % (len(skipped) - 12))
    return anims
