"""Synthetic runtime files for deployment tests; no vendor binaries are used."""

import json
import sys
from pathlib import Path

from . import ROOT


if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
import noesis_runtime as runtime


def make_runtime_fixture(directory):
    directory = Path(directory)
    project = directory / "project"
    source = directory / "source"
    project.mkdir()
    source.mkdir()
    for name in runtime.DISTRIBUTION_DIRECTORIES:
        (source / name).mkdir()
    for relative in set(runtime.ROOT_FILES + runtime.CRITICAL_FILES):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic distribution: " + relative).encode("utf-8"))
    (source / "ReadMe.txt").write_text("This is Noesis version 4.474.\n", encoding="utf-8")
    for name in runtime.MANAGED_FILES:
        (project / name).write_bytes(("synthetic project: " + name).encode("utf-8"))
    inventory = runtime.distribution_inventory(source)
    lock = {
        "schema": 1, "version": "4.474", "entrypoint": "Noesis64.exe",
        "inventory_policy": runtime.INVENTORY_POLICY,
        "distribution": runtime.inventory_summary(inventory),
        "critical_files": {name: inventory[name] for name in runtime.CRITICAL_FILES},
    }
    (project / "runtime.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return project, source
