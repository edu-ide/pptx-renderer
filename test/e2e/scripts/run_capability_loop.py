#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


E2E_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = E2E_DIR.parents[1]
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from oracle.capability_contract import (  # noqa: E402
    AcceptanceHistory,
    load_acceptance_history,
    load_capability_registry,
    validate_acceptance_history,
)
from oracle.capability_evidence import (  # noqa: E402
    build_promotion_receipt,
    evaluate_evidence_state,
    sanitize_receipt_for_tracking,
)
from oracle.capability_inventory import (  # noqa: E402
    inventory_to_dict,
    scan_corpus,
)
from oracle.capability_verification import (  # noqa: E402
    normalize_native_evaluation_reports,
)
from oracle.capability_ranking import (  # noqa: E402
    build_ledger_rows,
    build_work_packet,
    ledger_row_from_dict,
    ledger_row_to_dict,
    rank_capabilities,
    ranked_capability_to_dict,
    select_ranked_capability,
)
from oracle.provenance import detect_renderer_git_state  # noqa: E402


class CapabilityLoopError(ValueError):
    pass


def _path(value: str | Path | None, repo: Path, default: str) -> Path:
    path = Path(value) if value is not None else Path(default)
    return path if path.is_absolute() else repo / path


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CapabilityLoopError(f"cannot read JSON input: {path}") from error
    except json.JSONDecodeError as error:
        raise CapabilityLoopError(f"invalid JSON input: {path}: {error.msg}") from error


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{encoded}\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo(args: argparse.Namespace) -> Path:
    repo = Path(args.repo_root).resolve()
    if not repo.is_dir():
        raise CapabilityLoopError(f"repository root does not exist: {repo}")
    return repo


def _contracts(args: argparse.Namespace):
    repo = _repo(args)
    registry_path = _path(
        getattr(args, "registry", None),
        repo,
        "test/e2e/oracle/capabilities.json",
    )
    acceptance_path = _path(
        getattr(args, "acceptance", None),
        repo,
        "test/e2e/oracle/capability-acceptance.json",
    )
    registry = load_capability_registry(registry_path)
    history = load_acceptance_history(acceptance_path)
    validate_acceptance_history(registry, history)
    return repo, registry_path, acceptance_path, registry, history


def command_validate(args: argparse.Namespace) -> int:
    _, _, _, registry, history = _contracts(args)
    print(
        f"validated {len(registry.capabilities)} capabilities and "
        f"{len(history.receipts)} historical verification records"
    )
    return 0


def command_inventory(args: argparse.Namespace) -> int:
    repo = _repo(args)
    registry_path = _path(args.registry, repo, "test/e2e/oracle/capabilities.json")
    registry = load_capability_registry(registry_path)
    corpus_values = args.corpus or ["test/e2e/testdata/cases"]
    roots = [Path(value) if Path(value).is_absolute() else repo / value for value in corpus_values]
    report = scan_corpus(roots, registry)
    default_case_policy = (
        args.corpus is None
        and not args.representative_alias
        and not args.validation_alias
    )
    default_validation_pattern = "corpus-0/*oracle-*"
    validation_alias_globs = tuple(args.validation_alias)
    if default_case_policy and any(
        fnmatch.fnmatchcase(alias, default_validation_pattern)
        for package in (*report.packages, *report.rejected_packages)
        for alias in package.aliases
    ):
        validation_alias_globs = (default_validation_pattern,)
    payload = inventory_to_dict(
        report,
        representative_alias_globs=tuple(args.representative_alias),
        validation_alias_globs=validation_alias_globs,
    )
    if default_case_policy:
        payload["corpusClassification"]["mode"] = "default-testdata-case-convention"
        payload["corpusClassification"]["validationAliasGlobs"] = [
            default_validation_pattern
        ]
    revision, dirty = detect_renderer_git_state(repo)
    payload["renderer"] = {"revision": revision, "dirty": dirty}
    if args.issues:
        issue_path = _path(args.issues, repo, args.issues)
        issues = _load_json(issue_path)
        if not isinstance(issues, list):
            raise CapabilityLoopError("issue snapshot must be a JSON list")
        payload["issueSnapshot"] = {
            "sha256": _sha256(issue_path),
            "count": len(issues),
        }
    output = _path(args.out, repo, "test/e2e/reports/capability-loop/inventory.json")
    _write_json(output, payload)
    if args.fail_on_rejected and report.rejected_package_count:
        raise CapabilityLoopError(
            f"inventory rejected {report.rejected_package_count} package(s); report written to {output}"
        )
    print(
        f"inventoried {report.raw_package_count} packages, "
        f"{report.unique_package_count} unique, "
        f"{report.rejected_package_count} rejected -> {output}"
    )
    return 0


def _issue_rows(path: Path | None) -> list[Mapping[str, Any]]:
    if path is None:
        return []
    payload = _load_json(path)
    if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
        raise CapabilityLoopError("issue snapshot must be a JSON list of objects")
    return list(payload)


def command_rank(args: argparse.Namespace) -> int:
    repo = _repo(args)
    registry_path = _path(args.registry, repo, "test/e2e/oracle/capabilities.json")
    registry = load_capability_registry(registry_path)
    inventory_path = _path(args.inventory, repo, args.inventory)
    inventory = _load_json(inventory_path)
    if not isinstance(inventory, Mapping) or inventory.get("schemaVersion") != 1:
        raise CapabilityLoopError("inventory requires schemaVersion=1")
    issue_path = _path(args.issues, repo, args.issues) if args.issues else None
    issues = _issue_rows(issue_path)
    oracle_report = None
    if args.oracle_report:
        value = _load_json(_path(args.oracle_report, repo, args.oracle_report))
        if not isinstance(value, Mapping):
            raise CapabilityLoopError("oracle report must be a JSON object")
        oracle_report = value
    acceptance_path = _path(
        args.acceptance,
        repo,
        "test/e2e/oracle/capability-acceptance.json",
    )
    accepted_states: dict[str, str] = {}
    if acceptance_path.is_file():
        history = load_acceptance_history(acceptance_path)
        validate_acceptance_history(registry, history)
        latest_receipts = {receipt.capability_id: receipt for receipt in history.receipts}
        for capability_id, receipt in latest_receipts.items():
            accepted_states[capability_id] = evaluate_evidence_state(
                registry.by_id()[capability_id],
                receipt,
                None,
                repo,
            ).name
    rows = build_ledger_rows(
        registry,
        inventory,
        issues=issues,
        oracle_report=oracle_report,
        accepted_states=accepted_states,
    )
    ranked = rank_capabilities(rows)
    ledger_payload = {
        "schemaVersion": 1,
        "renderer": inventory.get("renderer"),
        "inventorySha256": _sha256(inventory_path),
        "rows": [ledger_row_to_dict(row) for row in rows],
    }
    ranking_payload = {
        "schemaVersion": 1,
        "ledgerInputSha256": hashlib.sha256(
            json.dumps(ledger_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "ranking": [ranked_capability_to_dict(item) for item in ranked],
    }
    ledger_output = _path(
        args.ledger_out,
        repo,
        "test/e2e/reports/capability-loop/ledger.json",
    )
    ranking_output = _path(
        args.ranking_out,
        repo,
        "test/e2e/reports/capability-loop/ranking.json",
    )
    _write_json(ledger_output, ledger_payload)
    _write_json(ranking_output, ranking_payload)
    print(f"ranked {len(ranked)} capabilities -> {ranking_output}")
    return 0


def command_work_packet(args: argparse.Namespace) -> int:
    repo = _repo(args)
    registry_path = _path(args.registry, repo, "test/e2e/oracle/capabilities.json")
    registry = load_capability_registry(registry_path)
    ledger_path = _path(args.ledger, repo, args.ledger)
    ledger = _load_json(ledger_path)
    if not isinstance(ledger, Mapping) or ledger.get("schemaVersion") != 1:
        raise CapabilityLoopError("ledger requires schemaVersion=1")
    values = ledger.get("rows")
    if not isinstance(values, list):
        raise CapabilityLoopError("ledger rows must be a list")
    rows = tuple(ledger_row_from_dict(value) for value in values if isinstance(value, Mapping))
    ranked = rank_capabilities(rows)
    selected, selection = select_ranked_capability(
        ranked,
        args.capability,
        args.selection_reason,
    )
    packet = build_work_packet(selected, registry)
    packet["selection"] = selection
    packet["ledgerSha256"] = _sha256(ledger_path)
    output = _path(
        args.out,
        repo,
        "test/e2e/reports/capability-loop/work-packet.json",
    )
    _write_json(output, packet)
    print(f"wrote work packet for {selected.capability_id} -> {output}")
    return 0


def _load_object_reports(values: Sequence[str], repo: Path, label: str) -> list[Mapping[str, Any]]:
    reports: list[Mapping[str, Any]] = []
    for value in values:
        report = _load_json(_path(value, repo, value))
        if not isinstance(report, Mapping):
            raise CapabilityLoopError(f"{label} must contain JSON objects")
        reports.append(report)
    return reports


def _manual_verdicts(values: Sequence[str]) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for value in values:
        case_id, separator, verdict = value.partition("=")
        if not separator or not case_id or verdict not in {"passed", "accepted"}:
            raise CapabilityLoopError(
                "manual verdict must use CASE_ID=passed or CASE_ID=accepted"
            )
        if case_id in verdicts:
            raise CapabilityLoopError(f"duplicate manual verdict for case: {case_id}")
        verdicts[case_id] = verdict
    return verdicts


def command_verify(args: argparse.Namespace) -> int:
    repo = _repo(args)
    registry_path = _path(args.registry, repo, "test/e2e/oracle/capabilities.json")
    registry = load_capability_registry(registry_path)
    capability = registry.by_id().get(args.capability)
    if capability is None:
        raise CapabilityLoopError(f"unknown capability: {args.capability}")
    if capability.render_mode != "native":
        raise CapabilityLoopError("verify requires renderMode=native for the bounded capability")
    revision, dirty = detect_renderer_git_state(repo)
    if revision is None or dirty is not False:
        raise CapabilityLoopError("verify requires a clean repository with a readable HEAD")
    reports = _load_object_reports(args.case_report, repo, "case report")
    baselines = _load_object_reports(args.baseline_report, repo, "baseline report")
    verification = normalize_native_evaluation_reports(
        capability,
        reports,
        repo,
        oracle=args.oracle,
        baseline_reports=baselines,
        passed_gates=args.passed_gate,
        manual_verdicts=_manual_verdicts(args.manual_verdict),
        bevel_report=(
            _load_json(_path(args.bevel_report, repo, args.bevel_report))
            if args.bevel_report
            else None
        ),
        camera_report=(
            _load_json(_path(args.camera_report, repo, args.camera_report))
            if args.camera_report
            else None
        ),
        shadow_report=(
            _load_json(_path(args.shadow_report, repo, args.shadow_report))
            if args.shadow_report
            else None
        ),
        reflection_report=(
            _load_json(_path(args.reflection_report, repo, args.reflection_report))
            if args.reflection_report
            else None
        ),
    )
    if verification["renderer"]["revision"] != revision:
        raise CapabilityLoopError("native case reports do not match current HEAD")
    output = _path(
        args.out,
        repo,
        "test/e2e/reports/capability-loop/verification.json",
    )
    _write_json(output, verification)
    print(f"verified {len(reports)} native case report(s) for {capability.id} -> {output}")
    return 0


def _assert_tracked(repo: Path, path: Path) -> None:
    try:
        relative = path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as error:
        raise CapabilityLoopError("acceptance history must be inside the repository") from error
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CapabilityLoopError("acceptance history must be a tracked repository file")


def command_accept(args: argparse.Namespace) -> int:
    repo, _, acceptance_path, registry, history = _contracts(args)
    revision, dirty = detect_renderer_git_state(repo)
    if revision is None or dirty is not False:
        raise CapabilityLoopError("accept requires a clean repository with a readable HEAD")
    _assert_tracked(repo, acceptance_path)
    capability = registry.by_id().get(args.capability)
    if capability is None:
        raise CapabilityLoopError(f"unknown capability: {args.capability}")
    if capability.render_mode != "native":
        raise CapabilityLoopError("accept requires renderMode=native for the bounded capability")
    verification_path = _path(args.verification, repo, args.verification)
    verification = _load_json(verification_path)
    if not isinstance(verification, Mapping):
        raise CapabilityLoopError("verification report must be a JSON object")
    renderer = verification.get("renderer")
    if not isinstance(renderer, Mapping) or renderer.get("revision") != revision:
        raise CapabilityLoopError("verification renderer revision does not match current HEAD")
    accepted_at = args.accepted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt = build_promotion_receipt(capability, verification, repo, accepted_at)
    if any(
        existing.capability_id == receipt.capability_id
        and existing.accepted_revision == receipt.accepted_revision
        and existing.case_input_sha256 == receipt.case_input_sha256
        and existing.ground_truth_sha256 == receipt.ground_truth_sha256
        for existing in history.receipts
    ):
        raise CapabilityLoopError("an equivalent verification record already exists")
    receipts = tuple(
        sorted(
            (
                *(item for item in history.receipts if item.capability_id != receipt.capability_id),
                receipt,
            ),
            key=lambda item: (item.capability_id, item.accepted_at),
        )
    )
    candidate_history = AcceptanceHistory(schema_version=2, receipts=receipts)
    validate_acceptance_history(registry, candidate_history)
    payload = {
        "schemaVersion": 2,
        "receipts": [sanitize_receipt_for_tracking(item) for item in receipts],
    }
    temporary = acceptance_path.with_name(f".{acceptance_path.name}.{os.getpid()}.candidate")
    try:
        _write_json(temporary, payload)
        parsed = load_acceptance_history(temporary)
        validate_acceptance_history(registry, parsed)
        os.replace(temporary, acceptance_path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"accepted {capability.id} at {revision}")
    return 0


def _add_common_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--registry")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-driven PPTX renderer capability loop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate tracked capability contracts")
    _add_common_contract_arguments(validate)
    validate.add_argument("--acceptance")
    validate.set_defaults(handler=command_validate)

    inventory = subparsers.add_parser("inventory", help="scan bounded PPTX corpora")
    _add_common_contract_arguments(inventory)
    inventory.add_argument("--corpus", action="append")
    inventory.add_argument(
        "--representative-alias",
        action="append",
        default=[],
        help=(
            "alias glob for representative documents; repeat as needed. "
            "When present, unmatched packages are validation fixtures"
        ),
    )
    inventory.add_argument(
        "--validation-alias",
        action="append",
        default=[],
        help=(
            "alias glob for generated validation fixtures; repeat as needed. "
            "When present, unmatched packages are representative documents"
        ),
    )
    inventory.add_argument("--issues")
    inventory.add_argument("--out")
    inventory.add_argument("--fail-on-rejected", action="store_true")
    inventory.set_defaults(handler=command_inventory)

    rank = subparsers.add_parser("rank", help="reconcile and rank capability evidence")
    _add_common_contract_arguments(rank)
    rank.add_argument("--inventory", required=True)
    rank.add_argument("--issues")
    rank.add_argument("--acceptance")
    rank.add_argument("--oracle-report")
    rank.add_argument("--ledger-out")
    rank.add_argument("--ranking-out")
    rank.set_defaults(handler=command_rank)

    work_packet = subparsers.add_parser("work-packet", help="emit one bounded work packet")
    _add_common_contract_arguments(work_packet)
    work_packet.add_argument("--ledger", required=True)
    work_packet.add_argument("--capability", required=True)
    work_packet.add_argument("--selection-reason")
    work_packet.add_argument("--out")
    work_packet.set_defaults(handler=command_work_packet)

    verify = subparsers.add_parser("verify", help="normalize native evaluation evidence")
    _add_common_contract_arguments(verify)
    verify.add_argument("--capability", required=True)
    verify.add_argument("--case-report", action="append", required=True)
    verify.add_argument("--baseline-report", action="append", default=[])
    verify.add_argument("--oracle", required=True)
    verify.add_argument("--passed-gate", action="append", default=[])
    verify.add_argument("--manual-verdict", action="append", default=[])
    verify.add_argument("--bevel-report")
    verify.add_argument("--camera-report")
    verify.add_argument("--shadow-report")
    verify.add_argument("--reflection-report")
    verify.add_argument("--out")
    verify.set_defaults(handler=command_verify)

    accept = subparsers.add_parser(
        "accept", help="write one historical verification record"
    )
    _add_common_contract_arguments(accept)
    accept.add_argument("--acceptance")
    accept.add_argument("--verification", required=True)
    accept.add_argument("--capability", required=True)
    accept.add_argument("--accepted-at")
    accept.set_defaults(handler=command_accept)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (CapabilityLoopError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
