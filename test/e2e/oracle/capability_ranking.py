from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from oracle.capability_contract import (
    IMPACTS,
    PLANNING_MODES,
    RENDER_MODES,
    CapabilityDefinition,
    CapabilityRegistry,
)


IMPACT_ORDER = {
    "security": 0,
    "crash": 1,
    "missing": 2,
    "semantics": 3,
    "reliability": 4,
    "fidelity": 5,
    "enhancement": 6,
}
FAILURE_ORDER = {
    "runtime-error": 0,
    "deterministic-failure": 1,
    "native-failure": 2,
    "review-warning": 3,
    "none": 4,
}
EVIDENCE_STATES = frozenset(
    {
        "unknown",
        "observed",
        "reproducible",
        "candidate",
        "historical",
        "verified",
        "regressed",
        "blocked",
    }
)


@dataclass(frozen=True)
class LedgerRow:
    capability_id: str
    impact: str
    current_issue_count: int
    observed_unique_packages: int
    observed_representative_packages: int
    failure_kind: str
    oracle_ready: bool
    dependency_depth: int
    evidence_state: str
    render_mode: str
    blockers: tuple[str, ...]
    issue_urls: tuple[str, ...]
    planning_mode: str = "ranked"


@dataclass(frozen=True)
class RankedCapability:
    row: LedgerRow
    priority_key: tuple[int, int, int, int, int, int, int, str]
    priority_labels: tuple[tuple[str, str], ...]

    @property
    def capability_id(self) -> str:
        return self.row.capability_id


def _validate_row(row: LedgerRow) -> None:
    if not row.capability_id:
        raise ValueError("ledger capability id must not be empty")
    if row.impact not in IMPACTS or row.impact not in IMPACT_ORDER:
        raise ValueError(f"unsupported impact: {row.impact}")
    if (
        row.current_issue_count < 0
        or row.observed_unique_packages < 0
        or row.observed_representative_packages < 0
    ):
        raise ValueError("ledger counts must be non-negative")
    if row.observed_representative_packages > row.observed_unique_packages:
        raise ValueError("representative package count must not exceed total observed packages")
    if row.failure_kind not in FAILURE_ORDER:
        raise ValueError(f"unsupported failure kind: {row.failure_kind}")
    if row.dependency_depth < 0:
        raise ValueError("dependency depth must be non-negative")
    if row.evidence_state not in EVIDENCE_STATES:
        raise ValueError(f"unsupported evidence state: {row.evidence_state}")
    if row.render_mode not in RENDER_MODES:
        raise ValueError(f"unsupported render mode: {row.render_mode}")
    if row.planning_mode not in PLANNING_MODES:
        raise ValueError(f"unsupported planning mode: {row.planning_mode}")
    if len(row.blockers) != len(set(row.blockers)):
        raise ValueError(f"duplicate blockers for {row.capability_id}")


def _ranked(row: LedgerRow) -> RankedCapability:
    priority_key = (
        IMPACT_ORDER[row.impact],
        -row.current_issue_count,
        -row.observed_representative_packages,
        -row.observed_unique_packages,
        FAILURE_ORDER[row.failure_kind],
        0 if row.oracle_ready else 1,
        row.dependency_depth,
        row.capability_id,
    )
    priority_labels = (
        ("impact", row.impact),
        ("currentIssueCount", str(row.current_issue_count)),
        ("observedRepresentativePackages", str(row.observed_representative_packages)),
        ("observedUniquePackages", str(row.observed_unique_packages)),
        ("failureKind", row.failure_kind),
        ("oracleReady", str(row.oracle_ready).lower()),
        ("dependencyDepth", str(row.dependency_depth)),
        ("capabilityId", row.capability_id),
    )
    return RankedCapability(row=row, priority_key=priority_key, priority_labels=priority_labels)


def rank_capabilities(rows: list[LedgerRow] | tuple[LedgerRow, ...]) -> tuple[RankedCapability, ...]:
    seen: set[str] = set()
    ranked: list[RankedCapability] = []
    for row in rows:
        _validate_row(row)
        if row.capability_id in seen:
            raise ValueError(f"duplicate ledger capability: {row.capability_id}")
        seen.add(row.capability_id)
        if row.planning_mode == "observation-only":
            continue
        if row.evidence_state in {"unknown", "historical", "verified", "blocked"}:
            continue
        ranked.append(_ranked(row))
    return tuple(sorted(ranked, key=lambda item: item.priority_key))


def select_ranked_capability(
    ranked: tuple[RankedCapability, ...],
    capability_id: str,
    reason: str | None = None,
) -> tuple[RankedCapability, dict[str, Any]]:
    for index, item in enumerate(ranked):
        if item.capability_id != capability_id:
            continue
        rank = index + 1
        normalized_reason = reason.strip() if isinstance(reason, str) else ""
        if rank > 1 and not normalized_reason:
            raise ValueError(
                f"non-top capability selection requires a selection reason: {capability_id}"
            )
        return item, {
            "rank": rank,
            "topRanked": rank == 1,
            "reason": normalized_reason or "top-ranked-capability",
        }
    raise ValueError(f"capability is not present in executable ranking: {capability_id}")


def ledger_row_to_dict(row: LedgerRow) -> dict[str, Any]:
    return {
        "capabilityId": row.capability_id,
        "impact": row.impact,
        "currentIssueCount": row.current_issue_count,
        "observedRepresentativePackages": row.observed_representative_packages,
        "observedUniquePackages": row.observed_unique_packages,
        "failureKind": row.failure_kind,
        "oracleReady": row.oracle_ready,
        "dependencyDepth": row.dependency_depth,
        "evidenceState": row.evidence_state,
        "renderMode": row.render_mode,
        "blockers": list(row.blockers),
        "issueUrls": list(row.issue_urls),
        "planningMode": row.planning_mode,
    }


def ledger_row_from_dict(value: Mapping[str, Any]) -> LedgerRow:
    try:
        observed_unique_packages = int(value["observedUniquePackages"])
        row = LedgerRow(
            capability_id=str(value["capabilityId"]),
            impact=str(value["impact"]),
            current_issue_count=int(value["currentIssueCount"]),
            observed_unique_packages=observed_unique_packages,
            observed_representative_packages=int(
                value.get("observedRepresentativePackages", observed_unique_packages)
            ),
            failure_kind=str(value["failureKind"]),
            oracle_ready=value["oracleReady"] is True,
            dependency_depth=int(value["dependencyDepth"]),
            evidence_state=str(value["evidenceState"]),
            render_mode=str(value["renderMode"]),
            blockers=tuple(str(item) for item in value.get("blockers", [])),
            issue_urls=tuple(str(item) for item in value.get("issueUrls", [])),
            planning_mode=str(value.get("planningMode", "ranked")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid capability ledger row") from error
    _validate_row(row)
    return row


def ranked_capability_to_dict(item: RankedCapability) -> dict[str, Any]:
    return {
        "capabilityId": item.capability_id,
        "priority": {name: value for name, value in item.priority_labels},
        "row": ledger_row_to_dict(item.row),
    }


def _issue_current_reproduction_count(
    capability: CapabilityDefinition,
    issues: Sequence[Mapping[str, Any]],
) -> int:
    linked_urls = set(capability.issue_urls)
    return sum(
        1
        for issue in issues
        if issue.get("url") in linked_urls and issue.get("currentReproduction") is True
    )


def _oracle_rows_for(
    capability_id: str,
    oracle_report: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    if oracle_report is None:
        return ()
    values = oracle_report.get("results")
    if not isinstance(values, list):
        return ()
    rows: list[Mapping[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        ids = value.get("capabilityIds")
        if value.get("capabilityId") == capability_id or (
            isinstance(ids, list) and capability_id in ids
        ):
            rows.append(value)
    return tuple(rows)


def _oracle_failure_kind(rows: tuple[Mapping[str, Any], ...]) -> str:
    if any(row.get("error") or row.get("evaluation_errors") for row in rows):
        return "runtime-error"
    if any(row.get("structuralPassed") is False for row in rows):
        return "deterministic-failure"
    if any(row.get("passed") is False for row in rows):
        return "native-failure"
    if any(row.get("needs_review") is True or row.get("needsReview") is True for row in rows):
        return "review-warning"
    return "none"


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _oracle_row_is_fresh(
    row: Mapping[str, Any],
    revision: Any,
    inventory_dirty: Any,
    inventory_hashes: set[str],
) -> bool:
    provenance = row.get("provenance")
    if not isinstance(provenance, Mapping):
        return False
    renderer = provenance.get("renderer")
    inputs = provenance.get("inputs")
    if not isinstance(renderer, Mapping) or not isinstance(inputs, Mapping):
        return False
    source = inputs.get("sourcePptx")
    ground_truth = inputs.get("groundTruth")
    source_sha256 = source.get("sha256") if isinstance(source, Mapping) else None
    ground_truth_sha256 = (
        ground_truth.get("combinedSha256") if isinstance(ground_truth, Mapping) else None
    )
    return (
        inventory_dirty is False
        and renderer.get("revision") == revision
        and renderer.get("dirty") is False
        and _is_sha256(source_sha256)
        and source_sha256 in inventory_hashes
        and _is_sha256(ground_truth_sha256)
    )


def build_ledger_rows(
    registry: CapabilityRegistry,
    inventory: Mapping[str, Any],
    *,
    issues: Sequence[Mapping[str, Any]] = (),
    oracle_report: Mapping[str, Any] | None = None,
    accepted_states: Mapping[str, str] | None = None,
) -> tuple[LedgerRow, ...]:
    packages = inventory.get("packages")
    if not isinstance(packages, list):
        raise ValueError("inventory packages must be a list")
    renderer = inventory.get("renderer")
    revision = renderer.get("revision") if isinstance(renderer, Mapping) else None
    inventory_dirty = renderer.get("dirty") if isinstance(renderer, Mapping) else None
    inventory_hashes = {
        package.get("sha256")
        for package in packages
        if isinstance(package, Mapping) and _is_sha256(package.get("sha256"))
    }
    accepted_states = accepted_states or {}
    package_roles: dict[str, str] = {}
    for package in packages:
        if not isinstance(package, Mapping):
            continue
        package_id = package.get("packageId") or package.get("sha256")
        role = package.get("corpusRole", "representative")
        if role not in {"representative", "validation"}:
            raise ValueError(f"unsupported corpus role: {role}")
        package_roles[str(package_id)] = role
    rows: list[LedgerRow] = []
    for capability in registry.capabilities:
        observed = sum(
            1
            for package in packages
            if isinstance(package, Mapping)
            and isinstance(package.get("capabilityIds"), list)
            and capability.id in package["capabilityIds"]
        )
        observed_representative = sum(
            1
            for package in packages
            if isinstance(package, Mapping)
            and isinstance(package.get("capabilityIds"), list)
            and capability.id in package["capabilityIds"]
            and package_roles.get(
                str(package.get("packageId") or package.get("sha256")),
                "representative",
            )
            == "representative"
        )
        oracle_rows = _oracle_rows_for(capability.id, oracle_report)
        fresh_rows = tuple(
            row
            for row in oracle_rows
            if _oracle_row_is_fresh(row, revision, inventory_dirty, inventory_hashes)
        )
        blockers: list[str] = []
        accepted_state = accepted_states.get(capability.id)
        if accepted_state is not None and accepted_state not in EVIDENCE_STATES:
            raise ValueError(
                f"unsupported accepted evidence state for {capability.id}: {accepted_state}"
            )
        if oracle_report is not None and oracle_rows and not fresh_rows:
            blockers.append("oracle-report:stale")
        elif (
            "native-powerpoint" in capability.required_gates
            and not oracle_rows
            and accepted_state != "verified"
        ):
            blockers.append("oracle-report:missing-or-unmapped")
        oracle_ready = bool(fresh_rows)
        failure_kind = _oracle_failure_kind(fresh_rows)
        if accepted_state is not None:
            evidence_state = accepted_state
            if accepted_state == "regressed":
                blockers.append("acceptance:regressed")
        else:
            evidence_state = "reproducible" if oracle_ready else "observed" if observed else "unknown"
        if accepted_state == "verified" and failure_kind != "none":
            evidence_state = "regressed"
            blockers.append("accepted-capability:fresh-failure")
        rows.append(
            LedgerRow(
                capability_id=capability.id,
                impact=capability.impact,
                current_issue_count=_issue_current_reproduction_count(capability, issues),
                observed_unique_packages=observed,
                observed_representative_packages=observed_representative,
                failure_kind=failure_kind,
                oracle_ready=oracle_ready,
                dependency_depth=0,
                evidence_state=evidence_state,
                render_mode=capability.render_mode,
                blockers=tuple(blockers),
                issue_urls=capability.issue_urls,
                planning_mode=capability.planning_mode,
            )
        )
    return tuple(rows)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _number_values(value: Any) -> tuple[float | int, ...]:
    if not isinstance(value, tuple):
        return ()
    return tuple(item for item in value if isinstance(item, (int, float)) and not isinstance(item, bool))


def _case_matrix(capability: CapabilityDefinition) -> dict[str, list[Any]]:
    matrix: dict[str, list[Any]] = {
        "semantics": ["positive-default", "explicit-override", "inverse-opt-out"],
        "rendering": ["source-model", "dom-svg", "browser", "native-powerpoint"],
        "regression": ["target-cohort", "neighbor-sentinels"],
    }
    if capability.component in {"shape", "chart", "text"}:
        matrix["aspectRatio"] = ["square", "wide", "tall"]
    scope = capability.scope
    bounds = _number_values(scope.get("bounds"))
    defaults = _number_values(scope.get("default"))
    if len(bounds) == 2:
        matrix["adjustment"] = list(dict.fromkeys((bounds[0], *defaults, bounds[1])))
    if capability.component == "shape":
        matrix["container"] = ["standalone", "grouped", "placeholder-parent"]
        matrix["paint"] = ["solid", "theme-style-reference"]
    if capability.id == "drawingml.shape.geometry.adjustment.donut":
        matrix["container"] = ["standalone", "grouped", "picture-clip-sentinel"]
    if capability.id == "drawingml.shape.effect.outer-shadow":
        matrix["caseId"] = [
            "no-shadow-inverse",
            "rect-blur-defaults",
            "wide-roundrect-common-offset",
            "tall-ellipse-directional-offset",
            "rect-uniform-scale-102",
            "rect-uniform-scale-92-top-right",
            "grouped-roundrect",
            "scheme-color-modifiers",
        ]
        matrix["container"] = ["standalone", "grouped"]
        matrix["geometry"] = ["rect", "roundRect", "ellipse"]
        matrix["paint"] = ["solid", "simple-gradient", "scheme-color-modifiers"]
    if capability.id == "drawingml.shape.effect.reflection":
        matrix["caseId"] = [
            "no-reflection-inverse",
            "rect-common-alpha-fade",
            "wide-roundrect-common-alpha-fade",
            "tall-gradient-ellipse",
            "wide-gradient-arrow-broad-blur",
            "grouped-roundrect",
            "rect-distance-neighbor",
        ]
        matrix["container"] = ["standalone", "grouped"]
        matrix["geometry"] = ["rect", "roundRect", "ellipse", "upArrow"]
        matrix["paint"] = ["solid", "simple-gradient"]
    if capability.id == "presentation.animation.entrance.fade":
        matrix["caseId"] = [
            "shape-click-fade-500ms",
            "shape-with-effect-fade-500ms",
            "shape-after-effect-excluded",
            "picture-target-excluded",
            "group-target-excluded",
            "paragraph-range-excluded",
            "non-fade-inverse",
        ]
        matrix["trigger"] = ["clickEffect", "withEffect"]
        matrix["timelineSample"] = [
            "initial-hidden",
            "midpoint-opacity",
            "completed-visible",
            "replay-reset",
        ]
    if ".3d." in capability.id:
        matrix["operations"] = ["malformed-input", "resource-bounds", "deterministic-disposal"]
    return {key: matrix[key] for key in sorted(matrix)}


def _commands_for(capability: CapabilityDefinition) -> list[str]:
    commands = ["pnpm capability:check"]
    gates = set(capability.required_gates)
    if "unit" in gates or "structural" in gates:
        commands.append("pnpm test")
    if "browser" in gates:
        commands.append("pnpm test:browser")
    if "typecheck" in gates or capability.component in {"shape", "chart", "text"}:
        commands.append("pnpm typecheck")
    if "package-size" in gates:
        commands.append("pnpm size")
    if "native-powerpoint" in gates:
        commands.append("Run the scoped native PowerPoint oracle with matching input hashes")
    if "manual-visual" in gates:
        commands.append("Review every needs-review row and localized diff image")
    return commands


def build_work_packet(
    selected: RankedCapability,
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    capability = registry.by_id().get(selected.capability_id)
    if capability is None:
        raise ValueError(f"selected capability is not registered: {selected.capability_id}")
    if capability.planning_mode != "ranked":
        raise ValueError(
            f"capability is observation-only and cannot produce a work packet: {capability.id}"
        )
    row = selected.row
    return {
        "schemaVersion": 1,
        "capabilityId": capability.id,
        "component": capability.component,
        "currentRenderMode": row.render_mode,
        "evidenceState": row.evidence_state,
        "acceptedScope": _thaw(capability.scope),
        "fallback": capability.fallback,
        "observations": {
            "currentIssueCount": row.current_issue_count,
            "observedRepresentativePackages": row.observed_representative_packages,
            "observedUniquePackages": row.observed_unique_packages,
            "failureKind": row.failure_kind,
            "oracleReady": row.oracle_ready,
            "dependencyDepth": row.dependency_depth,
        },
        "priority": {name: value for name, value in selected.priority_labels},
        "issueUrls": list(capability.issue_urls or row.issue_urls),
        "caseMatrix": _case_matrix(capability),
        "requiredGates": list(capability.required_gates),
        "requiredCommands": _commands_for(capability),
        "blockers": list(row.blockers),
        "rollbackConditions": [
            "native-oracle-cannot-reproduce-feature",
            "source-semantics-do-not-support-assumption",
            "accepted-neighbor-regresses",
            "undeclared-fallback-or-silent-content-loss",
            "resource-or-package-budget-regresses",
            "only-stale-or-aggregate-metrics-improve",
        ],
        "documentationPaths": [
            "README.md",
            "CHANGELOG.md",
            "docs/ARCHITECTURE.md",
            "docs/TESTING.md",
            "test/e2e/oracle/README.md",
            "test/e2e/oracle/capabilities.json",
        ],
    }
