from inc_noesis import *
import copy
import json


def readModelPartSpecs(value):
    if isinstance(value, str):
        if value.startswith('@'):
            with open(value[1:], 'r', encoding='utf-8') as source:
                manifest = json.load(source)
            if not isinstance(manifest, dict) or manifest.get('version') != 1:
                raise ValueError('unsupported model-part manifest version')
            items = manifest.get('parts')
        else:
            items = [path.strip() for path in value.split(';') if path.strip()]
    else:
        items = value
    if not isinstance(items, (list, tuple)) or len(items) > 64:
        raise ValueError('model parts must be a list of at most 64 entries')
    result = []
    allowed = set(('file', 'meshPrefixes', 'boneSuffix', 'attachTo'))
    for item in items:
        spec = {'file': item} if isinstance(item, str) else dict(item)
        if set(spec) - allowed:
            raise ValueError('unknown model-part fields: %s' % sorted(set(spec) - allowed))
        path = spec.get('file')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('model part requires a non-empty file path')
        prefixes = spec.get('meshPrefixes', [])
        if not isinstance(prefixes, (list, tuple)) or any(
                not isinstance(prefix, str) or not prefix for prefix in prefixes):
            raise ValueError('meshPrefixes must be a list of non-empty strings')
        for field in ('attachTo', 'boneSuffix'):
            if field in spec and (not isinstance(spec[field], str) or not spec[field]):
                raise ValueError('%s must be a non-empty string' % field)
        if spec.get('boneSuffix') and not spec.get('attachTo'):
            raise ValueError('boneSuffix requires an explicit attachTo bone')
        spec['file'] = path.strip()
        spec['meshPrefixes'] = tuple(prefixes)
        result.append(spec)
    return result


def meshMatches(name, prefixes):
    return not prefixes or any(name.startswith(prefix) for prefix in prefixes)


def _worldMatrices(bones):
    copies = [NoeBone(bone.index, bone.name, copy.deepcopy(bone.getMatrix()),
                      bone.parentName, bone.parentIndex) for bone in bones]
    return [bone.getMatrix() for bone in rapi.multiplyBones(copies)]


def attachPartBones(targetBones, partBones, boneSuffix, attachTo, label):
    if not targetBones or not partBones:
        raise ValueError('attached model part requires both skeletons')
    for bones in (targetBones, partBones):
        if any(bone.index != index for index, bone in enumerate(bones)):
            raise ValueError('model-part skeleton indices must be contiguous')
    targetIndices = dict((bone.name, bone.index) for bone in targetBones)
    if attachTo not in targetIndices:
        raise ValueError('attachment bone not found: ' + attachTo)
    anchor = targetIndices[attachTo]
    targetWorld = _worldMatrices(targetBones)
    partWorld = _worldMatrices(partBones)
    offset = len(targetBones)
    names = set(targetIndices)
    appended = []
    matched = 0
    for index, bone in enumerate(partBones):
        canonical = bone.name
        if boneSuffix and canonical.endswith(boneSuffix):
            canonical = canonical[:-len(boneSuffix)]
        if canonical != 'root' and canonical in targetIndices:
            parent = targetIndices[canonical]
            matrix = partWorld[index] * targetWorld[parent].inverse()
            matched += 1
        elif canonical == 'root' or bone.parentIndex < 0:
            parent = anchor
            matrix = partWorld[index] * targetWorld[parent].inverse()
        else:
            if bone.parentIndex >= len(partBones):
                raise ValueError('invalid model-part parent index')
            parent = offset + bone.parentIndex
            matrix = copy.deepcopy(bone.getMatrix())
        name = bone.name
        if name in names:
            name = label + '__' + name
        while name in names:
            name += '_part'
        names.add(name)
        appended.append(NoeBone(offset + index, name, matrix, None, parent))
    if not matched:
        raise ValueError('model-part skeleton has no matching driver bones')
    targetBones.extend(appended)
    return offset, matched
