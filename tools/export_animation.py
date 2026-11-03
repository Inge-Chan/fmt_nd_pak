"""Export with the pinned Noesis host; validate structure, not source timing or poses."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from noesis_runtime import (
    REPOSITORY_ROOT, RuntimeSetupError, file_record, prepare_runtime,
    reject_link, run_identifier, runtime_directory, utc_now, write_json_atomic,
)
from check_fbx_animation import inspect_fbx

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
import inc_nd_config


def existing_file(value, suffix):
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != suffix:
        raise ValueError("expected an existing %s file: %s" % (suffix, path))
    return path


def output_paths(root, value=None):
    output = (Path(value).expanduser().absolute() if value else
              Path(root) / "artifacts/noesis" / run_identifier() / "export.fbx")
    reject_link(output)
    if output.suffix.lower() != ".fbx":
        raise ValueError("output must have an .fbx extension")
    output = output.resolve()
    try:
        output.relative_to((Path(root) / "runtime").resolve())
    except ValueError:
        pass
    else:
        raise ValueError("export output cannot be placed inside the runtime")
    paths = {
        "fbx": output, "log": output.with_suffix(".noesis.log"),
        "console": output.with_suffix(".console.log"),
        "record": output.with_suffix(".noesis-run.json"),
    }
    for path in paths.values():
        reject_link(path)
        if path.exists():
            raise FileExistsError("refusing to reuse an existing output or sidecar: " + str(path))
    return paths


def declared_inputs(input_path, animation_path, parts_path, config):
    inputs = [("input", input_path)]
    if animation_path:
        inputs.append(("animation", animation_path))
    if parts_path:
        inputs.append(("manifest", parts_path))
        manifest = json.loads(parts_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("unsupported model-part manifest")
        parts = manifest.get("parts")
        if not isinstance(parts, list) or len(parts) > 64:
            raise ValueError("model-part manifest requires a list of at most 64 parts")
        for entry in parts:
            filename = entry if isinstance(entry, str) else entry.get("file") if isinstance(entry, dict) else None
            path = inc_nd_config.resolve_asset_path(filename, config["baseDirectories"], str(input_path.parent))
            inputs.append(("part", existing_file(path, ".pak")))
    result = []
    for role, path in inputs:
        result.append(dict(file_record(path), role=role, path=str(path)))
    return result


def build_command(runtime, input_path, paths, animation_path=None, parts_path=None,
                  clip=None, game=None, additive=False, legacy_helpers=False):
    command = [str(Path(runtime) / "Noesis64.exe"), "?cmode", str(input_path), str(paths["fbx"]),
               "-nodialog", "-anims", "-fbxmultitake", "-logfile", str(paths["log"])]
    if game:
        command.extend(("-ndgame", game))
    if animation_path:
        command.extend(("-animfile", str(animation_path)))
    if parts_path:
        command.extend(("-modelparts", "@" + str(parts_path)))
    if clip:
        command.extend(("-animclip", clip))
    if additive:
        command.append("-animadditive")
    if legacy_helpers:
        command.append("-ndlegacyhelpers")
    return command


def run_noesis(command, directory, environment, console, timeout):
    options = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    with Path(console).open("xb") as capture:
        process = subprocess.Popen(command, cwd=str(directory), env=environment, stdin=subprocess.DEVNULL,
                                   stdout=capture, stderr=subprocess.STDOUT, shell=False, **options)
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.kill()
            process.wait()
            raise


def check_export(paths, expected=None, clip=None):
    if not paths["fbx"].is_file() or not paths["fbx"].stat().st_size:
        raise ValueError("Noesis did not create a non-empty FBX")
    if not paths["log"].is_file():
        raise ValueError("Noesis did not create the requested log")
    raw_log = paths["log"].read_bytes()
    encoding = "utf-16" if raw_log.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    log = raw_log.decode(encoding, errors="replace")
    attached = re.findall(r"\[NDAnim\] attached (\d+) animation\(s\)", log)
    if not attached or int(attached[-1]) < 1:
        raise ValueError("log does not confirm that an animation was attached")
    count = int(attached[-1])
    report = inspect_fbx(paths["fbx"])
    if report["objects"].get("Geometry:Mesh", 0) < 1 or report["weighted_skeleton_bones"] < 2:
        raise ValueError("FBX does not contain a mesh with multi-bone skinning")
    if not report["curve_values"] or report["non_finite_values"]:
        raise ValueError("FBX animation curves are missing or non-finite")
    if count != len(report["animations"]) or expected is not None and count != expected:
        raise ValueError("attached/exported animation count does not match the requested count")
    if clip and not any(name.casefold() == clip.casefold() for name in report["animations"]):
        raise ValueError("the requested animation name was not preserved")
    return report


def export_animation(arguments, root=REPOSITORY_ROOT):
    input_path = existing_file(arguments.input, ".pak")
    animation_path = existing_file(arguments.animation_pak, ".pak") if arguments.animation_pak else None
    parts_path = existing_file(arguments.parts, ".json") if arguments.parts else None
    paths = output_paths(root, arguments.output)
    runtime = prepare_runtime(root)
    directory = runtime_directory(root)
    config = inc_nd_config.load_config(str(directory / "plugins/python"))
    inputs = declared_inputs(input_path, animation_path, parts_path, config)
    command = build_command(directory, input_path, paths, animation_path, parts_path, arguments.clip,
                            arguments.game, arguments.additive, arguments.legacy_helpers)
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment_names = ("ND_DEFAULT_GAME", "ND_U4_ROOT", "ND_TLL_ROOT", "ND_TLOUP1_ROOT", "ND_TLOU2_ROOT",
                         "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE")
    record = {
        "schema": 1, "started_at": utc_now(), "status": "running",
        "runtime": runtime, "command": command, "working_directory": str(directory),
        "driver": {
            "python": sys.version.split()[0], "platform": sys.platform,
            "files": {name: file_record(Path(__file__).parent / name)
                      for name in ("export_animation.py", "noesis_runtime.py", "check_fbx_animation.py")},
        },
        "environment": {name: environment[name] for name in environment_names if name in environment},
        "declared_inputs": inputs, "outputs": {name: str(path) for name, path in paths.items()},
        "expected_animations": arguments.expected_animations, "clip": arguments.clip,
        "timing_validation": "not_checked", "pose_validation": "not_checked",
    }
    configuration = directory / "plugins/python/NDConfig.json"
    if configuration.is_file():
        record["configuration"] = file_record(configuration)
    paths["record"].parent.mkdir(parents=True, exist_ok=True)
    with paths["record"].open("x", encoding="utf-8") as destination:
        json.dump(record, destination, ensure_ascii=False, indent=2)
    print("Noesis:", runtime["version"], flush=True)
    print("Output:", paths["fbx"], flush=True)
    print("Command:", subprocess.list2cmdline(command), flush=True)
    started = time.monotonic()
    try:
        return_code = run_noesis(command, directory, environment, paths["console"], arguments.timeout)
        record["return_code"] = return_code
        if return_code != 0:
            raise ValueError("Noesis exited with code %d" % return_code)
        record["structure"] = check_export(paths, arguments.expected_animations, arguments.clip)
        record["fbx"] = file_record(paths["fbx"])
        record["status"] = "structure_passed"
    except Exception as error:
        record["status"] = "failed"
        record["error"] = "%s: %s" % (type(error).__name__, error)
        raise
    except KeyboardInterrupt:
        record["status"] = "interrupted"
        raise
    finally:
        record["finished_at"] = utc_now()
        record["elapsed_seconds"] = time.monotonic() - started
        write_json_atomic(paths["record"], record)
        print("Run record:", paths["record"], flush=True)
    print("PASS: expected animation and skinned FBX structure; source timing and poses are NOT validated")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="main model or animation PAK")
    parser.add_argument("--animation-pak", help="optional separate animation PAK")
    parser.add_argument("--parts", help="model-part JSON manifest")
    parser.add_argument("--clip", help="exact animation name, case insensitive")
    parser.add_argument("--game", choices=inc_nd_config.GAME_IDS)
    parser.add_argument("--output", help="new FBX path; default is a unique artifacts/noesis run")
    parser.add_argument("--expected-animations", type=int, help="require this count; --clip defaults to 1")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--additive", action="store_true", help="experimental bind-pose additive preview")
    parser.add_argument("--legacy-helpers", action="store_true", help="old helper behavior for explicit A/B tests")
    arguments = parser.parse_args(argv)
    if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if arguments.expected_animations is not None and arguments.expected_animations < 1:
        parser.error("--expected-animations must be positive")
    if arguments.clip:
        if arguments.expected_animations not in (None, 1):
            parser.error("--clip requires exactly one animation")
        arguments.expected_animations = 1
    if os.name != "nt":
        parser.error("native Noesis export requires Windows")
    try:
        export_animation(arguments)
    except Exception as error:
        print("ERROR:", error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; the owned Noesis process was stopped", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
