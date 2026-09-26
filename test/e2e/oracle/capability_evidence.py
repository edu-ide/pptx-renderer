from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from oracle.capability_contract import CapabilityDefinition, PromotionReceipt
from oracle.provenance import detect_renderer_git_state


_SHA256_LENGTH = 64


class CapabilityEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceState:
    name: str
    reasons: tuple[str, ...]


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _report_mapping(report: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = report.get(key)
    return value if isinstance(value, Mapping) else {}


def _report_cases(report: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = report.get("caseResults")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _case_hashes(
    cases: tuple[Mapping[str, Any], ...],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
    case_ids: list[str] = []
    source_hashes: list[str] = []
    ground_truth_hashes: list[str] = []
    for case in cases:
        case_id = case.get("caseId")
        source_hash = case.get("sourceSha256")
        ground_truth_hash = case.get("groundTruthSha256")
        if (
            not isinstance(case_id, str)
            or not case_id
            or not _is_sha256(source_hash)
            or not _is_sha256(ground_truth_hash)
        ):
            return None
        case_ids.append(case_id)
        source_hashes.append(source_hash)
        ground_truth_hashes.append(ground_truth_hash)
    if not case_ids or len(case_ids) != len(set(case_ids)):
        return None
    return tuple(case_ids), tuple(source_hashes), tuple(ground_truth_hashes)


def _verification_reasons(
    capability: CapabilityDefinition,
    report: Mapping[str, Any],
    expected_revision: str | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if report.get("schemaVersion") != 2:
        return ("evidence:unsupported-schema",)
    if report.get("capabilityId") != capability.id:
        return ("evidence:capability-id-mismatch",)

    renderer = _report_mapping(report, "renderer")
    if renderer.get("dirty") is not False:
        reasons.append("evidence:dirty-worktree")
    revision = renderer.get("revision")
    if not isinstance(revision, str) or len(revision) < 40:
        reasons.append("evidence:missing-renderer-revision")
    elif expected_revision is not None and revision != expected_revision:
        reasons.append("evidence:renderer-revision-drift")

    gates = _report_mapping(report, "gates")
    for gate in capability.required_gates:
        status = gates.get(gate)
        if status != "passed":
            reasons.append(f"gate:{gate}:{status if isinstance(status, str) else 'missing'}")

    cases = _report_cases(report)
    for case in cases:
        case_id = case.get("caseId") if isinstance(case.get("caseId"), str) else "unknown"
        if case.get("skipped") is True:
            reasons.append("evidence:skipped-required-case")
        if case.get("passed") is not True:
            reasons.append(f"case:{case_id}:failed")
        runtime_errors = case.get("runtimeErrors")
        if not isinstance(runtime_errors, list) or runtime_errors:
            reasons.append(f"case:{case_id}:runtime-error")
        if case.get("needsReview") is True and case.get("manualVerdict") not in {
            "passed",
            "accepted",
        }:
            reasons.append("evidence:manual-review-required")
    return tuple(dict.fromkeys(reasons))


def evaluate_evidence_state(
    capability: CapabilityDefinition,
    receipt: PromotionReceipt | None,
    report: Mapping[str, Any] | None,
    repo: Path,
) -> EvidenceState:
    if receipt is not None and receipt.capability_id != capability.id:
        return EvidenceState("regressed", ("evidence:receipt-capability-id-mismatch",))

    if report is None:
        if receipt is not None:
            return EvidenceState(
                "historical",
                ("evidence:historical-verification-record",),
            )
        return EvidenceState("unknown", ("evidence:no-verification-report",))

    cases = _report_cases(report)
    if _case_hashes(cases) is None:
        return EvidenceState("reproducible", ("evidence:missing-input-hash",))

    revision, dirty = detect_renderer_git_state(repo)
    reasons = list(_verification_reasons(capability, report, revision))
    if dirty is not False:
        reasons.append("evidence:current-worktree-not-clean")
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        report_revision = _report_mapping(report, "renderer").get("revision")
        if receipt is not None and receipt.accepted_revision == report_revision == revision:
            return EvidenceState("regressed", tuple(reasons))
        return EvidenceState("candidate", tuple(reasons))
    return EvidenceState("verified", ())


def build_promotion_receipt(
    capability: CapabilityDefinition,
    report: Mapping[str, Any],
    repo: Path,
    accepted_at: str,
) -> PromotionReceipt:
    revision, dirty = detect_renderer_git_state(repo)
    reasons = list(_verification_reasons(capability, report, revision))
    if revision is None:
        reasons.append("evidence:missing-current-revision")
    if dirty is not False:
        reasons.append("evidence:current-worktree-not-clean")
    cases = _report_cases(report)
    case_hashes = _case_hashes(cases)
    if case_hashes is None:
        reasons.append("evidence:missing-input-hash")
    if reasons:
        raise CapabilityEvidenceError(
            "cannot record verification: " + ", ".join(dict.fromkeys(reasons))
        )
    renderer = _report_mapping(report, "renderer")
    revision = renderer["revision"]
    case_ids, source_hashes, ground_truth_hashes = case_hashes
    environment = report.get("environment")
    if not isinstance(environment, Mapping) or not environment:
        raise CapabilityEvidenceError(
            "cannot record verification: evidence:missing-environment"
        )
    gates = _report_mapping(report, "gates")
    passed_gates = tuple(gate for gate in capability.required_gates if gates.get(gate) == "passed")
    return PromotionReceipt(
        capability_id=capability.id,
        accepted_revision=revision,
        case_ids=case_ids,
        case_input_sha256=source_hashes,
        ground_truth_sha256=ground_truth_hashes,
        gates=passed_gates,
        environment=dict(environment),
        accepted_at=accepted_at,
    )


def _sanitize_value(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if any(token in normalized_key for token in ("path", "username", "issuebody", "caselabel")):
        return None
    if isinstance(value, Mapping):
        result = {}
        for child_key, child_value in sorted(value.items()):
            if not isinstance(child_key, str):
                continue
            sanitized = _sanitize_value(child_value, child_key)
            if sanitized is not None:
                result[child_key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        return [item for item in (_sanitize_value(item, key) for item in value) if item is not None]
    if isinstance(value, str):
        if (
            value.startswith(("/", "~", "\\\\", "file://"))
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or "/Users/" in value
            or "/home/" in value
        ):
            return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def sanitize_receipt_for_tracking(receipt: PromotionReceipt) -> dict[str, Any]:
    environment = _sanitize_value(receipt.environment)
    if not isinstance(environment, dict) or not environment:
        raise CapabilityEvidenceError("sanitized verification environment must not be empty")
    payload = {
        "capabilityId": receipt.capability_id,
        "acceptedRevision": receipt.accepted_revision,
        "caseIds": list(receipt.case_ids),
        "caseInputSha256": list(receipt.case_input_sha256),
        "groundTruthSha256": list(receipt.ground_truth_sha256),
        "gates": list(receipt.gates),
        "environment": environment,
        "acceptedAt": receipt.accepted_at,
    }
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload
