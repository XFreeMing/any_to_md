"""Run complete acceptance scenarios; only explicit checks count as a pass."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tests.scenarios.support import Blocked, VisualReviewRequired


SCENARIO_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCENARIO_DIR.parents[1]
TODO = PROJECT_ROOT / "TODO.md"
SCENARIO_ID = re.compile(r"UC-[0-9]{2}-S[1-9][0-9]*")
TODO_SCENARIO = re.compile(r"^\s*Scenario(?: Outline)?:\s+(UC-[0-9]{2}-S[1-9][0-9]*)\b", re.MULTILINE)


def code_fingerprint() -> dict[str, str]:
    """Bind acceptance to current production, scenario, dependency and TODO inputs."""
    paths = {PROJECT_ROOT / name for name in ("AGENTS.md", "TODO.md", "pyproject.toml", "uv.lock")}
    for name in ("src", "web", "pdf_to_md", "ocr_to_md", "tests", "scripts/run"):
        paths.update(path for path in (PROJECT_ROOT / name).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix not in {".pyc", ".pyo"} and path.name != ".DS_Store")
    return {path.relative_to(PROJECT_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def inventory() -> tuple[dict[str, Path], set[str]]:
    """Include retained regression files and require every active TODO scenario."""
    files = {}
    for path in sorted(SCENARIO_DIR.glob("UC-*.py")):
        if SCENARIO_ID.fullmatch(path.stem) is None:
            raise ValueError(f"Invalid scenario filename: {path.name}")
        files[path.stem] = path
    active_ids = TODO_SCENARIO.findall(TODO.read_text(encoding="utf-8"))
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("TODO.md contains duplicate scenario IDs")
    return files, set(active_ids)


def _order(identifier: str) -> tuple[int, int]:
    use_case, scenario = identifier.removeprefix("UC-").split("-S")
    return int(use_case), int(scenario)


def _retained_files(workdir: Path) -> dict[str, str]:
    """Fingerprint every retained technical artifact, including metadata and ZIPs."""
    return {path.relative_to(workdir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(workdir.rglob("*")) if path.is_file()}


def _run(identifier: str, path: Path | None, workdir: Path | None = None) -> dict:
    started = time.monotonic()
    record = {"id": identifier, "status": "failed", "checks": []}
    module_name = f"tests.scenarios.{identifier}"
    try:
        if path is None:
            raise FileNotFoundError(f"TODO scenario has no test file: {identifier}.py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load scenario: {identifier}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        run = getattr(module, "run", None)
        if not callable(run):
            raise NotImplementedError(f"{identifier} must provide run(workdir)")
        if workdir is None:
            with tempfile.TemporaryDirectory(prefix=f"any-to-md-{identifier}-") as directory:
                outcome = run(Path(directory))
            record["artifacts_retained"] = False
        else:
            workdir.mkdir(parents=True, exist_ok=False)
            record.update(workdir=str(workdir), artifacts_retained=True)
            outcome = run(workdir)
        if not isinstance(outcome, dict):
            raise NotImplementedError("Scenario returned no explicit verification result")
        if outcome.get("status", "passed") != "passed":
            raise AssertionError(f"Scenario did not pass: {outcome.get('status')}")
        checks = outcome.get("checks")
        if not isinstance(checks, list) or not checks or any(not isinstance(check, str) or not check.strip() for check in checks):
            raise NotImplementedError("Scenario must report a nonempty list of verified checks")
        record.update(status="passed", checks=checks)
        for field in ("evidence", "limitations"):
            if field in outcome:
                record[field] = outcome[field]
    except VisualReviewRequired as error:
        record.update(status="blocked", reason=str(error), error_type=type(error).__name__)
        record["partial_checks"] = error.technical_checks
        record["pending_review"] = {
            "technical_complete": True,
            "identity": error.identity,
            "sources": [str(path.resolve()) for path in error.sources],
            "exports": [str(path.resolve()) for path in error.exports],
            "required_checks": error.required_checks,
        }
    except Blocked as error:
        record.update(status="blocked", reason=str(error), error_type=type(error).__name__)
        if getattr(error, "technical_checks", None):
            record["partial_checks"] = error.technical_checks
    except (Exception, SystemExit) as error:
        record.update(reason=str(error), error_type=type(error).__name__, traceback=traceback.format_exc())
    finally:
        sys.modules.pop(module_name, None)
    if "pending_review" in record and workdir is not None:
        record["pending_review"]["artifact_sha256"] = _retained_files(workdir)
    record["elapsed_seconds"] = round(time.monotonic() - started, 4)
    return record


def execute(identifiers: list[str] | None = None, *, artifact_parent: Path | None = None) -> dict:
    """Execute all discovered/required scenarios, or an explicit selection."""
    if sys.flags.optimize:
        raise RuntimeError("Scenario assertions require Python without -O or -OO")
    files, active = inventory()
    available = files.keys() | active
    selected = sorted(available, key=_order) if not identifiers else identifiers
    if len(selected) != len(set(selected)):
        raise ValueError("Select each scenario only once")
    unknown = set(selected) - available
    if unknown:
        raise ValueError(f"Unknown scenario IDs: {', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("No acceptance scenarios were discovered")
    fingerprint = code_fingerprint()
    run_directory = None
    if artifact_parent is not None:
        run_directory = artifact_parent.resolve() / uuid.uuid4().hex
        run_directory.mkdir(parents=True, exist_ok=False)
    results = []
    for identifier in selected:
        workdir = run_directory / identifier if run_directory else None
        record = _run(identifier, files.get(identifier), workdir)
        results.append(record)
        detail = f": {record['reason']}" if record.get("reason") else ""
        print(f"{identifier}: {record['status']}{detail}", flush=True)
    unchanged = code_fingerprint() == fingerprint
    report = {
        "schema_version": 1,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.executable,
        "scope": "all" if not identifiers else "selected",
        "run_directory": str(run_directory) if run_directory else None,
        "code_fingerprint": fingerprint,
        "code_unchanged_during_run": unchanged,
        "active_todo_scenarios": sorted(active, key=_order),
        "results": results,
        "counts": {status: sum(result["status"] == status for result in results) for status in ("passed", "failed", "blocked")},
        "passed": unchanged and all(result["status"] == "passed" for result in results),
    }
    if not unchanged:
        report["reason"] = "Execution inputs changed during the run; rerun scenarios against unchanged code"
    return report


def finalize_review(report_path: Path, evidence_path: Path, identifiers: list[str] | None = None) -> dict:
    """Accept human review only for an unchanged, technically complete retained run."""
    if sys.flags.optimize:
        raise RuntimeError("Scenario assertions require Python without -O or -OO")
    from tests.scenarios.delivery_support import validate_review, visual_identity

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("The report does not contain scenario results")
    if report.get("code_unchanged_during_run") is not True or report.get("code_fingerprint") != code_fingerprint():
        raise ValueError("Execution code, dependencies or TODO changed; run the scenarios again before review")
    records = {item["id"]: item for item in report["results"]}
    if len(records) != len(report["results"]):
        raise ValueError("The report contains duplicate scenario IDs")
    selected = identifiers or [identifier for identifier, item in records.items() if "pending_review" in item]
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Select one or more distinct scenarios awaiting visual review")
    if set(selected) - records.keys():
        raise ValueError("A selected scenario is not in the retained report")
    evidence_path = evidence_path.resolve()
    evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if not isinstance(report.get("run_directory"), str) or not report["run_directory"]:
        raise ValueError("The report has no retained run directory")
    run_directory = Path(report["run_directory"]).resolve()
    finalized = copy.deepcopy(report)
    updates = {item["id"]: item for item in finalized["results"]}
    for identifier in selected:
        record = records[identifier]
        pending = record.get("pending_review")
        if (record.get("status") != "blocked" or record.get("error_type") != "VisualReviewRequired"
                or not isinstance(pending, dict) or pending.get("technical_complete") is not True):
            raise ValueError(f"{identifier} has no completed technical run awaiting visual review")
        if record.get("artifacts_retained") is not True or not record.get("workdir"):
            raise ValueError(f"{identifier} has no retained artifacts; rerun it with --report")
        workdir = Path(record["workdir"]).resolve()
        if not workdir.is_relative_to(run_directory) or not workdir.is_dir():
            raise ValueError(f"{identifier} retained work directory is missing or invalid")
        artifact_hashes = pending.get("artifact_sha256")
        if not isinstance(artifact_hashes, dict) or not artifact_hashes or _retained_files(workdir) != artifact_hashes:
            raise ValueError(f"{identifier} retained technical artifacts changed after execution; keep review evidence outside its work directory")
        sources = [Path(value).resolve() for value in pending.get("sources", [])]
        exports = [Path(value).resolve() for value in pending.get("exports", [])]
        required_checks = pending.get("required_checks")
        if (not sources or not exports or not isinstance(required_checks, list) or not required_checks
                or any(not isinstance(check, str) or not check.strip() for check in required_checks)):
            raise ValueError(f"{identifier} has incomplete review requirements")
        if any(not path.exists() or not path.is_relative_to(workdir) for path in sources + exports):
            raise ValueError(f"{identifier} review inputs are missing or outside its retained work directory")
        identity = visual_identity(identifier, sources, exports, required_checks)
        if identity != pending.get("identity"):
            raise ValueError(f"{identifier} retained sources or reading artifacts changed after execution")
        review = validate_review(identity, evidence_path)
        if _retained_files(workdir) != artifact_hashes:
            raise ValueError(f"{identifier} retained technical artifacts changed during review validation")
        updated = updates[identifier]
        updated["status"] = "passed"
        updated["checks"] = list(record.get("partial_checks", [])) + [f"人工阅读：{name}" for name in required_checks]
        updated["review"] = {
            "previous_outcome": {"status": record["status"], "reason": record.get("reason"),
                                 "error_type": record["error_type"]},
            "requirements": pending,
            "evidence_path": str(evidence_path),
            "evidence_sha256": evidence_digest,
            "reviewer": review["reviewer"],
            "reviewed_at": review["reviewed_at"],
            "conclusion": review["conclusion"],
            "checks": review["checks"],
        }
        for field in ("pending_review", "partial_checks", "reason", "error_type", "traceback"):
            updated.pop(field, None)
    if code_fingerprint() != report["code_fingerprint"] or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evidence_digest:
        raise ValueError("Execution inputs or review evidence changed during validation; review was not applied")
    finalized["counts"] = {status: sum(item["status"] == status for item in finalized["results"])
                           for status in ("passed", "failed", "blocked")}
    installation = finalized.get("installation", {})
    finalized["passed"] = (bool(finalized["results"]) and all(item["status"] == "passed" for item in finalized["results"])
                           and installation.get("status", "passed") == "passed")
    finalized["review_finalized_at"] = datetime.now(timezone.utc).isoformat()
    return finalized


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", metavar="UC-01-S1")
    parser.add_argument("--list", action="store_true", help="List scenario IDs without running them")
    parser.add_argument("--report", type=Path, help="Write the current scenario results as JSON")
    parser.add_argument("--review-evidence", type=Path, help="Finalize pending visual review in an existing --report without rerunning conversion")
    args = parser.parse_args(argv)
    if args.review_evidence:
        if not args.report or args.list:
            parser.error("--review-evidence requires an existing --report and cannot be combined with --list")
        try:
            report = finalize_review(args.report, args.review_evidence, args.scenarios or None)
            write_report(args.report, report)
        except Exception as error:
            print(f"Visual review was not applied: {error}", file=sys.stderr)
            return 1
        for record in report["results"]:
            print(f"{record['id']}: {record['status']}", flush=True)
        return 0 if report["passed"] else 1
    try:
        if args.list:
            files, active = inventory()
            available = files.keys() | active
            selected = args.scenarios or sorted(available, key=_order)
            if set(selected) - available:
                raise ValueError("Requested scenario ID is not in the test files or TODO.md")
            for identifier in selected:
                print(f"{identifier}{' (missing file)' if identifier not in files else ''}")
            return 0
        artifact_parent = args.report.absolute().parent / "artifacts" if args.report else None
        report = execute(args.scenarios or None, artifact_parent=artifact_parent)
    except (OSError, ValueError, RuntimeError) as error:
        report = {"passed": False, "error_type": type(error).__name__, "reason": str(error), "results": []}
        print(f"Scenario runner failed: {error}", file=sys.stderr)
    if args.report:
        write_report(args.report, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
