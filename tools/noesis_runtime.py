"""Pinned local Noesis distribution and managed plugin deployment."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_POLICY = "noesis-distribution-v1"
ROOT_FILES = (
    "Noesis.exe", "Noesis64.exe", "noesis.dll", "noegl_opengl.dll",
    "noesiskinect.dll", "ReadMe.txt", "pluginsource.zip",
)
DISTRIBUTION_DIRECTORIES = ("noex64", "plugins", "optionalplugins", "scenes")
MANAGED_FILES = (
    "fmt_nd_pak.py", "inc_nd_anim.py", "inc_nd_skeleton.py",
    "inc_nd_modelparts.py", "inc_nd_config.py", "NDPartPresets.json",
)
LOCAL_PLUGIN_FILES = ("NDConfig.json", "NDTextureHashes.json", "NDRawDataStarts.json")
CRITICAL_FILES = (
    "Noesis64.exe", "ReadMe.txt", "noex64/noesis.dll", "noex64/noegl_opengl.dll",
    "plugins/x64/NoesisPython.dll", "plugins/x64/autodesk_fbx.dll",
    "plugins/x64/noesis_misc.dll", "plugins/python/core321.zip",
    "plugins/python/inc_noesis.py", "plugins/python/noewin.py",
)


class RuntimeSetupError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_identifier():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]


def file_record(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def reject_link(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeSetupError("symlinks are not allowed here: " + str(path))
    if path.exists():
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise RuntimeSetupError("reparse points are not allowed here: " + str(path))


def checked_child(root, relative):
    root = Path(root).absolute()
    reject_link(root)
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        raise RuntimeSetupError("unsafe workspace-relative path: " + str(relative))
    current = root
    for part in parts:
        current = current / part
        reject_link(current)
    resolved = current.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        raise RuntimeSetupError("path escapes its workspace: " + str(current))
    return resolved


def runtime_directory(root):
    return checked_child(root, "runtime/noesis")


def distribution_inventory(directory):
    directory = Path(directory).absolute()
    reject_link(directory)
    if not directory.is_dir():
        raise RuntimeSetupError("Noesis directory does not exist: " + str(directory))
    inventory = {}
    for relative in ROOT_FILES:
        path = checked_child(directory, relative)
        if not path.is_file():
            raise RuntimeSetupError("required runtime file is missing: " + relative)
        inventory[relative] = file_record(path)
    excluded = {"plugins/python/" + name for name in MANAGED_FILES + LOCAL_PLUGIN_FILES}
    for relative in DISTRIBUTION_DIRECTORIES:
        base = checked_child(directory, relative)
        if not base.is_dir():
            raise RuntimeSetupError("required runtime directory is missing: " + relative)
        for current, directories, filenames in os.walk(str(base), followlinks=False):
            for name in directories:
                reject_link(Path(current) / name)
            directories[:] = sorted(name for name in directories if name != "__pycache__")
            for name in sorted(filenames):
                path = Path(current) / name
                reject_link(path)
                key = path.relative_to(directory.resolve()).as_posix()
                if key in excluded or path.suffix.lower() in (".pyc", ".pyo"):
                    continue
                if not path.is_file():
                    raise RuntimeSetupError("unexpected non-file in runtime: " + str(path))
                inventory[key] = file_record(path)
    return inventory


def inventory_summary(inventory):
    rows = [[name, values["size"], values["sha256"]]
            for name, values in sorted(inventory.items())]
    canonical = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "files": len(rows),
        "bytes": sum(values["size"] for values in inventory.values()),
    }


def load_lock(root):
    path = checked_child(root, "runtime.lock.json")
    with path.open("r", encoding="utf-8") as source:
        lock = json.load(source)
    if not isinstance(lock, dict) or lock.get("schema") != 1:
        raise RuntimeSetupError("unsupported runtime.lock.json schema")
    if lock.get("inventory_policy") != INVENTORY_POLICY or lock.get("entrypoint") != "Noesis64.exe":
        raise RuntimeSetupError("unsupported runtime inventory policy or entrypoint")
    if not isinstance(lock.get("version"), str):
        raise RuntimeSetupError("runtime version must be a string")
    summary = lock.get("distribution", {})
    if not isinstance(summary, dict) or not isinstance(summary.get("sha256"), str) \
            or not re.fullmatch("[0-9a-f]{64}", summary["sha256"]):
        raise RuntimeSetupError("runtime distribution fingerprint is missing or invalid")
    critical = lock.get("critical_files", {})
    if not isinstance(critical, dict) or set(critical) != set(CRITICAL_FILES):
        raise RuntimeSetupError("runtime lock must contain every critical file")
    for values in critical.values():
        if not isinstance(values, dict) or not isinstance(values.get("sha256"), str) \
                or not re.fullmatch("[0-9a-f]{64}", values["sha256"]):
            raise RuntimeSetupError("invalid critical runtime file fingerprint")
    return lock


def verify_distribution(directory, lock):
    inventory = distribution_inventory(directory)
    for relative in CRITICAL_FILES:
        if inventory.get(relative) != lock["critical_files"][relative]:
            raise RuntimeSetupError("pinned runtime file differs: " + relative)
    if inventory_summary(inventory) != lock["distribution"]:
        raise RuntimeSetupError("runtime inventory differs from the lock; no files were adopted")
    first_line = (Path(directory) / "ReadMe.txt").read_text(encoding="utf-8", errors="replace").splitlines()[0]
    if first_line != "This is Noesis version " + lock["version"] + ".":
        raise RuntimeSetupError("Noesis version does not match its pinned ReadMe")
    return inventory


def verify_runtime(root, lock=None):
    lock = load_lock(root) if lock is None else lock
    directory = runtime_directory(root)
    inventory = verify_distribution(directory, lock)
    allowed = set(ROOT_FILES + DISTRIBUTION_DIRECTORIES)
    unexpected = sorted(path.name for path in directory.iterdir() if path.name not in allowed)
    if unexpected:
        raise RuntimeSetupError("unexpected files at runtime root: " + ", ".join(unexpected))
    return inventory


def write_json_atomic(path, value):
    path = Path(path)
    reject_link(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as destination:
            json.dump(value, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def remove_staging(staging, parent):
    parent = Path(parent).resolve()
    staging = Path(staging).absolute()
    if staging.parent.resolve() != parent or not staging.name.startswith(".setup-"):
        raise RuntimeSetupError("refusing to remove an unexpected staging directory")
    staging = checked_child(parent, staging.name)
    if staging.exists():
        shutil.rmtree(str(staging))


def install_runtime(root, source, lock):
    destination = runtime_directory(root)
    inventory = verify_distribution(source, lock)
    if destination.exists():
        verify_runtime(root, lock)
        return False
    parent = checked_child(root, "runtime")
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".setup-", dir=str(parent)))
    try:
        for directory in DISTRIBUTION_DIRECTORIES:
            checked_child(staging, directory).mkdir(parents=True, exist_ok=True)
        for relative in inventory:
            target = checked_child(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(checked_child(source, relative)), str(target))
        verify_distribution(staging, lock)
        staging = checked_child(parent, staging.name)
        destination = checked_child(parent, "noesis")
        if destination.exists():
            raise RuntimeSetupError("runtime appeared during setup; refusing to overwrite it")
        staging.rename(destination)
    finally:
        remove_staging(staging, parent)
    return True


def managed_payloads(root):
    payloads = {}
    for name in MANAGED_FILES:
        source = checked_child(root, name)
        if not source.is_file():
            raise RuntimeSetupError("project plugin file is missing: " + name)
        payloads["plugins/python/" + name] = source.read_bytes()
    config = checked_child(root, "NDConfig.json")
    if config.is_file():
        payloads["plugins/python/NDConfig.json"] = config.read_bytes()
    return payloads


def payload_records(payloads):
    return {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in payloads.items()}


def read_state(root):
    path = checked_child(root, "runtime/state.json")
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as source:
        state = json.load(source)
    if not isinstance(state, dict) or state.get("schema") != 1 or not isinstance(state.get("managed_files"), dict):
        raise RuntimeSetupError("invalid local runtime/state.json")
    return state


def check_deployment(root):
    directory = runtime_directory(root)
    expected = payload_records(managed_payloads(root))
    for relative, values in expected.items():
        path = checked_child(directory, relative)
        if not path.is_file() or file_record(path) != values:
            raise RuntimeSetupError("plugin deployment is missing or stale: " + relative)
    return expected


def clear_managed_bytecode(root):
    directory = runtime_directory(root)
    python = checked_child(directory, "plugins/python")
    cache = checked_child(directory, "plugins/python/__pycache__")
    stems = tuple(Path(name).stem for name in MANAGED_FILES if name.endswith(".py"))
    removed = []
    for folder in (python, cache):
        if not folder.is_dir():
            continue
        for candidate in folder.iterdir():
            name = candidate.name.lower()
            if candidate.suffix.lower() not in (".pyc", ".pyo"):
                continue
            if not any(name == stem + candidate.suffix.lower() or name.startswith(stem + ".")
                       for stem in stems):
                continue
            path = checked_child(directory, candidate.relative_to(directory))
            if not path.is_file():
                raise RuntimeSetupError("managed bytecode cache is not a regular file: " + str(path))
            path.unlink()
            removed.append(path.relative_to(directory).as_posix())
    return removed


def deploy_plugins(root, lock, force=False):
    directory = runtime_directory(root)
    payloads = managed_payloads(root)
    expected = payload_records(payloads)
    previous = read_state(root).get("managed_files", {})
    changes = []
    conflicts = []
    for relative, values in expected.items():
        target = checked_child(directory, relative)
        if target.exists() and not target.is_file():
            raise RuntimeSetupError("managed plugin destination is not a file: " + relative)
        actual = file_record(target) if target.exists() else None
        if actual == values:
            continue
        changes.append(relative)
        if actual is not None and actual != previous.get(relative):
            conflicts.append(relative)
    if conflicts and not force:
        raise RuntimeSetupError("local plugin edits would be overwritten; copy them back to the project or "
                                "use --force to back them up: " + ", ".join(conflicts))
    backup = None
    if conflicts:
        backup = checked_child(root, "runtime/backups/" + run_identifier())
        for relative in conflicts:
            target = checked_child(backup, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(checked_child(directory, relative)), str(target))
    for relative in changes:
        target = checked_child(directory, relative)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as destination:
                destination.write(payloads[relative])
            os.replace(str(temporary), str(target))
        finally:
            if temporary.exists():
                temporary.unlink()
    state = {
        "schema": 1, "version": lock["version"], "distribution": lock["distribution"],
        "deployed_at": utc_now(), "managed_files": expected,
    }
    write_json_atomic(checked_child(root, "runtime/state.json"), state)
    return {"changed": changes, "backup": str(backup) if backup else None, "managed_files": expected}


def prepare_runtime(root=REPOSITORY_ROOT, source=None, force=False, check_only=False):
    reject_link(root)
    root = Path(root).resolve()
    lock = load_lock(root)
    installed = False
    if source is not None:
        if check_only:
            raise RuntimeSetupError("--source and --check cannot be used together")
        installed = install_runtime(root, Path(source).expanduser().absolute(), lock)
    elif not runtime_directory(root).exists():
        raise RuntimeSetupError("runtime is not installed; run setup_runtime.py --source <local Noesis folder>")
    verify_runtime(root, lock)
    if check_only:
        deployed = {"changed": [], "backup": None, "removed_bytecode": [],
                    "managed_files": check_deployment(root)}
    else:
        deployed = deploy_plugins(root, lock, force=force)
        deployed["removed_bytecode"] = clear_managed_bytecode(root)
        check_deployment(root)
    return {
        "version": lock["version"], "runtime": str(runtime_directory(root)), "installed": installed,
        "distribution": lock["distribution"], "lock": file_record(checked_child(root, "runtime.lock.json")),
        "deployment": deployed,
    }
