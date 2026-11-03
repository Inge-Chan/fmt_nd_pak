import bisect
import math
import re
import struct


IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0)


def matrixMultiply(left, right):
    result = []
    for row in range(3):
        for column in range(4):
            value = sum(left[row * 4 + axis] * right[axis * 4 + column]
                        for axis in range(3))
            if column == 3:
                value += left[row * 4 + 3]
            result.append(value)
    return tuple(result)


def matrixInverse(matrix):
    rows = [list(matrix[index:index + 3]) for index in (0, 4, 8)]
    inverse = [[float(row == column) for column in range(3)] for row in range(3)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-10:
            raise ValueError('singular skeleton bind matrix')
        rows[column], rows[pivot] = rows[pivot], rows[column]
        inverse[column], inverse[pivot] = inverse[pivot], inverse[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        inverse[column] = [value / divisor for value in inverse[column]]
        for row in range(3):
            if row == column:
                continue
            factor = rows[row][column]
            rows[row] = [value - factor * source
                         for value, source in zip(rows[row], rows[column])]
            inverse[row] = [value - factor * source
                            for value, source in zip(inverse[row], inverse[column])]
    result = []
    position = matrixPosition(matrix)
    for row in inverse:
        result.extend(row)
        result.append(-dot(row, position))
    return tuple(result)


def dot(first, second):
    return sum(left * right for left, right in zip(first, second))


def normalize(vector):
    length = math.sqrt(dot(vector, vector))
    if length < 1e-12:
        raise ValueError('zero-length skeleton vector')
    return tuple(value / length for value in vector)


def matrixPosition(matrix):
    return tuple(matrix[index] for index in (3, 7, 11))


def quatMultiply(first, second):
    first_vector, second_vector = first[:3], second[:3]
    cross = (first[1] * second[2] - first[2] * second[1],
             first[2] * second[0] - first[0] * second[2],
             first[0] * second[1] - first[1] * second[0])
    return tuple(first[3] * second_vector[axis] + second[3] * first_vector[axis]
                 + cross[axis] for axis in range(3)) + (
                     first[3] * second[3] - dot(first_vector, second_vector),)


def quatInverse(quaternion):
    return tuple(-value for value in quaternion[:3]) + (quaternion[3],)


def matrixFromTRS(position, quaternion, scale=(1.0, 1.0, 1.0)):
    vector_x, vector_y, vector_z, scalar = normalize(quaternion)
    rotation = (
        1 - 2 * (vector_y * vector_y + vector_z * vector_z),
        2 * (vector_x * vector_y - vector_z * scalar),
        2 * (vector_x * vector_z + vector_y * scalar),
        2 * (vector_x * vector_y + vector_z * scalar),
        1 - 2 * (vector_x * vector_x + vector_z * vector_z),
        2 * (vector_y * vector_z - vector_x * scalar),
        2 * (vector_x * vector_z - vector_y * scalar),
        2 * (vector_y * vector_z + vector_x * scalar),
        1 - 2 * (vector_x * vector_x + vector_y * vector_y))
    return tuple(rotation[row * 3 + column] * scale[column]
                 if column < 3 else position[row]
                 for row in range(3) for column in range(4))


def matrixToTRS(matrix):
    scale = tuple(math.sqrt(sum(matrix[row * 4 + column] ** 2 for row in range(3)))
                  for column in range(3))
    if min(scale) < 1e-10:
        raise ValueError('cannot decompose a zero-scale helper matrix')
    rotation = [matrix[row * 4 + column] / scale[column]
                for row in range(3) for column in range(3)]
    trace = rotation[0] + rotation[4] + rotation[8]
    if trace > 0:
        divisor = math.sqrt(trace + 1.0) * 2
        quaternion = ((rotation[7] - rotation[5]) / divisor,
                      (rotation[2] - rotation[6]) / divisor,
                      (rotation[3] - rotation[1]) / divisor, divisor / 4)
    else:
        axis = max(range(3), key=lambda index: rotation[index * 3 + index])
        next_axis, last_axis = (axis + 1) % 3, (axis + 2) % 3
        divisor = math.sqrt(max(0.0, 1.0 + rotation[axis * 3 + axis]
                               - rotation[next_axis * 3 + next_axis]
                               - rotation[last_axis * 3 + last_axis])) * 2
        quaternion = [0.0] * 4
        quaternion[axis] = divisor / 4
        quaternion[next_axis] = (rotation[axis * 3 + next_axis]
                                 + rotation[next_axis * 3 + axis]) / divisor
        quaternion[last_axis] = (rotation[axis * 3 + last_axis]
                                 + rotation[last_axis * 3 + axis]) / divisor
        quaternion[3] = (rotation[last_axis * 3 + next_axis]
                         - rotation[next_axis * 3 + last_axis]) / divisor
    return matrixPosition(matrix), normalize(quaternion), scale


def sampleKeys(keys, time, default, quaternion=False):
    if not keys:
        return default
    times, values = keys
    index = bisect.bisect_right(times, time + 1e-8) - 1
    if index < 0:
        return values[0]
    if index + 1 == len(times) or abs(time - times[index]) < 1e-8:
        return values[index]
    fraction = (time - times[index]) / (times[index + 1] - times[index])
    first, second = values[index], values[index + 1]
    if quaternion and dot(first, second) < 0:
        second = tuple(-value for value in second)
    result = tuple(left + (right - left) * fraction for left, right in zip(first, second))
    return normalize(result) if quaternion else result


class DeformationRig:
    def __init__(self, names, parents, bone_map, inverse_bind):
        if len(names) != len(parents) or len(names) != len(inverse_bind):
            raise ValueError('inconsistent skeleton table lengths')
        checked = set()
        for bone in range(len(names)):
            chain = set()
            current = bone
            while current >= 0 and current not in checked:
                if current >= len(names) or current in chain:
                    raise ValueError('invalid or cyclic source hierarchy')
                chain.add(current)
                current = parents[current]
                if current < -1:
                    raise ValueError('invalid source parent index')
            checked.update(chain)
        if len(set(bone_map)) != len(bone_map) or any(not 0 <= bone < len(names) for bone in bone_map):
            raise ValueError('invalid animation bone mapping')
        self.names = list(names)
        self.parents = list(parents)
        self.boneMap = list(bone_map)
        self.inverseBind = list(inverse_bind)
        self.worldBind = [matrixInverse(matrix) for matrix in inverse_bind]
        self.mainBones = set(bone_map)
        self.constraints = {}
        self.followers = {}
        main_names = {names[index]: index for index in bone_map}
        if len(main_names) != len(bone_map):
            raise ValueError('duplicate names in animation skeleton')
        self.originalParents = list(parents)
        for bone, name in enumerate(names):
            if bone in self.mainBones:
                continue
            if name.endswith('_helper') and name[:-7] in main_names:
                source = main_names[name[:-7]]
                self.parents[bone] = source
                self.followers[bone] = source
            elif parents[bone] < 0:
                self.parents[bone] = main_names.get('root', -1)
            if re.match(r'^m_twist_\d+$', name):
                candidates = [index for key, index in main_names.items()
                              if key in ('spinea', 'spineb', 'spinec', 'spined',
                                         'neck', 'heada', 'headb')]
                if candidates:
                    source = min(candidates, key=lambda index: self._distance(bone, index))
                    if self._distance(bone, source) < 0.002:
                        self.parents[bone] = source
                        self.followers[bone] = source
        for side in ('l', 'r'):
            source = main_names.get(side + '_elbow')
            tip = main_names.get(side + '_wrist')
            if source is None or tip is None:
                continue
            relative = matrixMultiply(inverse_bind[source], self.worldBind[tip])
            endpoint = matrixPosition(relative)
            length_squared = dot(endpoint, endpoint)
            if length_squared < 1e-8:
                continue
            for bone, name in enumerate(names):
                if bone in self.mainBones or not re.match(r'^' + side + r'_twist_\d+$', name):
                    continue
                ancestor = self.originalParents[bone]
                visited = set()
                while ancestor >= 0 and ancestor not in visited:
                    visited.add(ancestor)
                    if names[ancestor] == side + '_elbow_helper':
                        break
                    ancestor = self.originalParents[ancestor]
                if ancestor < 0 or names[ancestor] != side + '_elbow_helper':
                    continue
                local = matrixPosition(matrixMultiply(inverse_bind[source], self.worldBind[bone]))
                fraction = dot(local, endpoint) / length_squared
                residual = tuple(local[axis] - fraction * endpoint[axis] for axis in range(3))
                if not 0 <= fraction <= 1.02 or dot(residual, residual) > length_squared * 0.001:
                    continue
                self.parents[bone] = source
                self.constraints[bone] = {'kind': 'twist', 'source': source, 'tip': tip,
                                          'axis': normalize(endpoint), 'fraction': min(1.0, fraction),
                                          'bindRotation': matrixToTRS(relative)[1]}
        for side in ('l', 'r'):
            # Upper-arm twist bones hang off the shoulder helper, so they would
            # inherit the full shoulder roll. Keep the part near the clavicle
            # unrolled and let the roll build up towards the elbow.
            source = main_names.get(side + '_shoulder')
            anchor = main_names.get(side + '_clavicle')
            tip = main_names.get(side + '_elbow')
            if source is None or anchor is None or tip is None:
                continue
            endpoint = matrixPosition(matrixMultiply(inverse_bind[source], self.worldBind[tip]))
            length_squared = dot(endpoint, endpoint)
            if length_squared < 1e-8:
                continue
            relative = matrixMultiply(inverse_bind[source], self.worldBind[anchor])
            for bone, name in enumerate(names):
                if bone in self.mainBones or bone in self.constraints \
                        or not re.match(r'^' + side + r'_twist_0[1-4]$', name):
                    continue
                if names[self.originalParents[bone]] != side + '_shoulder_helper':
                    continue
                local = matrixPosition(matrixMultiply(inverse_bind[source], self.worldBind[bone]))
                fraction = dot(local, endpoint) / length_squared
                residual = tuple(local[axis] - fraction * endpoint[axis] for axis in range(3))
                if not -0.05 <= fraction <= 1.02 or dot(residual, residual) > length_squared * 0.001:
                    continue
                self.parents[bone] = source
                self.constraints[bone] = {'kind': 'twist', 'source': source, 'tip': anchor,
                                          'axis': normalize(endpoint),
                                          'fraction': 1.0 - min(1.0, max(0.0, fraction)),
                                          'bindRotation': matrixToTRS(relative)[1]}
        for bone, name in enumerate(names):
            match = re.match(r'^([lr])_backpack_strap_\d+_base$', name)
            if not match or bone in self.mainBones:
                continue
            anchors = [main_names[key] for key in ('spinea', 'spineb', 'spinec', 'spined',
                       match.group(1) + '_clavicle') if key in main_names]
            if not anchors:
                continue
            anchors.sort(key=lambda source: self._distance(bone, source))
            anchors = anchors[:3]
            weights = [1.0 / max(self._distance(bone, source), 0.01) ** 2 for source in anchors]
            total = sum(weights)
            self.constraints[bone] = {'kind': 'strap', 'sources': anchors,
                                      'weights': [weight / total for weight in weights]}
        self.localBind = [matrixMultiply(inverse_bind[parent], self.worldBind[bone])
                          if parent >= 0 else self.worldBind[bone]
                          for bone, parent in enumerate(self.parents)]
        self._buildOrder()

    def _buildOrder(self):
        names = self.names
        self.order = []
        visited, visiting = set(), set()

        def visit(bone):
            if bone in visited:
                return
            if not 0 <= bone < len(names) or bone in visiting:
                raise ValueError('invalid or cyclic deformation hierarchy')
            visiting.add(bone)
            parent = self.parents[bone]
            if parent >= 0:
                visit(parent)
            constraint = self.constraints.get(bone, {})
            for source in constraint.get('sources', []):
                visit(source)
            for key in ('source', 'tip'):
                if key in constraint:
                    visit(constraint[key])
            visiting.remove(bone)
            visited.add(bone)
            self.order.append(bone)

        for bone in self.constraints:
            visit(bone)
        self.bindTRS = {bone: matrixToTRS(self.localBind[bone])
                        for bone in self.order if bone in self.mainBones}

    def useSkinStrapAnchors(self, vertices, max_vertices=12, max_sources=6, max_distance=0.05):
        """Re-anchor each strap base to the bones that skin the body surface
        under it. vertices: [(position, [(bone, weight), ...]), ...] in bind
        space, same units as the skeleton. Vertices weighted to the backpack
        itself or to bones outside this skeleton are ignored. Returns the
        number of straps re-anchored."""
        straps = [bone for bone, constraint in self.constraints.items()
                  if constraint['kind'] == 'strap']
        if not straps:
            return 0
        excluded = re.compile(r'backpack|strap|mover|_grp$')
        allowed = [not excluded.search(name) for name in self.names]
        anchors = [matrixPosition(self.worldBind[bone]) for bone in straps]
        low = [min(anchor[axis] for anchor in anchors) - max_distance for axis in range(3)]
        high = [max(anchor[axis] for anchor in anchors) + max_distance for axis in range(3)]
        body = [(position, influences) for position, influences in vertices
                if influences and all(low[axis] <= position[axis] <= high[axis] for axis in range(3))
                and all(0 <= source < len(allowed) and allowed[source]
                        for source, weight in influences)]
        limit = max_distance * max_distance
        updated = 0
        for bone in straps:
            anchor = matrixPosition(self.worldBind[bone])
            nearby = []
            for position, influences in body:
                offset = tuple(position[axis] - anchor[axis] for axis in range(3))
                distance = dot(offset, offset)
                if distance <= limit:
                    nearby.append((distance, influences))
            if len(nearby) < max_vertices:
                continue
            nearby.sort(key=lambda item: item[0])
            totals = {}
            for distance, influences in nearby[:max_vertices]:
                for source, weight in influences:
                    totals[source] = totals.get(source, 0.0) + weight
            ranked = sorted(totals.items(), key=lambda item: -item[1])[:max_sources]
            total = sum(weight for source, weight in ranked)
            if total <= 0:
                continue
            self.constraints[bone] = {'kind': 'strap', 'sources': [source for source, weight in ranked],
                                      'weights': [weight / total for source, weight in ranked]}
            updated += 1
        if updated:
            self._buildOrder()
        return updated

    def _distance(self, first, second):
        difference = tuple(left - right for left, right in zip(
            matrixPosition(self.worldBind[first]), matrixPosition(self.worldBind[second])))
        return math.sqrt(dot(difference, difference))

    def bake(self, num_frames, frame_rate, tracks):
        prepared = {bone: {channel: ([key[0] for key in keys], [key[1] for key in keys])
                           for channel, keys in channels.items() if keys}
                    for bone, channels in tracks.items() if bone in self.bindTRS}
        output = {bone: [] for bone in self.constraints}
        previous_angles = {}
        for frame in range(num_frames):
            time = frame / frame_rate
            world = {}
            inverse_cache = {}
            correction_cache = {}

            def inverseWorld(bone):
                if bone not in inverse_cache:
                    inverse_cache[bone] = matrixInverse(world[bone])
                return inverse_cache[bone]

            for bone in self.order:
                local = self.localBind[bone]
                if bone in prepared:
                    position, rotation, scale = self.bindTRS[bone]
                    channels = prepared[bone]
                    position = sampleKeys(channels.get('translation'), time, position)
                    rotation = sampleKeys(channels.get('rotation'), time, rotation, True)
                    scale = sampleKeys(channels.get('scale'), time, scale)
                    local = matrixFromTRS(position, rotation, scale)
                parent = self.parents[bone]
                world[bone] = matrixMultiply(world[parent], local) if parent >= 0 else local
                constraint = self.constraints.get(bone)
                if not constraint:
                    continue
                if constraint['kind'] == 'twist':
                    source, tip = constraint['source'], constraint['tip']
                    relative = matrixMultiply(inverseWorld(source), world[tip])
                    delta = quatMultiply(matrixToTRS(relative)[1], quatInverse(constraint['bindRotation']))
                    angle = 2 * math.atan2(dot(delta[:3], constraint['axis']), delta[3])
                    previous = previous_angles.get(bone, 0.0)
                    angle += 2 * math.pi * round((previous - angle) / (2 * math.pi))
                    previous_angles[bone] = angle
                    half_angle = angle * constraint['fraction'] * 0.5
                    rotation = tuple(value * math.sin(half_angle) for value in constraint['axis']) + (math.cos(half_angle),)
                    correction = matrixMultiply(world[source], matrixFromTRS((0, 0, 0), rotation))
                    correction = matrixMultiply(correction, self.inverseBind[source])
                else:
                    for source in constraint['sources']:
                        if source not in correction_cache:
                            correction_cache[source] = matrixToTRS(
                                matrixMultiply(world[source], self.inverseBind[source]))
                    # Blend where each anchor carries the bind position, not the
                    # anchors' correction offsets: offsets taken about the origin
                    # drift far apart whenever the anchors rotate differently.
                    anchor = matrixPosition(self.worldBind[bone])
                    position = [0.0, 0.0, 0.0]
                    for source, weight in zip(constraint['sources'], constraint['weights']):
                        moved = matrixPosition(matrixMultiply(
                            matrixMultiply(world[source], self.inverseBind[source]),
                            matrixFromTRS(anchor, (0, 0, 0, 1))))
                        for axis in range(3):
                            position[axis] += moved[axis] * weight
                    components = [correction_cache[source] for source in constraint['sources']]
                    rotations = [component[1] for component in components]
                    reference = rotations[0]
                    rotations = [tuple(-value for value in rotation)
                                 if dot(rotation, reference) < 0 else rotation for rotation in rotations]
                    rotation = normalize(tuple(sum(quaternion[axis] * weight for quaternion, weight in
                                         zip(rotations, constraint['weights'])) for axis in range(4)))
                    correction = matrixFromTRS((0, 0, 0), rotation)
                    rotated = matrixPosition(matrixMultiply(correction, matrixFromTRS(anchor, (0, 0, 0, 1))))
                    correction = matrixFromTRS(tuple(position[axis] - rotated[axis] for axis in range(3)), rotation)
                world[bone] = matrixMultiply(correction, self.worldBind[bone])
                local = matrixMultiply(inverseWorld(parent), world[bone]) if parent >= 0 else world[bone]
                position, rotation, scale = matrixToTRS(local)
                if output[bone] and dot(rotation, output[bone][-1][2]) < 0:
                    rotation = tuple(-value for value in rotation)
                output[bone].append((time, position, rotation, scale))
        return output


def readDeformationRig(data, header, names, parents, bone_map):
    if header < 0 or header + 64 > len(data):
        raise ValueError('invalid skeleton header offset')
    if struct.unpack_from('<I', data, header)[0] != 0x41480105:
        return None
    count = struct.unpack_from('<H', data, header + 16)[0]
    offset = header + struct.unpack_from('<I', data, header + 40)[0]
    hierarchy = header + struct.unpack_from('<I', data, header + 60)[0]
    if count != len(names) or offset < header + 64 or offset + 48 * count > min(hierarchy, len(data)):
        raise ValueError('invalid native inverse-bind table')
    inverse_bind = [struct.unpack_from('<12f', data, offset + 48 * bone) for bone in range(count)]
    if any(not math.isfinite(value) for matrix in inverse_bind for value in matrix):
        raise ValueError('non-finite native inverse-bind matrix')
    return DeformationRig(names, parents, bone_map, inverse_bind)
