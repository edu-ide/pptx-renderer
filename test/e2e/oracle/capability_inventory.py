from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from oracle.capability_contract import CapabilityDefinition, CapabilityRegistry, XmlSelector


INVENTORY_SCHEMA_VERSION = 1


class CapabilityInventoryError(ValueError):
    def __init__(self, message: str, *, code: str = "scan-error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ScanLimits:
    max_entries: int = 4_000
    max_entry_uncompressed_bytes: int = 32 * 1024 * 1024
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in (
            ("max_entries", self.max_entries),
            ("max_entry_uncompressed_bytes", self.max_entry_uncompressed_bytes),
            ("max_total_uncompressed_bytes", self.max_total_uncompressed_bytes),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_SCAN_LIMITS = ScanLimits()


@dataclass(frozen=True)
class PackageObservation:
    package_id: str
    sha256: str
    aliases: tuple[str, ...]
    capability_ids: tuple[str, ...]
    matching_parts: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class RejectedPackage:
    package_id: str
    sha256: str | None
    aliases: tuple[str, ...]
    reason_code: str
    reason: str


@dataclass(frozen=True)
class InventoryReport:
    schema_version: int
    raw_package_count: int
    unique_package_count: int
    packages: tuple[PackageObservation, ...]
    rejected_package_count: int
    rejected_packages: tuple[RejectedPackage, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise CapabilityInventoryError(
            f"cannot read PPTX package: {path.name}", code="package-read"
        ) from error
    return digest.hexdigest()


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise CapabilityInventoryError(
            f"unsafe ZIP member path: {name!r}", code="unsafe-zip-member"
        )
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise CapabilityInventoryError(
            f"unsafe ZIP member path: {name}", code="unsafe-zip-member"
        )


def _selected_capabilities(
    part_name: str,
    registry: CapabilityRegistry,
) -> tuple[CapabilityDefinition, ...]:
    return tuple(
        capability
        for capability in registry.capabilities
        if any(fnmatch.fnmatchcase(part_name, selector.part_glob) for selector in capability.selectors)
    )


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return "", tag


def _attribute_value(attributes: dict[str, str], name: str) -> str | None:
    if name in attributes:
        return attributes[name]
    matches = [value for key, value in attributes.items() if _split_tag(key)[1] == name]
    return matches[0] if len(matches) == 1 else None


def _attribute_matches(actual: str | None, accepted: tuple[str, ...]) -> bool:
    for expected in accepted:
        if expected == "$present" and actual is not None:
            return True
        if expected == "$absent" and actual is None:
            return True
        if actual is None:
            continue
        if expected == actual:
            return True
        if expected in {"$nonzero", "$positive"}:
            try:
                numeric = float(actual)
            except ValueError:
                continue
            if math.isfinite(numeric) and (
                (expected == "$nonzero" and numeric != 0)
                or (expected == "$positive" and numeric > 0)
            ):
                return True
    return False


def _selector_matches(
    selector: XmlSelector,
    namespace: str,
    local_name: str,
    attributes: dict[str, str],
    ancestors: Sequence[tuple[str, str]],
) -> bool:
    if selector.namespace != namespace or selector.local_name != local_name:
        return False
    parent_namespace, parent_local_name = ancestors[-1] if ancestors else (None, None)
    if selector.parent_namespace is not None and (
        selector.parent_namespace != parent_namespace
        or parent_local_name not in selector.parent_local_names
    ):
        return False
    if selector.ancestor_path:
        if len(ancestors) < len(selector.ancestor_path):
            return False
        path = ancestors[-len(selector.ancestor_path) :]
        if any(
            step.namespace != ancestor_namespace
            or ancestor_local_name not in step.local_names
            for step, (ancestor_namespace, ancestor_local_name) in zip(
                selector.ancestor_path,
                path,
                strict=True,
            )
        ):
            return False
    return all(
        _attribute_matches(_attribute_value(attributes, name), accepted)
        for name, accepted in selector.attributes.items()
    )


def _scan_xml_part(
    part_name: str,
    data: bytes,
    capabilities: tuple[CapabilityDefinition, ...],
) -> set[str]:
    matches: set[str] = set()
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, flags=re.IGNORECASE):
        raise CapabilityInventoryError(
            f"DTD or entity declaration is not allowed in XML part: {part_name}",
            code="xml-dtd-entity",
        )
    try:
        ancestors: list[tuple[str, str]] = []
        events = ElementTree.iterparse(io.BytesIO(data), events=("start", "end"))
        for event, element in events:
            namespace, local_name = _split_tag(element.tag)
            if event == "start":
                attributes = dict(element.attrib)
                for capability in capabilities:
                    if any(
                        fnmatch.fnmatchcase(part_name, selector.part_glob)
                        and _selector_matches(
                            selector,
                            namespace,
                            local_name,
                            attributes,
                            ancestors,
                        )
                        for selector in capability.selectors
                    ):
                        matches.add(capability.id)
                ancestors.append((namespace, local_name))
            else:
                ancestors.pop()
                element.clear()
    except ElementTree.ParseError as error:
        raise CapabilityInventoryError(
            f"invalid XML part {part_name}: {error}", code="invalid-xml"
        ) from error
    return matches


def scan_pptx(
    path: Path,
    registry: CapabilityRegistry,
    limits: ScanLimits = DEFAULT_SCAN_LIMITS,
) -> PackageObservation:
    path = Path(path)
    sha256 = _sha256_file(path)
    matching_parts: dict[str, set[str]] = {}
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries:
                raise CapabilityInventoryError(
                    f"ZIP entry count {len(entries)} exceeds limit {limits.max_entries}",
                    code="zip-entry-count",
                )
            total_size = 0
            for entry in entries:
                _validate_member_name(entry.filename)
                if entry.file_size > limits.max_entry_uncompressed_bytes:
                    raise CapabilityInventoryError(
                        f"ZIP entry size {entry.file_size} exceeds limit "
                        f"{limits.max_entry_uncompressed_bytes}: {entry.filename}",
                        code="zip-entry-size",
                    )
                total_size += entry.file_size
                if total_size > limits.max_total_uncompressed_bytes:
                    raise CapabilityInventoryError(
                        f"ZIP decoded total {total_size} exceeds limit "
                        f"{limits.max_total_uncompressed_bytes}",
                        code="zip-total-size",
                    )

            for entry in sorted(entries, key=lambda item: item.filename):
                capabilities = _selected_capabilities(entry.filename, registry)
                if not capabilities:
                    continue
                with archive.open(entry) as stream:
                    data = stream.read(limits.max_entry_uncompressed_bytes + 1)
                if len(data) > limits.max_entry_uncompressed_bytes:
                    raise CapabilityInventoryError(
                        f"ZIP entry size exceeds limit while reading: {entry.filename}",
                        code="zip-entry-size",
                    )
                for capability_id in _scan_xml_part(entry.filename, data, capabilities):
                    matching_parts.setdefault(capability_id, set()).add(entry.filename)
    except BadZipFile as error:
        raise CapabilityInventoryError(
            f"invalid PPTX ZIP package: {path.name}", code="invalid-zip"
        ) from error
    except OSError as error:
        raise CapabilityInventoryError(
            f"cannot scan PPTX package: {path.name}", code="package-read"
        ) from error

    frozen_matches = {
        capability_id: tuple(sorted(parts))
        for capability_id, parts in sorted(matching_parts.items())
    }
    return PackageObservation(
        package_id=f"sha256:{sha256}",
        sha256=sha256,
        aliases=(),
        capability_ids=tuple(frozen_matches),
        matching_parts=frozen_matches,
    )


def _iter_packages(roots: Iterable[Path]) -> Iterable[tuple[str, Path]]:
    for root_index, root_value in enumerate(roots):
        root = Path(root_value)
        if root.is_file():
            if root.suffix.lower() == ".pptx":
                yield f"corpus-{root_index}/{root.name}", root
            continue
        if not root.is_dir():
            raise CapabilityInventoryError(
                f"corpus root does not exist: corpus-{root_index}", code="corpus-root-missing"
            )
        for path in sorted(root.rglob("*.pptx")):
            alias = f"corpus-{root_index}/{path.relative_to(root).as_posix()}"
            yield alias, path


def scan_corpus(
    roots: Iterable[Path],
    registry: CapabilityRegistry,
    limits: ScanLimits = DEFAULT_SCAN_LIMITS,
) -> InventoryReport:
    observations: dict[str, PackageObservation] = {}
    aliases_by_sha: dict[str, list[str]] = {}
    rejected: dict[str, tuple[str | None, str, str, list[str]]] = {}
    raw_package_count = 0
    rejected_package_count = 0
    for alias, path in _iter_packages(roots):
        raw_package_count += 1
        try:
            observation = scan_pptx(path, registry, limits)
        except CapabilityInventoryError as error:
            rejected_package_count += 1
            try:
                sha256 = _sha256_file(path)
            except CapabilityInventoryError:
                sha256 = None
            rejection_key = sha256 or f"alias:{alias}"
            if rejection_key not in rejected:
                rejected[rejection_key] = (sha256, error.code, str(error), [])
            rejected[rejection_key][3].append(alias)
            continue
        aliases_by_sha.setdefault(observation.sha256, []).append(alias)
        observations.setdefault(observation.sha256, observation)

    packages = tuple(
        replace(
            observations[sha256],
            aliases=tuple(sorted(aliases_by_sha[sha256])),
        )
        for sha256 in sorted(observations)
    )
    rejected_packages = tuple(
        RejectedPackage(
            package_id=f"sha256:{sha256}" if sha256 else rejection_key,
            sha256=sha256,
            aliases=tuple(sorted(values[3])),
            reason_code=values[1],
            reason=values[2],
        )
        for rejection_key, values in sorted(rejected.items())
        for sha256 in (values[0],)
    )
    return InventoryReport(
        schema_version=INVENTORY_SCHEMA_VERSION,
        raw_package_count=raw_package_count,
        unique_package_count=len(packages),
        packages=packages,
        rejected_package_count=rejected_package_count,
        rejected_packages=rejected_packages,
    )


def _corpus_role(
    aliases: tuple[str, ...],
    representative_alias_globs: tuple[str, ...],
    validation_alias_globs: tuple[str, ...],
) -> str:
    if representative_alias_globs:
        return (
            "representative"
            if any(
                fnmatch.fnmatchcase(alias, pattern)
                for alias in aliases
                for pattern in representative_alias_globs
            )
            else "validation"
        )
    if validation_alias_globs:
        return (
            "validation"
            if all(
                any(
                    fnmatch.fnmatchcase(alias, pattern)
                    for pattern in validation_alias_globs
                )
                for alias in aliases
            )
            else "representative"
        )
    return "representative"


def _alias_globs(
    values: Sequence[str],
    report: InventoryReport,
    *,
    role: str,
) -> tuple[str, ...]:
    patterns = tuple(values)
    if any(not isinstance(pattern, str) or not pattern.strip() for pattern in patterns):
        raise ValueError(f"{role} alias globs must be non-empty strings")
    patterns = tuple(pattern.strip() for pattern in patterns)
    if len(patterns) != len(set(patterns)):
        raise ValueError(f"{role} alias globs contain duplicates")
    all_aliases = tuple(
        alias
        for package in (*report.packages, *report.rejected_packages)
        for alias in package.aliases
    )
    unmatched = [
        pattern
        for pattern in patterns
        if not any(fnmatch.fnmatchcase(alias, pattern) for alias in all_aliases)
    ]
    if unmatched:
        raise ValueError(f"{role} alias globs matched no packages: {', '.join(unmatched)}")
    return patterns


def inventory_to_dict(
    report: InventoryReport,
    *,
    representative_alias_globs: Sequence[str] = (),
    validation_alias_globs: Sequence[str] = (),
) -> dict:
    if representative_alias_globs and validation_alias_globs:
        raise ValueError("representative and validation alias globs are mutually exclusive")
    representative_patterns = _alias_globs(
        representative_alias_globs,
        report,
        role="representative",
    )
    validation_patterns = _alias_globs(
        validation_alias_globs,
        report,
        role="validation",
    )
    package_roles = {
        package.package_id: _corpus_role(
            package.aliases,
            representative_patterns,
            validation_patterns,
        )
        for package in report.packages
    }
    representative_count = sum(role == "representative" for role in package_roles.values())
    return {
        "schemaVersion": report.schema_version,
        "rawPackageCount": report.raw_package_count,
        "uniquePackageCount": report.unique_package_count,
        "rejectedPackageCount": report.rejected_package_count,
        "uniqueRejectedPackageCount": len(report.rejected_packages),
        "corpusClassification": {
            "mode": (
                "explicit-representative-alias-globs"
                if representative_patterns
                else "explicit-validation-alias-globs"
                if validation_patterns
                else "all-representative"
            ),
            "representativeAliasGlobs": list(representative_patterns),
            "validationAliasGlobs": list(validation_patterns),
            "representativeUniquePackageCount": representative_count,
            "validationUniquePackageCount": len(report.packages) - representative_count,
        },
        "packages": [
            {
                "packageId": package.package_id,
                "sha256": package.sha256,
                "aliases": list(package.aliases),
                "corpusRole": package_roles[package.package_id],
                "capabilityIds": list(package.capability_ids),
                "matchingParts": {
                    capability_id: list(parts)
                    for capability_id, parts in sorted(package.matching_parts.items())
                },
            }
            for package in report.packages
        ],
        "rejectedPackages": [
            {
                "packageId": package.package_id,
                "sha256": package.sha256,
                "aliases": list(package.aliases),
                "corpusRole": _corpus_role(
                    package.aliases,
                    representative_patterns,
                    validation_patterns,
                ),
                "reasonCode": package.reason_code,
                "reason": package.reason,
            }
            for package in report.rejected_packages
        ],
    }


def write_inventory(
    report: InventoryReport,
    path: Path,
    *,
    representative_alias_globs: Sequence[str] = (),
    validation_alias_globs: Sequence[str] = (),
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        inventory_to_dict(
            report,
            representative_alias_globs=representative_alias_globs,
            validation_alias_globs=validation_alias_globs,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    output.write_text(f"{encoded}\n", encoding="utf-8")
    return output
