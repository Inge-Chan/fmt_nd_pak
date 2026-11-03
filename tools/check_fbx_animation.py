import argparse
import json
import math
import struct
import zlib
from collections import Counter
from pathlib import Path


def inspect_fbx(path):
    data = Path(path).read_bytes()
    if not data.startswith(b'Kaydara FBX Binary  \x00\x1a\x00'):
        raise ValueError('expected a binary FBX file')
    version = struct.unpack_from('<I', data, 23)[0]
    if version < 7500:
        raise ValueError('this diagnostic expects FBX 7.5 or newer')

    def children(start, limit):
        offset = start
        while offset + 25 <= limit:
            end, count, size, name_size = struct.unpack_from('<QQQB', data, offset)
            if not end:
                break
            property_start = offset + 25 + name_size
            child_start = property_start + size
            if not offset < child_start <= end <= len(data):
                raise ValueError('invalid FBX node bounds')
            name = data[offset + 25:property_start].decode('utf-8')
            yield name, end, count, property_start, child_start
            offset = end

    def properties(entry):
        offset = entry[3]
        values = []
        formats = {'Y': ('<h', 2), 'C': ('<?', 1), 'I': ('<i', 4),
                   'F': ('<f', 4), 'D': ('<d', 8), 'L': ('<q', 8)}
        for unused in range(entry[2]):
            kind = chr(data[offset])
            offset += 1
            if kind in formats:
                fmt, size = formats[kind]
                values.append(struct.unpack_from(fmt, data, offset)[0])
                offset += size
            elif kind in ('S', 'R'):
                size = struct.unpack_from('<I', data, offset)[0]
                offset += 4
                values.append(data[offset:offset + size])
                offset += size
            elif kind in ('f', 'd', 'l', 'i', 'b', 'c'):
                count, encoding, size = struct.unpack_from('<III', data, offset)
                offset += 12
                values.append((kind, count, encoding, data[offset:offset + size]))
                offset += size
            else:
                raise ValueError('unknown FBX property type: ' + kind)
        return values

    roots = {entry[0]: entry for entry in children(27, len(data))}
    objects = roots['Objects']
    counts = Counter()
    names = []
    skeleton_models = set()
    weighted_clusters = set()
    curve_values = 0
    non_finite = 0
    for entry in children(objects[4], objects[1]):
        kind = entry[0]
        if kind in ('Geometry', 'Model', 'Deformer', 'AnimationStack'):
            values = properties(entry)
            subtype = values[2].decode('utf-8') if len(values) > 2 else ''
            counts[kind + ':' + subtype] += 1
            if kind == 'Model' and subtype in ('Root', 'Limb', 'LimbNode'):
                skeleton_models.add(values[0])
            if kind == 'AnimationStack':
                names.append(values[1].split(b'\x00')[0].decode('utf-8'))
            if kind == 'Deformer' and subtype == 'Cluster':
                for child in children(entry[4], entry[1]):
                    if child[0] == 'Indexes' and properties(child)[0][1] > 0:
                        weighted_clusters.add(values[0])
        if kind == 'AnimationCurve':
            counts[kind] += 1
            for child in children(entry[4], entry[1]):
                if child[0] == 'KeyValueFloat':
                    array_kind, count, encoding, payload = properties(child)[0]
                    raw = zlib.decompress(payload) if encoding == 1 else payload
                    values = struct.unpack('<%df' % count, raw)
                    curve_values += count
                    non_finite += sum(not math.isfinite(value) for value in values)
    connections = roots['Connections']
    weighted_bones = set()
    for entry in children(connections[4], connections[1]):
        values = properties(entry)
        if len(values) >= 3 and values[1] in skeleton_models and values[2] in weighted_clusters:
            weighted_bones.add(values[1])
    return {'version': version, 'objects': dict(counts), 'animations': names,
            'weighted_clusters': len(weighted_clusters),
            'weighted_skeleton_bones': len(weighted_bones),
            'curve_values': curve_values, 'non_finite_values': non_finite}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--animation')
    arguments = parser.parse_args()
    report = inspect_fbx(arguments.path)
    print(json.dumps(report, indent=2))
    if report['objects'].get('Geometry:Mesh', 0) < 1:
        raise SystemExit('FAIL: no mesh')
    if report['weighted_skeleton_bones'] < 2:
        raise SystemExit('FAIL: mesh is not bound to multiple skeleton bones')
    if not report['animations'] or not report['curve_values'] or report['non_finite_values']:
        raise SystemExit('FAIL: animation curves are missing or non-finite')
    if arguments.animation and arguments.animation not in report['animations']:
        raise SystemExit('FAIL: requested animation name was not preserved')
    print('PASS: skinned mesh and finite animation curves are present')
