"""Minimal keyframe objects for testing without the proprietary Noesis host."""

import struct
import sys
import types

from .pak_reader import Pak


class NoeQuat:
    def __init__(self, values):
        self.v = list(values)

    def __getitem__(self, index):
        return self.v[index]

    def transpose(self):
        return NoeQuat((-self.v[0], -self.v[1], -self.v[2], self.v[3]))


class NoeVec3:
    def __init__(self, values):
        self.v = list(values)

    def __getitem__(self, index):
        return self.v[index]


class NoeKeyFramedValue:
    def __init__(self, time, value):
        self.time = time
        self.value = value


class NoeKeyFramedBone:
    def __init__(self, index):
        self.boneIndex = index
        self.rotationKeys = []
        self.translationKeys = []
        self.scaleKeys = []

    def setRotation(self, keys, *args, **kwargs):
        self.rotationKeys = keys

    def setTranslation(self, keys, *args, **kwargs):
        self.translationKeys = keys

    def setScale(self, keys, *args, **kwargs):
        self.scaleKeys = keys


class NoeKeyFramedAnim:
    def __init__(self, name, bones, keyframed_bones, frameRate=20.0, flags=0):
        self.name = name
        self.bones = bones
        self.kfBones = keyframed_bones
        self.frameRate = frameRate


class FakeBitStream:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def tell(self):
        return self.pos

    def seek(self, offset, origin=0):
        self.pos = offset if origin == 0 else self.pos + offset

    def getSize(self):
        return len(self.data)

    def readBytes(self, count):
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result


class FakePak:
    def __init__(self, path):
        package = Pak(path)
        self.bs = FakeBitStream(package.d)
        self.pakPageEntries = package.pages
        self.pointerPageIds = package.fixups
        self.boneList = None


class FakeBone:
    def __init__(self, name):
        self.name = name


noesis = types.ModuleType("noesis")
for name, value in (
        ("NOEKF_ROTATION_QUATERNION_4", 0), ("NOEKF_TRANSLATION_VECTOR_3", 1),
        ("NOEKF_SCALE_SCALAR_1", 2), ("NOEKF_SCALE_VECTOR_3", 3),
        ("NOEKF_INTERPOLATE_LINEAR", 0)):
    setattr(noesis, name, value)
noesis.validateListType = lambda *args, **kwargs: None
inc = types.ModuleType("inc_noesis")
for name, value in (
        ("NoeQuat", NoeQuat), ("NoeVec3", NoeVec3),
        ("NoeKeyFramedValue", NoeKeyFramedValue), ("NoeKeyFramedBone", NoeKeyFramedBone),
        ("NoeKeyFramedAnim", NoeKeyFramedAnim), ("noesis", noesis), ("struct", struct)):
    setattr(inc, name, value)
sys.modules["noesis"] = noesis
sys.modules["inc_noesis"] = inc
