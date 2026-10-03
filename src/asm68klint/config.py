"""Settings from a configuration file.

The file is ``asm68klint.toml``, or the ``[tool.asm68klint]`` table of a
``pyproject.toml``, in the current directory or the nearest one above it:

    reserved = ["a5", "a6"]
    include-dirs = ["include", "build"]
    select = ["H", "R"]
    ignore = ["R002"]

Directories are relative to the file. What the command line gives comes first.
"""

import tomllib
from pathlib import Path

OWN_FILE = "asm68klint.toml"
PROJECT_FILE = "pyproject.toml"
KEYS = ("reserved", "include-dirs", "select", "ignore")
SWITCHES = ("infer",)


def read_config(path: Path) -> dict | None:
    """Read the settings in a file; None if a pyproject.toml has none for us.

    Raises ValueError for a setting that is unknown or not a list of strings.
    """
    with path.open("rb") as file:
        settings = tomllib.load(file)
    if path.name == PROJECT_FILE:
        settings = settings.get("tool", {}).get("asm68klint")
        if settings is None:
            return None
    for key, value in settings.items():
        if key in SWITCHES and isinstance(value, bool):
            continue
        if key in SWITCHES:
            raise ValueError(f"{path}: {key} must be true or false")
        if key not in KEYS:
            raise ValueError(f"{path}: unknown setting {key!r}")
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{path}: {key} must be a list of strings")
    if "include-dirs" in settings:
        settings["include-dirs"] = [
            str(path.parent / directory) for directory in settings["include-dirs"]
        ]
    return settings


def find_config(start: Path) -> dict:
    """Return the settings of the nearest configuration file, or none."""
    for directory in [start.resolve(), *start.resolve().parents]:
        for name in (OWN_FILE, PROJECT_FILE):
            if (directory / name).is_file():
                settings = read_config(directory / name)
                if settings is not None:
                    return settings
    return {}
