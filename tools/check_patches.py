"""Checks that every patch a test makes on a sceneaccess module can take effect, and fails if not.

A test that runs monkeypatch.setattr(module, "name", …) changes what code in that module finds under the name. If
the module only imports the name and the code using it lives elsewhere, the patch silently does nothing; for names
such as config_path_from_standard_paths or announce that means a test reads the user's real chats or speaks
through the screen reader. So a name the module imports must be used by its own code; a name the module defines
reaches the module's own uses, and else must not be imported when loading by a module that uses it.
Code a test runs in a child process from a string, where it assigns module.name = …, is checked the same way.

Usage: python tools\\check_patches.py; prints every patch that cannot take effect and exits with 1 if there is one."""

from __future__ import annotations

import ast
import contextlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def modules_imported(tree: ast.AST) -> dict[str, str]:
    """Local name to dotted sceneaccess module, for "from sceneaccess.x import y" (y a module), "import a.b as c"."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sceneaccess"):
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                if module_file(dotted):
                    found[alias.asname or alias.name] = dotted
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sceneaccess") and alias.asname and module_file(alias.name):
                    found[alias.asname] = alias.name
    return found


def module_file(dotted: str) -> Path | None:
    path = REPO.joinpath(*dotted.split("."))
    return path.with_suffix(".py") if path.with_suffix(".py").is_file() else None


def reads(tree: ast.AST, name: str) -> bool:
    return any(isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
               for node in ast.walk(tree))


def defines(tree: ast.Module, name: str) -> bool:
    """Whether the module defines name itself, at its top level, rather than importing it."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return True
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                for target in node.targets):
            return True
    return False


def bound_early_elsewhere(dotted: str, name: str) -> list[str]:
    """Modules that import name from dotted when they load and then use it: a patch on dotted misses their
    uses. An import inside a function reads the module when it runs, so the patch reaches it."""
    found = []
    for path in sorted((REPO / "sceneaccess").rglob("*.py")):
        here = ".".join(path.relative_to(REPO).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and any(alias.name == name and not alias.asname
                                                        for alias in node.names):
                package = here.split(".")[:-1] if path.name != "__init__.py" else here.split(".")
                source = (".".join(package[:len(package) - (node.level - 1)] + ([node.module] if node.module else []))
                          if node.level else node.module)
                if source == dotted and reads(tree, name):
                    found.append(here)
    return found


def effect(dotted: str, name: str) -> str:
    """"" if a patch of name on dotted takes effect, else why not."""
    tree = ast.parse(module_file(dotted).read_text(encoding="utf-8"))
    if defines(tree, name):
        # It reaches the module's own uses; only a name the module never uses itself must not be bound early.
        early = [] if reads(tree, name) else bound_early_elsewhere(dotted, name)
        return f"{dotted} does not use it and {', '.join(early)} imported it when loading" if early else ""
    return "" if reads(tree, name) else f"{dotted} does not use it"


def patches(tree: ast.AST, modules: dict[str, str]) -> list[tuple[int, str, str]]:
    """(line, module, name) for monkeypatch.setattr(module, "name", …) and for module.name = … ."""
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in modules
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            found.append((node.lineno, modules[node.args[0].id], node.args[1].value))
        elif isinstance(node, ast.Assign):
            found.extend((node.lineno, modules[target.value.id], target.attr) for target in node.targets
                         if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                         and target.value.id in modules)
    return found


def check() -> list[str]:
    problems = []
    for path in sorted((REPO / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        trees = [(tree, 0)]
        # Code run in a child process from a string, such as test_single_instance.py's scripts.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sceneaccess" in node.value:
                with contextlib.suppress(SyntaxError):
                    trees.append((ast.parse(node.value), node.lineno - 1))
        for part, offset in trees:
            modules = modules_imported(part) or modules_imported(tree)
            for line, dotted, name in patches(part, modules):
                why = effect(dotted, name)
                if why:
                    problems.append(f"{path.relative_to(REPO)}:{line + offset}: {dotted}.{name} is patched, but {why}")
    return problems


if __name__ == "__main__":
    found = check()
    print("\n".join(found) or "Every patch on a sceneaccess module can take effect.")
    sys.exit(1 if found else 0)
