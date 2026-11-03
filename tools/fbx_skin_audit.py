import argparse
import collections
import json
import struct
import zlib
from pathlib import Path


def read_fbx(path):
    data = Path(path).read_bytes()
    if not data.startswith(b'Kaydara FBX Binary  \x00\x1a\x00'):
        raise ValueError('expected a binary FBX')
    version = struct.unpack_from('<I', data, 23)[0]
    header_format = '<QQQB' if version >= 7500 else '<IIIB'
    header_size = struct.calcsize(header_format)
    scalars = {'Y': 'h', 'C': '?', 'F': 'f', 'D': 'd', 'I': 'i', 'L': 'q'}
    arrays = {'f': 'f', 'd': 'd', 'i': 'i', 'l': 'q', 'b': 'b', 'c': 'b'}

    def nodes(start, limit):
        cursor = start
        result = []
        while cursor + header_size <= limit:
            end, count, property_bytes, name_bytes = struct.unpack_from(
                header_format, data, cursor)
            if not end:
                break
            position = cursor + header_size
            name = data[position:position + name_bytes].decode('utf-8')
            position += name_bytes
            child_start = position + property_bytes
            if not cursor < child_start <= end <= len(data):
                raise ValueError('invalid FBX node bounds')
            values = []
            for unused in range(count):
                kind = chr(data[position])
                position += 1
                if kind in scalars:
                    value_format = '<' + scalars[kind]
                    values.append(struct.unpack_from(value_format, data, position)[0])
                    position += struct.calcsize(value_format)
                elif kind in ('S', 'R'):
                    size = struct.unpack_from('<I', data, position)[0]
                    position += 4
                    values.append(data[position:position + size])
                    position += size
                elif kind in arrays:
                    length, encoding, size = struct.unpack_from('<III', data, position)
                    position += 12
                    raw = data[position:position + size]
                    position += size
                    if encoding == 1:
                        raw = zlib.decompress(raw)
                    elif encoding != 0:
                        raise ValueError('unsupported FBX array encoding')
                    values.append(struct.unpack('<%d%s' % (length, arrays[kind]), raw))
                else:
                    raise ValueError('unsupported FBX property: ' + kind)
            if position != child_start:
                raise ValueError('invalid FBX property length')
            result.append((name, values, nodes(child_start, end)))
            cursor = end
        return result

    return version, {name: (values, children)
                     for name, values, children in nodes(27, len(data))}


def skin_signature(path):
    version, roots = read_fbx(path)
    objects = {values[0]: (kind, values, children)
               for kind, values, children in roots['Objects'][1] if values}
    connections = [values for name, values, children in roots['Connections'][1]]
    labels = {identifier: entry[1][1].split(b'\x00')[0].decode('utf-8')
              for identifier, entry in objects.items() if len(entry[1]) > 1}
    clusters = {identifier for identifier, entry in objects.items()
                if entry[0] == 'Deformer' and entry[1][2:] == [b'Cluster']}
    skins = {identifier for identifier, entry in objects.items()
             if entry[0] == 'Deformer' and entry[1][2:] == [b'Skin']}
    cluster_bones = {entry[2]: entry[1] for entry in connections
                     if entry[2] in clusters and entry[1] in objects
                     and objects[entry[1]][0] == 'Model'}
    cluster_skins = {entry[1]: entry[2] for entry in connections
                     if entry[1] in clusters and entry[2] in skins}
    skin_meshes = {entry[1]: entry[2] for entry in connections
                   if entry[1] in skins and entry[2] in objects
                   and objects[entry[2]][0] == 'Geometry'}
    geometry, weights = {}, {}
    for identifier, (kind, values, children) in objects.items():
        if kind not in ('Geometry', 'Deformer'):
            continue
        fields = {name: child_values for name, child_values, unused in children}
        if kind == 'Geometry':
            geometry[labels[identifier]] = (fields.get('Vertices'), fields.get('PolygonVertexIndex'))
        elif identifier in clusters and fields.get('Indexes') and fields['Indexes'][0]:
            mesh = skin_meshes[cluster_skins[identifier]]
            key = (labels[mesh], labels[cluster_bones[identifier]])
            if key in weights:
                raise ValueError('duplicate weighted cluster: ' + repr(key))
            weights[key] = (fields['Indexes'], fields['Weights'])
    return geometry, weights


def audit(path, filters=()):
    version, roots = read_fbx(path)
    objects = {values[0]: (kind, values, children)
               for kind, values, children in roots['Objects'][1] if values}
    connections = [values for name, values, children in roots['Connections'][1]]

    def label(identifier):
        return objects[identifier][1][1].split(b'\x00')[0].decode('utf-8')

    def subtype(identifier, value):
        return identifier in objects and objects[identifier][1][2:] == [value]

    parents = {entry[1]: entry[2] for entry in connections
               if entry[0] == b'OO' and entry[1] in objects and entry[2] in objects
               and objects[entry[1]][0] == objects[entry[2]][0] == 'Model'}
    cluster_bones = {entry[2]: entry[1] for entry in connections
                     if entry[1] in objects and objects[entry[1]][0] == 'Model'
                     and subtype(entry[2], b'Cluster')}
    cluster_skin = {entry[1]: entry[2] for entry in connections
                    if entry[1] in cluster_bones and subtype(entry[2], b'Skin')}
    skin_geometry = {entry[1]: entry[2] for entry in connections
                     if subtype(entry[1], b'Skin') and entry[2] in objects
                     and objects[entry[2]][0] == 'Geometry'}
    geometry_weights = collections.defaultdict(collections.Counter)
    for identifier, bone in cluster_bones.items():
        fields = {name: values for name, values, children in objects[identifier][2]}
        if 'Weights' in fields and identifier in cluster_skin:
            geometry = skin_geometry[cluster_skin[identifier]]
            geometry_weights[geometry][bone] += sum(fields['Weights'][0])
    output = []
    for geometry, weights in geometry_weights.items():
        name = label(geometry)
        if filters and not any(token in name for token in filters):
            continue
        influences = []
        for bone, weight in weights.most_common():
            if weight <= 0:
                continue
            chain = [label(bone)]
            current = bone
            visited = {current}
            while current in parents:
                current = parents[current]
                if current in visited:
                    raise ValueError('cyclic FBX hierarchy')
                visited.add(current)
                chain.append(label(current))
            influences.append({'bone': label(bone), 'weight_sum': weight, 'chain': chain})
        output.append({'mesh': name, 'influences': influences})
    return {'version': version, 'meshes': output}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--filter', action='append', default=[])
    parser.add_argument('--top', type=int, default=12)
    parser.add_argument('--json', action='store_true')
    arguments = parser.parse_args()
    report = audit(arguments.path, arguments.filter)
    if arguments.json:
        print(json.dumps(report, indent=2))
    else:
        for mesh in report['meshes']:
            print('\nMESH', mesh['mesh'])
            for influence in mesh['influences'][:arguments.top]:
                print('  %.2f  %s' % (influence['weight_sum'],
                                     ' <- '.join(influence['chain'])))
