"""Independent bind-pose extraction for the documented real sample cohorts."""

import struct

from .pak_reader import Pak


TYPE_JOINTS = 0x50CAF5257D6A140B


def read_skeleton(path):
    package = Pak(path)
    for start, offset, identifier, unused in package.items():
        if identifier != TYPE_JOINTS:
            continue
        payload = start + offset + 20 + package.padSz
        bone_count = package.u32(payload)
        header = package.ptr(payload + 12)
        names_table = package.ptr(payload + 36)
        transform_count = package.u16(header + 18)
        header_size = package.u32(header + 32)
        transforms = []
        for index in range(transform_count):
            record = header + header_size + 48 * index
            transforms.append({
                "scale": struct.unpack_from("<3f", package.d, record),
                "quat": struct.unpack_from("<4f", package.d, record + 16),
                "pos": struct.unpack_from("<3f", package.d, record + 32),
            })
        names = [package.cstr(start + package.u64(names_table + 16 * index + 8))
                 for index in range(bone_count)]
        hierarchy = header + package.u32(header + 60) + 20
        parent_table = hierarchy + package.u32(hierarchy) - 20
        parents = [struct.unpack_from("<4i", package.d, parent_table + 16 * index)
                   for index in range(bone_count)]
        return {
            "pak": package, "boneCount": bone_count, "xformCount": transform_count,
            "nodeCount": package.u16(header + 16), "names": names,
            "xforms": transforms, "parents": parents, "headerSize": header_size,
        }
    return None


def read_skel_names(path):
    skeleton = read_skeleton(path)
    return skeleton["names"] if skeleton else []


def read_skel_bonemap(path):
    skeleton = read_skeleton(path)
    if skeleton is None:
        return []
    parents = skeleton["parents"]
    result = []
    for bone in range(len(parents)):
        current = bone
        visited = set()
        while parents[current][1] != -1:
            if current in visited:
                raise ValueError("cyclic skeleton hierarchy")
            visited.add(current)
            current = parents[current][1]
            if not 0 <= current < len(parents):
                raise ValueError("invalid skeleton parent index")
        if current == 0:
            result.append(bone)
    return result
