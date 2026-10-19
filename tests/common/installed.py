"""Run acceptance scenarios against an installed wheel, outside the checkout.

Only scenario code and independent inspection tools come from this repository.
Production imports and CLI subprocesses must use the supplied virtualenv.
"""

from __future__ import annotations

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().with_name("bootstrap.py")))

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


_CHECK = r'''
import runpy
import sys
from pathlib import Path

tests_root, report, *identifiers = sys.argv[1:]
runpy.run_path(str(Path(tests_root) / "common" / "bootstrap.py"))
from tests.common.installed import check_installed
raise SystemExit(check_installed(identifiers or None, Path(report)))
'''


def _installation_details() -> dict:
    """Check distribution boundaries after scenario imports have occurred."""
    prefix = Path(sys.prefix).resolve()
    if prefix == Path(sys.base_prefix).resolve():
        raise RuntimeError("An independent virtualenv is required")
    import any_to_md

    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] in {"any_to_md", "web", "pdf_to_md", "ocr_to_md"} and getattr(module, "__file__", None):
            origin = Path(module.__file__).resolve()
            if not origin.is_relative_to(prefix):
                raise RuntimeError(f"Production import escaped the installed environment: {name}")
            origins[name] = str(origin)
    for name in ("epub_to_md", "epub_to_html", "html_to_pdf", "html_to_md", "docx_to_md", "url_to_md", "md_to_any", "group_verbs", "refine_groups"):
        if importlib.util.find_spec(name) is not None:
            raise RuntimeError(f"Replaced converter still installed: {name}")
    font_dir = Path(any_to_md.__file__).parent / "resources" / "fonts"
    fonts = [font_dir / name for name in ("NotoSansCJK.ttc", "NotoEmoji-Regular.ttf")]
    if not all(font.is_file() for font in fonts):
        raise RuntimeError("Packaged fonts missing")
    command = prefix / "bin" / "any-to-md-book"
    help_result = subprocess.run([str(command), "--help"], capture_output=True, text=True, timeout=30)
    if help_result.returncode != 0 or "--input-dir" not in help_result.stdout or "--target" not in help_result.stdout:
        raise RuntimeError("Installed console entry is unavailable")
    return {
        "status": "passed",
        "python": sys.executable,
        "prefix": str(prefix),
        "production_modules": origins,
        "packaged_fonts": [str(font) for font in fonts],
        "console_entry": str(command),
    }


def check_installed(identifiers: list[str] | None, report_path: Path) -> int:
    """Called only by the isolated interpreter; use the normal scenario runner."""
    from tests.common.run import execute, write_report

    try:
        report = execute(identifiers, artifact_parent=report_path.parent / "artifacts")
    except Exception as error:
        report = {"passed": False, "results": [], "error_type": type(error).__name__, "reason": str(error)}
    try:
        report["installation"] = _installation_details()
    except Exception as error:
        report["installation"] = {"status": "failed", "error_type": type(error).__name__, "reason": str(error)}
        report["passed"] = False
        print(f"Installation check failed: {error}", file=sys.stderr)
    write_report(report_path, report)
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", metavar="UC-01-S1", help="Run selected scenarios; omit to run all")
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum child runtime in seconds")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    report = args.report.absolute()
    report.parent.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "ANY_TO_MD_TEST_PDF"}}
    # Keep the virtualenv symlink: resolving it would select the base interpreter.
    command = [str(args.python.absolute()), "-I", "-c", _CHECK, str(Path(__file__).resolve().parents[1]), str(report), *args.scenarios]
    try:
        with tempfile.TemporaryDirectory(prefix="any-to-md-installed-") as directory:
            return subprocess.run(command, cwd=directory, env=environment, timeout=args.timeout).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        from tests.common.run import write_report

        write_report(report, {"passed": False, "results": [], "installation": {"status": "blocked", "error_type": type(error).__name__, "reason": str(error)}})
        print(f"Installed scenario run blocked: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
