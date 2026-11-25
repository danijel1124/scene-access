"""Checks that the sceneaccess package imports only downwards, and fails if not.

The folders form layers, lowest first: storage, llm, media, settings, pipeline, ui. A module imports from its own
folder, from folders before it, and from the shared bricks in the package folder itself, which import from no
folder. cli.py, the command line, may import anything. So no two folders ever import from each other.

Usage: python tools\\check_layers.py; prints every import against the rule and exits with 1 if there is one."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "sceneaccess"
LAYERS = ["storage", "llm", "media", "settings", "pipeline", "ui"]
BRICKS = {"request_control", "worker_process", "timecodes", "providers", "prompts", "__init__"}
TOP = {"cli"}


def place(module: str, folders: dict[str, str]) -> str:
    """The layer of a dotted module below sceneaccess, e.g. "llm.client" or "timecodes"."""
    return folders.get(module, module.split(".")[0] if "." in module else ("top" if module in TOP else "bricks"))


def imports(path: Path) -> list[tuple[int, str]]:
    """(line, dotted module below sceneaccess) for each import of a sceneaccess module in path."""
    package = path.relative_to(PACKAGE).parent.parts
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = list(package[:len(package) - (node.level - 1)])
            parts = base + (node.module.split(".") if node.module else [])
        elif (node.module or "").split(".")[0] == "sceneaccess":
            parts = node.module.split(".")[1:]
        else:
            continue
        # "from . import answering" and "from .pipeline import answering" name modules, not attributes.
        names = [alias.name for alias in node.names]
        if (PACKAGE.joinpath(*parts) / "__init__.py").exists() or not parts:
            found += [(node.lineno, ".".join([*parts, name])) for name in names]
        else:
            found.append((node.lineno, ".".join(parts)))
    return found


def check(folders: dict[str, str] | None = None) -> list[str]:
    """Every import against the rule. folders places modules by name instead of by where their files are, to try
    a layout before moving anything, e.g. {"openrouter": "llm"}."""
    folders = folders or {}
    problems = []
    for path in sorted(PACKAGE.rglob("*.py")):
        module = ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
        source = place(module, folders)
        for line, target in imports(path):
            target_place = place(target, folders)
            if source == "top" or target_place == source:
                continue
            if source == "bricks":
                allowed = target_place == "bricks"
            elif target_place in ("bricks",):
                allowed = True
            else:
                allowed = (source in LAYERS and target_place in LAYERS
                           and LAYERS.index(target_place) < LAYERS.index(source))
            if not allowed:
                problems.append(f"{path.relative_to(PACKAGE.parent)}:{line}: {source} imports {target} ({target_place})")
    return problems


if __name__ == "__main__":
    found = check()
    print("\n".join(found) or "The imports all point downwards.")
    sys.exit(1 if found else 0)
