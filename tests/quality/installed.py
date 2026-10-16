"""Run the real EPUB gates against an installed wheel, outside the checkout.

Only the independent tests are loaded from this repository. All production
imports and CLI subprocesses must resolve from the supplied virtualenv.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


_CHECK = r'''
import importlib.util
import json
import subprocess
import sys
import types
import unittest
from pathlib import Path

tests_root, report = map(Path, sys.argv[1:])
namespace = types.ModuleType("tests")
namespace.__path__ = [str(tests_root)]
sys.modules["tests"] = namespace
suite = unittest.defaultTestLoader.loadTestsFromNames([
    "tests.quality.test_canonical_epub",
    "tests.quality.test_epub_entries",
    "tests.quality.test_publication",
    "tests.quality.test_jobs",
    "tests.quality.test_catalog_migration",
    "tests.quality.test_full_book",
    "tests.quality.test_html_conversion",
    "tests.quality.test_docx_conversion",
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
prefix = Path(sys.prefix)
origins = {}
for name, module in tuple(sys.modules.items()):
    if name.split(".")[0] in {"any_to_md", "web"} and getattr(module, "__file__", None):
        origin = Path(module.__file__).resolve()
        if not origin.is_relative_to(prefix):
            raise RuntimeError(f"Production import escaped the installed environment: {name}")
        origins[name] = str(origin)
for name in ("epub_to_md", "epub_to_html", "html_to_pdf", "html_to_md", "docx_to_md"):
    if importlib.util.find_spec(name) is not None:
        raise RuntimeError(f"Replaced converter still installed: {name}")
import any_to_md
font_dir = Path(any_to_md.__file__).parent / "resources" / "fonts"
fonts = [font_dir / name for name in ("NotoSansCJK.ttc", "NotoEmoji-Regular.ttf")]
if not all(font.is_file() for font in fonts):
    raise RuntimeError("Packaged fonts missing")
command = prefix / "bin" / "any-to-md-book"
help_result = subprocess.run([str(command), "--help"], capture_output=True, text=True, timeout=30)
if help_result.returncode != 0 or "--input-dir" not in help_result.stdout or "--target" not in help_result.stdout:
    raise RuntimeError("Installed console entry is unavailable")
report.write_text(json.dumps({"python": sys.executable, "prefix": str(prefix), "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped), "production_modules": origins, "packaged_fonts": [str(font) for font in fonts], "console_entry": str(command)}, indent=2) + "\n")
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--pdf", action="store_true")
    args = parser.parse_args()
    report = args.report.absolute()
    report.parent.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "ANY_TO_MD_TEST_PDF"}}
    if args.pdf:
        environment["ANY_TO_MD_TEST_PDF"] = "1"
    # Do not resolve the virtualenv's python symlink into its base interpreter.
    command = [str(args.python.absolute()), "-I", "-c", _CHECK, str(Path(__file__).resolve().parents[1]), str(report)]
    with tempfile.TemporaryDirectory() as directory:
        return subprocess.run(command, cwd=directory, env=environment, timeout=180).returncode


if __name__ == "__main__":
    raise SystemExit(main())
