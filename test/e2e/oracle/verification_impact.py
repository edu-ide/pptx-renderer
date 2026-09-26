from __future__ import annotations

import fnmatch
import glob
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any


LOCAL_VISUAL_GATES = frozenset(
    {
        "bevel-local",
        "camera-local",
        "shadow-local",
        "reflection-local",
    }
)
LOCAL_GATE_SCRIPTS = {
    "bevel-local": "test/e2e/scripts/shape3d_bevel_metrics.py",
    "camera-local": "test/e2e/scripts/shape3d_camera_metrics.py",
    "shadow-local": "test/e2e/scripts/outer_shadow_metrics.py",
    "reflection-local": "test/e2e/scripts/reflection_metrics.py",
}
SELF_TEST_MAP = {
    "test/e2e/oracle/verification_impact.py": "test/e2e/test_verification_impact.py",
    "test/e2e/scripts/verify_affected.py": "test/e2e/test_verification_impact.py",
    "test/e2e/scripts/generate_pypptx_cases.py": "test/e2e/test_pypptx_generator_cases.py",
}
DOCUMENTATION_CONTRACT_TESTS = frozenset(
    {"test/unit/build/browserDistribution.test.ts"}
)
CAPABILITY_REGISTRY_PATH = "test/e2e/oracle/capabilities.json"
CAPABILITY_ACCEPTANCE_PATH = "test/e2e/oracle/capability-acceptance.json"
EVIDENCE_CONTROL_PATHS = frozenset(
    {
        "test/e2e/conftest.py",
        "test/e2e/extract_ground_truth.py",
        "test/e2e/server.py",
        "test/e2e/test_visual.py",
        "test/e2e/testdata_paths.py",
        "test/e2e/oracle/capability_contract.py",
        "test/e2e/oracle/capability_evidence.py",
        "test/e2e/oracle/capability_verification.py",
        "test/e2e/oracle/chart_metrics.py",
        "test/e2e/oracle/metrics.py",
        "test/e2e/oracle/powerpoint_oracle.py",
        "test/e2e/oracle/provenance.py",
        "test/e2e/scripts/run_all_shapes_eval.py",
        "test/e2e/scripts/run_capability_loop.py",
    }
)


def _value(item: Any, mapping_key: str, attribute: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(mapping_key)
    return getattr(item, attribute)


def _command(*argv: str, cwd: str = ".") -> dict[str, Any]:
    return {"argv": list(argv), "cwd": cwd}


def _normalized_paths(paths: Iterable[str]) -> list[str]:
    normalized: set[str] = set()
    for raw in paths:
        value = str(raw).replace("\\", "/")
        if not value:
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"changed path must be repository-relative: {raw}")
        normalized.add(path.as_posix())
    return sorted(normalized)


def _is_documentation(path: str) -> bool:
    return path.lower().endswith(".md")


def _is_runtime_path(path: str) -> bool:
    return (
        path.startswith("src/")
        or path.startswith("test/pages/")
        or path == "test/e2e/server.py"
        or path == "package.json"
        or path == "pnpm-lock.yaml"
        or path.startswith("vite.config")
    )


def _is_global_runtime_control(path: str) -> bool:
    return (
        path in {"package.json", "pnpm-lock.yaml", "scripts/build.mjs"}
        or path.startswith("tsconfig")
        or path.startswith("vite.config")
    )


def _is_evidence_control(path: str) -> bool:
    return path in EVIDENCE_CONTROL_PATHS


def _is_unit_test(path: str) -> bool:
    return path.startswith("test/unit/") and path.endswith((".test.ts", ".spec.ts"))


def _is_python_test(path: str) -> bool:
    return path.startswith("test/e2e/test_") and path.endswith(".py")


def _is_browser_test(path: str) -> bool:
    return path.startswith("test/browser/") and path.endswith((".test.ts", ".spec.ts"))


def _is_documentation_contract_test(path: str) -> bool:
    return (
        path in DOCUMENTATION_CONTRACT_TESTS
        or path.startswith("test/unit/docs/")
        and path.endswith((".test.ts", ".spec.ts"))
    )


def _capability_fields(
    capability: Any,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    capability_id = str(_value(capability, "id", "id"))
    affected_paths = tuple(
        str(path)
        for path in _value(capability, "affectedPaths", "affected_paths")
    )
    required_gates = tuple(
        str(gate) for gate in _value(capability, "requiredGates", "required_gates")
    )
    verification_cases = tuple(
        str(case_id)
        for case_id in (_value(capability, "verificationCases", "verification_cases") or ())
    )
    return capability_id, affected_paths, required_gates, verification_cases


def _commands_for_targeted_plan(
    *,
    unit_tests: Sequence[str],
    python_tests: Sequence[str],
    changed_paths: Sequence[str],
    python_executable: str,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if unit_tests:
        commands.append(_command("pnpm", "exec", "vitest", "run", *unit_tests))
    if python_tests:
        e2e_relative_tests = [path.removeprefix("test/e2e/") for path in python_tests]
        commands.append(
            _command(
                python_executable,
                "-m",
                "pytest",
                *e2e_relative_tests,
                "-q",
                cwd="test/e2e",
            )
        )
    if any(
        path.startswith("src/") and path.endswith((".ts", ".tsx"))
        for path in changed_paths
    ):
        commands.append(_command("pnpm", "typecheck"))
    return commands


def _artifact_candidates(value: Any) -> set[tuple[str, str]]:
    if (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) for item in value)
    ):
        return {value}
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return set()
    candidates: set[tuple[str, str]] = set()
    for item in value:
        if (
            isinstance(item, (tuple, list))
            and len(item) == 2
            and all(isinstance(part, str) for part in item)
        ):
            candidates.add((item[0], item[1]))
    return candidates


def build_verification_plan(
    *,
    changed_paths: Iterable[str],
    capabilities: Sequence[Any],
    latest_case_ids: Mapping[str, Sequence[str]],
    repository_paths: Iterable[str] = (),
    available_native_case_ids: Iterable[str] | None = None,
    latest_case_hashes: Mapping[
        str, Mapping[str, tuple[str, str]]
    ] | None = None,
    available_native_case_hashes: Mapping[str, Any] | None = None,
    case_reports: Mapping[str, str] | None = None,
    python_executable: str | None = None,
    release_metadata_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a fail-closed verification plan for the current change.

    Fast deterministic commands run in the edit loop. Browser, native PowerPoint,
    and local visual gates remain explicit obligations for the final evidence pass.
    """

    changed = _normalized_paths(changed_paths)
    release_metadata = _normalized_paths(release_metadata_paths)
    unknown_release_metadata = sorted(set(release_metadata).difference(changed))
    if unknown_release_metadata:
        raise ValueError(
            "release metadata must also be a changed path: "
            + ", ".join(unknown_release_metadata)
        )
    available_paths = _normalized_paths(repository_paths)
    python_command = python_executable or sys.executable
    available_native_cases = (
        None if available_native_case_ids is None else set(available_native_case_ids)
    )
    capability_fields = [_capability_fields(capability) for capability in capabilities]
    effective_case_ids = {
        capability_id: tuple(latest_case_ids.get(capability_id) or verification_cases)
        for capability_id, _, _, verification_cases in capability_fields
        if latest_case_ids.get(capability_id) or verification_cases
    }

    def empty_plan(
        mode: str, *, commands: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "fullReason": None,
            "changedPaths": changed,
            "releaseMetadataPaths": release_metadata,
            "impactedCapabilityIds": [],
            "unitTestPaths": [],
            "pythonTestPaths": [],
            "nativeScope": "none",
            "nativeCaseIds": [],
            "nativeCapabilitiesWithoutCases": [],
            "nativeCasesMissingArtifacts": [],
            "nativeCaseArtifactIssues": [],
            "localGates": [],
            "localGatesWithoutCaseReport": [],
            "localGateMissingCaseReports": {},
            "localGateCommands": [],
            "requiredGates": [],
            "requiresBrowser": False,
            "requiresNative": False,
            "unclassifiedPaths": [],
            "unmappedRuntimePaths": [],
            "commands": commands or [],
        }

    if not changed:
        return empty_plan("none")

    documentation_paths = [path for path in changed if _is_documentation(path)]
    documentation_tests = sorted(
        path for path in available_paths if _is_documentation_contract_test(path)
    )
    documentation_commands: list[dict[str, Any]] = []
    if documentation_paths:
        documentation_commands.append(
            _command("pnpm", "exec", "prettier", "--check", *documentation_paths)
        )
        if documentation_tests:
            documentation_commands.append(
                _command("pnpm", "exec", "vitest", "run", *documentation_tests)
            )

    release_commands: list[dict[str, Any]] = []
    if release_metadata:
        release_commands = [
            _command("pnpm", "exec", "prettier", "--check", *release_metadata),
            _command("pnpm", "build"),
            _command("pnpm", "test:package"),
            _command("pnpm", "publint"),
            _command("pnpm", "size"),
        ]

    behavior_changes = [
        path
        for path in changed
        if not _is_documentation(path) and path not in release_metadata
    ]
    if not behavior_changes:
        mode = "release-metadata" if release_metadata else "docs-only"
        plan = empty_plan(mode, commands=[*documentation_commands, *release_commands])
        plan["unitTestPaths"] = documentation_tests
        if documentation_paths:
            plan["requiredGates"].append("docs")
        if release_metadata:
            plan["requiredGates"].append("package")
        return plan

    direct_tests = {
        path
        for path in behavior_changes
        if _is_unit_test(path) or _is_python_test(path) or _is_browser_test(path)
    }
    self_test_sources = set(SELF_TEST_MAP).intersection(behavior_changes)
    capability_signal_paths = set(behavior_changes).difference(
        direct_tests, self_test_sources
    )
    claimed_paths: set[str] = set()
    impacted: list[
        tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]
    ] = []
    behavior_set = set(behavior_changes)
    for fields in capability_fields:
        _, affected_paths, _, _ = fields
        matching_paths = {
            changed_path
            for changed_path in capability_signal_paths
            if any(
                fnmatch.fnmatchcase(changed_path, affected_path)
                for affected_path in affected_paths
            )
        }
        if matching_paths:
            impacted.append(fields)
            claimed_paths.update(matching_paths)

    self_tests = {SELF_TEST_MAP[path] for path in self_test_sources}
    classified_paths = claimed_paths | direct_tests | self_test_sources
    unclassified = sorted(path for path in behavior_changes if path not in classified_paths)

    registry_changed = CAPABILITY_REGISTRY_PATH in behavior_set
    acceptance_changed = CAPABILITY_ACCEPTANCE_PATH in behavior_set
    evidence_control_changed = any(
        _is_evidence_control(path) for path in behavior_changes
    )
    global_runtime_control_changed = any(
        _is_global_runtime_control(path) for path in behavior_changes
    )
    global_control_changed = (
        evidence_control_changed or global_runtime_control_changed
    )
    unclaimed_runtime_changed = any(_is_runtime_path(path) for path in unclassified)
    force_full = (
        bool(unclassified)
        or registry_changed
        or acceptance_changed
        or global_control_changed
    )
    all_capabilities_scope = (
        registry_changed or global_control_changed or unclaimed_runtime_changed
    )
    if all_capabilities_scope:
        impacted = list(capability_fields)

    if registry_changed:
        full_reason = "capability-contract-change"
    elif acceptance_changed:
        full_reason = "capability-acceptance-change"
    elif evidence_control_changed:
        full_reason = "evidence-control-change"
    elif global_runtime_control_changed:
        full_reason = "global-runtime-control-change"
    elif unclassified and all(_is_runtime_path(path) for path in unclassified):
        full_reason = "unmapped-runtime-path"
    elif unclassified:
        full_reason = "unclassified-change"
    else:
        full_reason = None

    impacted_ids = sorted(fields[0] for fields in impacted)
    affected_paths: set[str] = set()
    for _, patterns, _, _ in impacted:
        for pattern in patterns:
            if _is_documentation(pattern):
                continue
            if glob.has_magic(pattern):
                affected_paths.update(
                    path for path in available_paths if fnmatch.fnmatchcase(path, pattern)
                )
            else:
                affected_paths.add(pattern)
    unit_tests = sorted(
        {
            path
            for path in (*behavior_changes, *affected_paths, *self_tests)
            if _is_unit_test(path)
        }
    )
    python_tests = sorted(
        {
            path
            for path in (*behavior_changes, *affected_paths, *self_tests)
            if _is_python_test(path)
        }
    )
    required_gates = sorted({gate for _, _, gates, _ in impacted for gate in gates})
    if any(_is_browser_test(path) for path in direct_tests):
        required_gates = sorted(set(required_gates).union({"browser"}))
    if force_full:
        required_gates = sorted(
            set(required_gates).union({"unit", "regression", "typecheck", "browser"})
        )
    native_cases = sorted(
        {
            case_id
            for capability_id in impacted_ids
            for case_id in effective_case_ids.get(capability_id, ())
        }
    )
    requires_native = "native-powerpoint" in required_gates or bool(
        LOCAL_VISUAL_GATES.intersection(required_gates)
    )
    native_capabilities_without_cases = sorted(
        capability_id
        for capability_id, _, gates, _ in impacted
        if (
            "native-powerpoint" in gates or bool(LOCAL_VISUAL_GATES.intersection(gates))
        )
        and not effective_case_ids.get(capability_id)
    )

    artifact_issues: list[dict[str, str]] = []
    for capability_id, _, gates, _ in impacted:
        if not (
            "native-powerpoint" in gates or bool(LOCAL_VISUAL_GATES.intersection(gates))
        ):
            continue
        for case_id in effective_case_ids.get(capability_id, ()):
            reason: str | None = None
            accepted_hashes = (
                latest_case_hashes.get(capability_id)
                if latest_case_hashes is not None
                else None
            )
            if accepted_hashes is not None:
                expected = accepted_hashes.get(case_id)
                candidates = _artifact_candidates(
                    (available_native_case_hashes or {}).get(case_id)
                )
                if expected is None:
                    reason = "recorded-input-hash-missing"
                elif not candidates:
                    reason = "missing-artifact"
                elif tuple(expected) not in candidates:
                    reason = "input-hash-mismatch"
            elif available_native_case_hashes is not None and not _artifact_candidates(
                available_native_case_hashes.get(case_id)
            ):
                reason = "missing-artifact"
            elif available_native_cases is not None and case_id not in available_native_cases:
                reason = "missing-artifact"
            if reason is not None:
                artifact_issues.append(
                    {
                        "capabilityId": capability_id,
                        "caseId": case_id,
                        "reason": reason,
                    }
                )
    artifact_issues.sort(
        key=lambda issue: (issue["caseId"], issue["capabilityId"], issue["reason"])
    )
    native_cases_missing_artifacts = sorted(
        {issue["caseId"] for issue in artifact_issues}
    )

    local_gates = sorted(LOCAL_VISUAL_GATES.intersection(required_gates))
    local_gate_commands: list[dict[str, Any]] = []
    local_gates_without_case_report: list[str] = []
    local_gate_missing_case_reports: dict[str, list[str]] = {}
    if local_gates and case_reports:
        for gate in local_gates:
            expected_cases = sorted(
                {
                    case_id
                    for capability_id, _, gates, _ in impacted
                    if gate in gates
                    for case_id in effective_case_ids.get(capability_id, ())
                }
            )
            missing_cases = sorted(
                case_id
                for case_id in expected_cases
                if case_id not in case_reports or case_id in native_cases_missing_artifacts
            )
            if missing_cases:
                local_gate_missing_case_reports[gate] = missing_cases
                continue
            if not expected_cases:
                continue
            argv = [python_command, LOCAL_GATE_SCRIPTS[gate]]
            for case_id in expected_cases:
                argv.extend(["--case-report", case_reports[case_id]])
            argv.extend(
                [
                    "--out",
                    f"test/e2e/reports/capability-loop/affected-{gate}.json",
                ]
            )
            local_gate_commands.append(_command(*argv))
    elif local_gates:
        local_gates_without_case_report = local_gates

    if force_full:
        commands = [
            *documentation_commands,
            _command("pnpm", "test"),
            _command(
                python_command,
                "-m",
                "pytest",
                ".",
                "-q",
                cwd="test/e2e",
            ),
            _command("pnpm", "typecheck"),
            _command("pnpm", "test:browser"),
            *release_commands,
        ]
        if registry_changed or acceptance_changed or evidence_control_changed:
            commands.append(_command("pnpm", "capability:check"))
        mode = "full"
        requires_browser = True
    else:
        commands = [
            *documentation_commands,
            *_commands_for_targeted_plan(
                unit_tests=unit_tests,
                python_tests=python_tests,
                changed_paths=behavior_changes,
                python_executable=python_command,
            ),
            *release_commands,
        ]
        mode = "targeted"
        requires_browser = "browser" in required_gates

    if requires_native:
        native_scope = (
            "all-capabilities" if all_capabilities_scope else "impacted-capabilities"
        )
    else:
        native_scope = "none"

    return {
        "mode": mode,
        "fullReason": full_reason,
        "changedPaths": changed,
        "releaseMetadataPaths": release_metadata,
        "impactedCapabilityIds": impacted_ids,
        "unitTestPaths": sorted(set(unit_tests).union(documentation_tests)),
        "pythonTestPaths": ["test/e2e"] if force_full else python_tests,
        "nativeScope": native_scope,
        "nativeCaseIds": native_cases if requires_native else [],
        "nativeCapabilitiesWithoutCases": native_capabilities_without_cases,
        "nativeCasesMissingArtifacts": native_cases_missing_artifacts,
        "nativeCaseArtifactIssues": artifact_issues,
        "localGates": local_gates,
        "localGatesWithoutCaseReport": local_gates_without_case_report,
        "localGateMissingCaseReports": local_gate_missing_case_reports,
        "localGateCommands": local_gate_commands,
        "requiredGates": required_gates,
        "requiresBrowser": requires_browser,
        "requiresNative": requires_native,
        "unclassifiedPaths": unclassified,
        "unmappedRuntimePaths": unclassified,
        "commands": commands,
    }
