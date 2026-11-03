"""Install or verify the pinned local Noesis runtime without downloading it."""

import argparse
import json
import sys

from noesis_runtime import REPOSITORY_ROOT, RuntimeSetupError, prepare_runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--source", help="existing complete Noesis folder matching runtime.lock.json")
    actions.add_argument("--check", action="store_true", help="verify runtime and deployed plugins without writing")
    parser.add_argument("--force", action="store_true", help="back up conflicting local plugin edits before deploying")
    parser.add_argument("--json", action="store_true", help="print machine-readable verification results")
    arguments = parser.parse_args(argv)
    if arguments.check and arguments.force:
        parser.error("--force cannot be combined with --check")
    try:
        result = prepare_runtime(REPOSITORY_ROOT, arguments.source, arguments.force, arguments.check)
    except (RuntimeSetupError, OSError, ValueError) as error:
        print("ERROR:", error, file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Noesis %s: %s" % (result["version"], result["runtime"]))
        print("Verified distribution: %d files, %d bytes" %
              (result["distribution"]["files"], result["distribution"]["bytes"]))
        print("Verified managed files:", len(result["deployment"]["managed_files"]))
        print("Deployed changed files:", len(result["deployment"]["changed"]))
        print("Cleared managed bytecode caches:", len(result["deployment"]["removed_bytecode"]))
        if result["deployment"]["backup"]:
            print("Local edits backed up to:", result["deployment"]["backup"])
        print("PASS: runtime fingerprint and current plugin deployment match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
