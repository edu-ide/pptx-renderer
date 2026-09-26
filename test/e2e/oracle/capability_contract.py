from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse


SCHEMA_VERSION = 2
RENDER_MODES = frozenset({"none", "fallback", "approximate", "native", "excluded"})
PLANNING_MODES = frozenset({"ranked", "observation-only"})
IMPACTS = frozenset(
    {"security", "crash", "missing", "semantics", "reliability", "fidelity", "enhancement"}
)
GATES = frozenset(
    {
        "source",
        "structural",
        "unit",
        "browser",
        "native-powerpoint",
        "manual-visual",
        "regression",
        "security",
        "performance",
        "package",
        "package-size",
        "typecheck",
        "lint",
        "build",
        "docs",
        "bevel-local",
        "camera-local",
        "shadow-local",
        "reflection-local",
    }
)

_CAPABILITY_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_HEX_40_TO_64 = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class XmlAncestorStep:
    """One namespace/name constraint in a root-to-parent XML ancestor path."""

    namespace: str
    local_names: tuple[str, ...]


@dataclass(frozen=True)
class XmlSelector:
    """One element selector with optional parent or ancestor-path constraints."""

    part_glob: str
    namespace: str
    local_name: str
    parent_namespace: str | None
    parent_local_names: tuple[str, ...]
    ancestor_path: tuple[XmlAncestorStep, ...]
    attributes: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CapabilityDefinition:
    id: str
    component: str
    render_mode: str
    impact: str
    selectors: tuple[XmlSelector, ...]
    scope: Mapping[str, Any]
    fallback: str
    affected_paths: tuple[str, ...]
    required_gates: tuple[str, ...]
    issue_urls: tuple[str, ...]
    verification_cases: tuple[str, ...]
    planning_mode: str


@dataclass(frozen=True)
class CapabilityRegistry:
    schema_version: int
    capabilities: tuple[CapabilityDefinition, ...]

    def by_id(self) -> Mapping[str, CapabilityDefinition]:
        return MappingProxyType({capability.id: capability for capability in self.capabilities})


@dataclass(frozen=True)
class PromotionReceipt:
    capability_id: str
    accepted_revision: str
    case_ids: tuple[str, ...]
    case_input_sha256: tuple[str, ...]
    ground_truth_sha256: tuple[str, ...]
    gates: tuple[str, ...]
    environment: Mapping[str, Any]
    accepted_at: str


@dataclass(frozen=True)
class AcceptanceHistory:
    schema_version: int
    receipts: tuple[PromotionReceipt, ...]


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read JSON contract: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON contract: {path}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON contract must be an object: {path}")
    return payload


def _check_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
) -> None:
    optional = optional or set()
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise ValueError(f"{label} missing keys: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown keys: {', '.join(unknown)}")


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _unique_strings(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise ValueError(f"{label} must be {qualifier} of strings")
    result = tuple(_nonempty_string(item, label) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicates")
    return result


def _repository_relative_path(value: Any, label: str) -> str:
    text = _nonempty_string(value, label)
    if "\\" in text:
        raise ValueError(f"{label} must be a repository-relative POSIX path")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{label} must be a repository-relative POSIX path")
    return path.as_posix()


def _freeze_json(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return tuple(_freeze_json(item, label) for item in value)
    if isinstance(value, dict):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{label} object keys must be non-empty strings")
            frozen[key] = _freeze_json(item, label)
        return MappingProxyType(frozen)
    raise ValueError(f"{label} contains a non-JSON value")


def _parse_selector(value: Any, index: int, capability_id: str) -> XmlSelector:
    label = f"capability {capability_id} selector {index}"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _check_keys(
        value,
        required={"partGlob", "namespace", "localName"},
        optional={"ancestorPath", "attributes", "parent"},
        label=label,
    )
    if "parent" in value and "ancestorPath" in value:
        raise ValueError(f"{label} cannot combine parent and ancestorPath")
    parent_namespace: str | None = None
    parent_local_names: tuple[str, ...] = ()
    if "parent" in value:
        parent_value = value["parent"]
        if not isinstance(parent_value, dict):
            raise ValueError(f"{label} parent must be an object")
        _check_keys(
            parent_value,
            required={"namespace", "localNames"},
            label=f"{label} parent",
        )
        parent_namespace = _nonempty_string(
            parent_value["namespace"],
            f"{label} parent namespace",
        )
        parent_local_names = _unique_strings(
            parent_value["localNames"],
            f"{label} parent localNames",
        )
    ancestor_path: tuple[XmlAncestorStep, ...] = ()
    if "ancestorPath" in value:
        ancestor_path_value = value["ancestorPath"]
        if not isinstance(ancestor_path_value, list) or not ancestor_path_value:
            raise ValueError(f"{label} ancestorPath must be a non-empty list")
        steps: list[XmlAncestorStep] = []
        for step_index, step_value in enumerate(ancestor_path_value):
            step_label = f"{label} ancestorPath step {step_index}"
            if not isinstance(step_value, dict):
                raise ValueError(f"{step_label} must be an object")
            _check_keys(
                step_value,
                required={"namespace", "localNames"},
                label=step_label,
            )
            steps.append(
                XmlAncestorStep(
                    namespace=_nonempty_string(
                        step_value["namespace"],
                        f"{step_label} namespace",
                    ),
                    local_names=_unique_strings(
                        step_value["localNames"],
                        f"{step_label} localNames",
                    ),
                )
            )
        ancestor_path = tuple(steps)
    attributes_value = value.get("attributes", {})
    if not isinstance(attributes_value, dict):
        raise ValueError(f"{label} attributes must be an object")
    attributes: dict[str, tuple[str, ...]] = {}
    for name, accepted_values in sorted(attributes_value.items()):
        attribute_name = _nonempty_string(name, f"{label} attribute name")
        attributes[attribute_name] = _unique_strings(
            accepted_values,
            f"{label} attribute {attribute_name}",
        )
    return XmlSelector(
        part_glob=_repository_relative_path(value["partGlob"], f"{label} partGlob"),
        namespace=_nonempty_string(value["namespace"], f"{label} namespace"),
        local_name=_nonempty_string(value["localName"], f"{label} localName"),
        parent_namespace=parent_namespace,
        parent_local_names=parent_local_names,
        ancestor_path=ancestor_path,
        attributes=MappingProxyType(attributes),
    )


def _parse_capability(value: Any, index: int) -> CapabilityDefinition:
    label = f"capability {index}"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _check_keys(
        value,
        required={
            "id",
            "component",
            "renderMode",
            "impact",
            "selectors",
            "scope",
            "fallback",
            "affectedPaths",
            "requiredGates",
            "issueUrls",
        },
        optional={"planningMode", "verificationCases"},
        label=label,
    )
    capability_id = _nonempty_string(value["id"], f"{label} id")
    if not _CAPABILITY_ID.fullmatch(capability_id):
        raise ValueError(f"{label} id is invalid: {capability_id}")
    render_mode = _nonempty_string(value["renderMode"], f"capability {capability_id} renderMode")
    if render_mode not in RENDER_MODES:
        raise ValueError(f"capability {capability_id} has unsupported renderMode: {render_mode}")
    impact = _nonempty_string(value["impact"], f"capability {capability_id} impact")
    if impact not in IMPACTS:
        raise ValueError(f"capability {capability_id} has unsupported impact: {impact}")
    planning_mode = _nonempty_string(
        value.get("planningMode", "ranked"),
        f"capability {capability_id} planningMode",
    )
    if planning_mode not in PLANNING_MODES:
        raise ValueError(
            f"capability {capability_id} has unsupported planningMode: {planning_mode}"
        )
    selectors_value = value["selectors"]
    if not isinstance(selectors_value, list):
        raise ValueError(f"capability {capability_id} selectors must be a list")
    selectors = tuple(
        _parse_selector(selector, selector_index, capability_id)
        for selector_index, selector in enumerate(selectors_value)
    )
    if not isinstance(value["scope"], dict):
        raise ValueError(f"capability {capability_id} scope must be an object")
    scope = _freeze_json(value["scope"], f"capability {capability_id} scope")
    fallback = value["fallback"]
    if not isinstance(fallback, str):
        raise ValueError(f"capability {capability_id} fallback must be a string")
    fallback = fallback.strip()
    if render_mode != "native" and not fallback:
        raise ValueError(f"capability {capability_id} fallback must describe non-native behavior")
    if render_mode == "native" and not scope:
        raise ValueError(f"capability {capability_id} native scope must not be empty")
    paths = _unique_strings(
        value["affectedPaths"],
        f"capability {capability_id} affectedPaths",
    )
    affected_paths = tuple(
        _repository_relative_path(path, f"capability {capability_id} affected path")
        for path in paths
    )
    required_gates = _unique_strings(
        value["requiredGates"],
        f"capability {capability_id} requiredGates",
    )
    unknown_gates = sorted(set(required_gates) - GATES)
    if unknown_gates:
        raise ValueError(
            f"capability {capability_id} has unsupported gates: {', '.join(unknown_gates)}"
        )
    if render_mode == "native" and "native-powerpoint" not in required_gates:
        raise ValueError(f"capability {capability_id} native mode requires native-powerpoint gate")
    issue_urls = _unique_strings(
        value["issueUrls"],
        f"capability {capability_id} issueUrls",
        allow_empty=True,
    )
    verification_cases = _unique_strings(
        value.get("verificationCases", []),
        f"capability {capability_id} verificationCases",
        allow_empty=True,
    )
    for issue_url in issue_urls:
        parsed = urlparse(issue_url)
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            raise ValueError(f"capability {capability_id} issue URL must be a GitHub HTTPS URL")
    return CapabilityDefinition(
        id=capability_id,
        component=_nonempty_string(value["component"], f"capability {capability_id} component"),
        render_mode=render_mode,
        impact=impact,
        selectors=selectors,
        scope=scope,
        fallback=fallback,
        affected_paths=affected_paths,
        required_gates=required_gates,
        issue_urls=issue_urls,
        verification_cases=verification_cases,
        planning_mode=planning_mode,
    )


def load_capability_registry(path: Path) -> CapabilityRegistry:
    payload = _load_object(path)
    _check_keys(payload, required={"schemaVersion", "capabilities"}, label="capability registry")
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"capability registry requires schemaVersion={SCHEMA_VERSION}")
    if not isinstance(payload["capabilities"], list):
        raise ValueError("capability registry capabilities must be a list")
    capabilities = tuple(
        _parse_capability(value, index) for index, value in enumerate(payload["capabilities"])
    )
    ids = [capability.id for capability in capabilities]
    duplicate_ids = sorted({capability_id for capability_id in ids if ids.count(capability_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate capability id: {', '.join(duplicate_ids)}")
    if ids != sorted(ids):
        raise ValueError("capability registry entries must be sorted by id")
    return CapabilityRegistry(schema_version=SCHEMA_VERSION, capabilities=capabilities)


def _parse_timestamp(value: Any, label: str) -> str:
    text = _nonempty_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return text


def _parse_receipt(value: Any, index: int) -> PromotionReceipt:
    label = f"verification record {index}"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _check_keys(
        value,
        required={
            "capabilityId",
            "acceptedRevision",
            "caseIds",
            "caseInputSha256",
            "groundTruthSha256",
            "gates",
            "environment",
            "acceptedAt",
        },
        label=label,
    )
    capability_id = _nonempty_string(value["capabilityId"], f"{label} capabilityId")
    accepted_revision = _nonempty_string(value["acceptedRevision"], f"{label} acceptedRevision")
    if not _HEX_40_TO_64.fullmatch(accepted_revision):
        raise ValueError(f"{label} acceptedRevision must be a lowercase Git object id")
    case_ids = _unique_strings(value["caseIds"], f"{label} caseIds")
    case_hashes = _unique_strings(
        value["caseInputSha256"], f"{label} caseInputSha256"
    )
    ground_truth_hashes = _unique_strings(
        value["groundTruthSha256"], f"{label} groundTruthSha256"
    )
    if len(case_ids) != len(case_hashes) or len(case_ids) != len(ground_truth_hashes):
        raise ValueError(
            f"{label} caseIds, caseInputSha256, and groundTruthSha256 "
            "must have equal length"
        )
    invalid_hashes = [item for item in case_hashes if not _SHA256.fullmatch(item)]
    if invalid_hashes:
        raise ValueError(f"{label} caseInputSha256 must contain lowercase SHA-256 values")
    invalid_ground_truth_hashes = [
        item for item in ground_truth_hashes if not _SHA256.fullmatch(item)
    ]
    if invalid_ground_truth_hashes:
        raise ValueError(f"{label} groundTruthSha256 must contain lowercase SHA-256 values")
    gates = _unique_strings(value["gates"], f"{label} gates")
    unknown_gates = sorted(set(gates) - GATES)
    if unknown_gates:
        raise ValueError(f"{label} has unsupported gates: {', '.join(unknown_gates)}")
    if not isinstance(value["environment"], dict) or not value["environment"]:
        raise ValueError(f"{label} environment must be a non-empty object")
    environment = _freeze_json(value["environment"], f"{label} environment")
    return PromotionReceipt(
        capability_id=capability_id,
        accepted_revision=accepted_revision,
        case_ids=case_ids,
        case_input_sha256=case_hashes,
        ground_truth_sha256=ground_truth_hashes,
        gates=gates,
        environment=environment,
        accepted_at=_parse_timestamp(value["acceptedAt"], f"{label} acceptedAt"),
    )


def load_acceptance_history(path: Path) -> AcceptanceHistory:
    payload = _load_object(path)
    _check_keys(payload, required={"schemaVersion", "receipts"}, label="acceptance history")
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"acceptance history requires schemaVersion={SCHEMA_VERSION}")
    if not isinstance(payload["receipts"], list):
        raise ValueError("acceptance history receipts must be a list")
    receipts = tuple(_parse_receipt(value, index) for index, value in enumerate(payload["receipts"]))
    ordering = [(receipt.capability_id, receipt.accepted_at) for receipt in receipts]
    if ordering != sorted(ordering):
        raise ValueError("acceptance receipts must be sorted by capabilityId and acceptedAt")
    if len(ordering) != len(set(ordering)):
        raise ValueError("acceptance history contains a duplicate receipt")
    capability_ids = [receipt.capability_id for receipt in receipts]
    if len(capability_ids) != len(set(capability_ids)):
        raise ValueError("acceptance history must contain only one receipt per capability")
    return AcceptanceHistory(schema_version=SCHEMA_VERSION, receipts=receipts)


def validate_acceptance_history(
    registry: CapabilityRegistry,
    history: AcceptanceHistory,
) -> None:
    capabilities = registry.by_id()
    for receipt in history.receipts:
        capability = capabilities.get(receipt.capability_id)
        if capability is None:
            raise ValueError(
                f"verification record references unknown capability: {receipt.capability_id}"
            )
