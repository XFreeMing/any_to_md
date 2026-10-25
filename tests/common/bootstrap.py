"""Bind this repository's test helpers without changing production import paths."""

from importlib.machinery import ModuleSpec
from pathlib import Path
import sys
from types import ModuleType


def bind_tests() -> None:
    """Prevent an unrelated installed ``tests`` package from shadowing this tree."""
    roots = [str(Path(__file__).resolve().parent.parent)]
    existing = sys.modules.get("tests")
    if existing is not None and list(getattr(existing, "__path__", ())) == roots:
        return
    for name in tuple(sys.modules):
        if name == "tests" or name.startswith("tests."):
            del sys.modules[name]
    namespace = ModuleType("tests")
    namespace.__package__ = "tests"
    namespace.__path__ = roots
    namespace.__spec__ = ModuleSpec("tests", loader=None, is_package=True)
    namespace.__spec__.submodule_search_locations = roots
    sys.modules["tests"] = namespace


bind_tests()
