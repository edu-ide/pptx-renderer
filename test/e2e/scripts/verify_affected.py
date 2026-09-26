#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence


E2E_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = E2E_DIR.parents[1]
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from oracle.capability_contract import (  # noqa: E402
    load_acceptance_history,
    load_capability_registry,
)
from oracle.verification_impact import build_verification_plan  # noqa: E402


class VerificationImpactError(RuntimeError):
    pass


def _git_paths(repo: Path, args: Sequence[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr.decode(errors="replace").strip()
            or result.stdout.decode(errors="replace").strip()
            or "git command failed"
        )
        raise VerificationImpactError(message)
    return [
        value.decode(sys.getfilesystemencoding(), errors="surrogateescape")
        for value in result.stdout.split(b"\0")
        if value
    ]


def _git_blob(repo: Path, revision: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def _is_release_metadata_package_change(before: bytes, after: bytes) -> bool:
    try:
        before_payload = json.loads(before)
        after_payload = json.loads(after)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(before_payload, dict) or not isinstance(after_payload, dict):
        return False
    changed_keys = {
        key
        for key in set(before_payload).union(after_payload)
        if before_payload.get(key) != after_payload.get(key)
    }
    before_version = before_payload.get("version")
    after_version = after_payload.get("version")
    return (
        isinstance(before_version, str)
        and isinstance(after_version, str)
        and before_version != after_version
        and changed_keys.issubset({"version", "knip"})
    )


def _detect_release_metadata_paths(
    repo: Path,
    base: str | None,
    head: str,
    changed_paths: Sequence[str],
) -> list[str]:
    if "package.json" not in changed_paths:
        return []

    if base:
        merge_base = subprocess.run(
            ["git", "merge-base", base, head],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            return []
        before = _git_blob(repo, merge_base.stdout.strip(), "package.json")
        after = _git_blob(repo, head, "package.json")
    else:
        working_change = subprocess.run(
            ["git", "status", "--porcelain", "--", "package.json"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        if working_change.returncode != 0:
            return []
        if working_change.stdout.strip():
            before = _git_blob(repo, head, "package.json")
            try:
                after = (repo / "package.json").read_bytes()
            except OSError:
                return []
        else:
            before = _git_blob(repo, f"{head}^", "package.json")
            after = _git_blob(repo, head, "package.json")

    if before is None or after is None:
        return []
    return ["package.json"] if _is_release_metadata_package_change(before, after) else []


def _discover_changed_paths(repo: Path, base: str | None, head: str) -> list[str]:
    if base:
        return _git_paths(
            repo,
            ["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", f"{base}...{head}"],
        )

    paths = {
        *_git_paths(repo, ["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB"]),
        *_git_paths(
            repo,
            ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACDMRTUXB"],
        ),
        *_git_paths(repo, ["ls-files", "-z", "--others", "--exclude-standard"]),
    }
    if paths:
        return sorted(paths)
    try:
        return _git_paths(
            repo,
            ["diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", "HEAD^", "HEAD"],
        )
    except VerificationImpactError:
        return _git_paths(repo, ["show", "--pretty=format:", "--name-only", "-z", "HEAD"])


def _repository_paths(repo: Path) -> set[str]:
    return {
        *_git_paths(repo, ["ls-files", "-z"]),
        *_git_paths(repo, ["ls-files", "-z", "--others", "--exclude-standard"]),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _visible_slide_numbers(pptx_path: Path) -> list[int]:
    presentation_namespace = "http://schemas.openxmlformats.org/presentationml/2006/main"
    office_rel_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(pptx_path) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(
            archive.read("ppt/_rels/presentation.xml.rels")
        )
        targets = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationships.findall(
                f"{{{package_rel_namespace}}}Relationship"
            )
        }
        visible: list[int] = []
        slide_ids = presentation.findall(
            f".//{{{presentation_namespace}}}sldId"
        )
        for slide_number, slide_id in enumerate(slide_ids, start=1):
            relationship_id = slide_id.get(f"{{{office_rel_namespace}}}id")
            target = targets.get(relationship_id)
            if not target:
                raise ValueError(f"slide relationship is missing: {relationship_id}")
            slide_part = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join("ppt", target))
            )
            slide = ET.fromstring(archive.read(slide_part))
            if slide.get("show") != "0":
                visible.append(slide_number)
        return visible


def _combined_ground_truth_sha256(case_dir: Path, pptx_path: Path) -> str | None:
    try:
        visible_slide_numbers = _visible_slide_numbers(pptx_path)
    except (KeyError, OSError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return None
    png_paths = [
        case_dir / "slides" / f"slide{slide_number}.png"
        for slide_number in visible_slide_numbers
        if (case_dir / "slides" / f"slide{slide_number}.png").is_file()
    ]
    pdf_path = case_dir / "ground-truth.pdf"
    if png_paths and len(png_paths) == len(visible_slide_numbers):
        ground_truth_paths = png_paths
    elif png_paths and pdf_path.is_file():
        ground_truth_paths = [*png_paths, pdf_path]
    elif pdf_path.is_file():
        ground_truth_paths = [pdf_path]
    else:
        return None
    digest = hashlib.sha256()
    for path in ground_truth_paths:
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def _native_case_hashes(
    repo: Path, case_ids: Sequence[str]
) -> dict[str, set[tuple[str, str]]]:
    testdata = repo / "test" / "e2e" / "testdata"
    available: dict[str, set[tuple[str, str]]] = {}
    for case_id in sorted(set(case_ids)):
        if case_id.startswith("win__"):
            stem = case_id.removeprefix("win__")
            sources = ("windows-cases",)
        else:
            stem = case_id
            # Historical records did not encode their corpus. Exact hashes prevent
            # a same-stem case in the other corpus from satisfying the record.
            sources = ("cases", "windows-cases")
        for source_name in sources:
            case_dir = testdata / source_name / stem
            pptx_path = case_dir / "source.pptx"
            if not pptx_path.is_file():
                continue
            ground_truth_hash = _combined_ground_truth_sha256(case_dir, pptx_path)
            if ground_truth_hash is None:
                continue
            available.setdefault(case_id, set()).add(
                (_sha256(pptx_path), ground_truth_hash)
            )
    return available


def _latest_case_evidence(
    acceptance_path: Path,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, dict[str, tuple[str, str]]],
]:
    history = load_acceptance_history(acceptance_path)
    latest_ids: dict[str, tuple[str, ...]] = {}
    latest_hashes: dict[str, dict[str, tuple[str, str]]] = {}
    for receipt in history.receipts:
        latest_ids[receipt.capability_id] = receipt.case_ids
        latest_hashes[receipt.capability_id] = {
            case_id: (source_hash, ground_truth_hash)
            for case_id, source_hash, ground_truth_hash in zip(
                receipt.case_ids,
                receipt.case_input_sha256,
                receipt.ground_truth_sha256,
                strict=True,
            )
        }
    return latest_ids, latest_hashes


def _command_parts(command: Mapping[str, Any]) -> tuple[list[str], Path, str]:
    argv = command.get("argv")
    cwd_value = command.get("cwd", ".")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or not isinstance(cwd_value, str)
    ):
        raise VerificationImpactError(f"invalid planned command: {command!r}")
    cwd = (PROJECT_ROOT / cwd_value).resolve()
    try:
        cwd.relative_to(PROJECT_ROOT.resolve())
    except ValueError as error:
        raise VerificationImpactError(
            f"planned command cwd escapes repository: {cwd_value}"
        ) from error
    return argv, cwd, cwd_value


def _format_command(command: Mapping[str, Any]) -> str:
    argv, _, cwd_value = _command_parts(command)
    suffix = "" if cwd_value == "." else f" (cwd={cwd_value})"
    return f"{' '.join(argv)}{suffix}"


def _run_commands(commands: Sequence[Mapping[str, Any]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for command in commands:
        argv, cwd, cwd_value = _command_parts(command)
        started = time.monotonic()
        print(f"\n$ {_format_command(command)}", flush=True)
        completed = subprocess.run(argv, cwd=cwd, check=False)
        elapsed = round(time.monotonic() - started, 3)
        results.append(
            {
                "argv": argv,
                "cwd": cwd_value,
                "exitCode": completed.returncode,
                "elapsedSeconds": elapsed,
            }
        )
        if completed.returncode != 0:
            break
    return results


def _summary(plan: dict[str, object]) -> None:
    print(f"Verification mode: {plan['mode']}")
    print(f"Changed paths: {len(plan['changedPaths'])}")
    impacted = plan["impactedCapabilityIds"]
    print(f"Impacted capabilities: {len(impacted)}")
    for capability_id in impacted:
        print(f"  - {capability_id}")
    release_metadata = plan["releaseMetadataPaths"]
    if release_metadata:
        print("Release metadata paths:")
        for path in release_metadata:
            print(f"  - {path}")
    print(f"Planned commands: {len(plan['commands'])}")
    for command in plan["commands"]:
        print(f"  $ {_format_command(command)}")
    native_cases = plan["nativeCaseIds"]
    print(f"Deferred native cases: {len(native_cases)}")
    for case_id in native_cases:
        print(f"  - {case_id}")
    missing_artifacts = plan["nativeCasesMissingArtifacts"]
    if missing_artifacts:
        print("Native cases missing the recorded testcase artifacts:")
        for issue in plan["nativeCaseArtifactIssues"]:
            print(
                f"  - {issue['caseId']} ({issue['capabilityId']}: {issue['reason']})"
            )
    missing_native_cases = plan["nativeCapabilitiesWithoutCases"]
    if missing_native_cases:
        print("Native-gated capabilities without a recorded case set:")
        for capability_id in missing_native_cases:
            print(f"  - {capability_id}")
    if plan["requiresBrowser"]:
        print("Pre-commit gate: pnpm test:browser")
    if plan["requiresNative"]:
        if missing_native_cases:
            print("Pre-merge gate: establish cases for the capabilities above, then evaluate them")
        else:
            print("Pre-merge gate: evaluate only the deferred native cases above")
    local_gate_commands = plan["localGateCommands"]
    for command in local_gate_commands:
        print(f"Local evidence gate: {_format_command(command)}")
    missing_case_report = plan["localGatesWithoutCaseReport"]
    if missing_case_report:
        print(
            "Local evidence gates still need --case-report: "
            + ", ".join(missing_case_report)
        )
    incomplete_local_reports = plan["localGateMissingCaseReports"]
    for gate, case_ids in incomplete_local_reports.items():
        print(f"Local evidence gate {gate} is missing usable case reports/artifacts:")
        for case_id in case_ids:
            print(f"  - {case_id}")
    if plan["unclassifiedPaths"]:
        print("Unclassified paths forced a full verification plan:")
        for path in plan["unclassifiedPaths"]:
            print(f"  - {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan and optionally run checks affected by the current change."
    )
    parser.add_argument("--base", help="Git base revision. Uses base...head when supplied.")
    parser.add_argument("--head", default="HEAD", help="Git head revision (default: HEAD).")
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        help="Explicit repository-relative changed path; repeat to bypass Git discovery.",
    )
    parser.add_argument(
        "--case-report",
        action="append",
        default=[],
        help="Fresh per-case /api/evaluate JSON report; repeat for each required local-gate case.",
    )
    parser.add_argument(
        "--run-fast",
        action="store_true",
        help="Run planned commands. A fail-closed full plan includes the full browser suite.",
    )
    parser.add_argument("--json-out", help="Optional JSON report path, relative to the repository root.")
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    # pnpm may forward its separator after arguments already embedded in the package script
    # (`--run-fast -- --base ...`). It has no meaning to this option-only CLI.
    raw_args = [argument for argument in raw_args if argument != "--"]
    args = parser.parse_args(raw_args)

    registry = load_capability_registry(E2E_DIR / "oracle" / "capabilities.json")
    latest_case_ids, latest_case_hashes = _latest_case_evidence(
        E2E_DIR / "oracle" / "capability-acceptance.json"
    )
    changed_paths = args.changed_path or _discover_changed_paths(PROJECT_ROOT, args.base, args.head)
    release_metadata_paths = (
        []
        if args.changed_path
        else _detect_release_metadata_paths(
            PROJECT_ROOT,
            args.base,
            args.head,
            changed_paths,
        )
    )
    case_reports: dict[str, str] = {}
    for case_report_value in args.case_report:
        report_path = Path(case_report_value)
        if not report_path.is_absolute():
            report_path = PROJECT_ROOT / report_path
        if not report_path.is_file():
            raise VerificationImpactError(f"case report does not exist: {report_path}")
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VerificationImpactError(f"cannot read case report: {report_path}: {error}") from error
        case_id = payload.get("testFile") if isinstance(payload, dict) else None
        if not isinstance(case_id, str) or not case_id:
            raise VerificationImpactError(f"case report is missing testFile: {report_path}")
        try:
            normalized_report_path = report_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            normalized_report_path = str(report_path)
        if case_id in case_reports:
            raise VerificationImpactError(f"duplicate case report for {case_id}")
        case_reports[case_id] = normalized_report_path
    plan_arguments = {
        "changed_paths": changed_paths,
        "capabilities": registry.capabilities,
        "latest_case_ids": latest_case_ids,
        "repository_paths": _repository_paths(PROJECT_ROOT),
        "latest_case_hashes": latest_case_hashes,
        "case_reports": case_reports,
        "python_executable": sys.executable,
        "release_metadata_paths": release_metadata_paths,
    }
    plan = build_verification_plan(
        **plan_arguments,
        available_native_case_hashes={},
    )
    if plan["nativeCaseIds"]:
        native_case_hashes = _native_case_hashes(
            PROJECT_ROOT, plan["nativeCaseIds"]
        )
        plan = build_verification_plan(
            **plan_arguments,
            available_native_case_hashes=native_case_hashes,
        )
    _summary(plan)

    if args.run_fast:
        results = _run_commands(plan["commands"])
        plan["executed"] = results
        if any(result["exitCode"] != 0 for result in results):
            exit_code = next(
                int(result["exitCode"]) for result in results if result["exitCode"] != 0
            )
        else:
            exit_code = 0
    else:
        exit_code = 0

    if args.json_out:
        output = Path(args.json_out)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Report: {output}")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationImpactError as error:
        print(f"verification impact error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
