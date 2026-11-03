"""Local configuration and path handling without a Noesis dependency."""

import json
import os
import re


GAME_IDS = ("U4", "TLL", "TLOUP1", "TLOU2")


def load_config(directory, environ=None):
    environ = os.environ if environ is None else environ
    path = os.path.join(directory, "NDConfig.json")
    config = {}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as source:
            config = json.load(source)
        if not isinstance(config, dict) or config.get("version") != 1:
            raise ValueError("NDConfig.json requires version 1")
        if set(config) - set(("version", "defaultGame", "baseDirectories")):
            raise ValueError("NDConfig.json contains unknown settings")
    default_game = environ.get("ND_DEFAULT_GAME", config.get("defaultGame", "TLOUP1"))
    if default_game not in GAME_IDS:
        raise ValueError("unknown default game: %s" % default_game)
    configured = config.get("baseDirectories", {})
    if not isinstance(configured, dict) or set(configured) - set(GAME_IDS):
        raise ValueError("baseDirectories must map supported game IDs to directories")
    directories = {}
    for game in GAME_IDS:
        value = environ.get("ND_" + game + "_ROOT", configured.get(game, ""))
        if not isinstance(value, str):
            raise ValueError("base directory for %s must be a string" % game)
        value = os.path.expanduser(os.path.expandvars(value.strip()))
        if value:
            if not os.path.isabs(value):
                value = os.path.join(directory, value)
            value = os.path.abspath(value).rstrip("\\/") + os.sep
        directories[game] = value
    return {"defaultGame": default_game, "baseDirectories": directories}


def detect_game(path, directories=None, fallback="TLOUP1"):
    if fallback not in GAME_IDS:
        raise ValueError("unknown fallback game: %s" % fallback)
    normalized = path.replace("\\", "/").lower()
    matches = []
    for game, directory in (directories or {}).items():
        root = directory.replace("\\", "/").rstrip("/").lower()
        if game in GAME_IDS and root and (normalized == root or normalized.startswith(root + "/")):
            matches.append((len(root), game))
    if matches:
        return max(matches)[1]
    if "thelostlegacy" in normalized or "the lost legacy" in normalized:
        return "TLL"
    if "uncharted4" in normalized or "uncharted 4" in normalized:
        return "U4"
    if re.search(r"part[ _-]*(?:ii|2)(?:[/ _.-]|$)", normalized) or "/ps4/main/" in normalized:
        return "TLOU2"
    if re.search(r"(?:^|[/ _-])(?:tlou2|t2)(?:[/_.-]|$)", normalized):
        return "TLOU2"
    if "last of us" in normalized or "tloup1" in normalized or "/pc/main/" in normalized:
        return "TLOUP1"
    return fallback


def find_root_dir(path):
    normalized = path.replace("\\", "/").lower()
    for marker in ("/uncharted4/", "/thelostlegacy/", "/ps4/main/", "/pc/main/"):
        position = normalized.find(marker)
        if position >= 0:
            return path[:position + len(marker)]
    return path


def resolve_asset_path(path, directories, source_directory):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("asset path must be a non-empty string")
    match = re.match(r"^\$\{([^}]+)\}[\\/](.*)$", path)
    if match:
        game, relative = match.groups()
        if game not in GAME_IDS:
            raise ValueError("unknown game root in asset path: %s" % game)
        root = directories.get(game)
        if not root:
            raise ValueError("configure ND_%s_ROOT or NDConfig.json before loading this part" % game)
        components = relative.replace("\\", "/").split("/")
        if not relative or relative.startswith(("/", "\\")) or ".." in components or ":" in relative:
            raise ValueError("game-root asset path must stay inside the configured root")
        return os.path.normpath(os.path.join(root, *components))
    if path.startswith("${"):
        raise ValueError("invalid game-root asset path: %s" % path)
    if not os.path.isabs(path):
        path = os.path.join(source_directory, path)
    return os.path.normpath(path)
