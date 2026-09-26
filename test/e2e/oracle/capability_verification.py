from __future__ import annotations

import hashlib
import json
import math
import posixpath
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from oracle.capability_contract import CapabilityDefinition


DERIVED_GATES = frozenset(
    {
        "native-powerpoint",
        "manual-visual",
        "regression",
        "bevel-local",
        "camera-local",
        "shadow-local",
        "reflection-local",
    }
)
SSIM_REGRESSION_BUDGET = 0.02
BEVEL_SCORE_THRESHOLD = 0.60
BEVEL_CORNER_SCORE_THRESHOLD = 0.78
BEVEL_RANGE_RATIO_THRESHOLD = 0.85
BEVEL_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD = 0.80
BEVEL_PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD = 0.70
BEVEL_SHADOW_AMPLITUDE_RATIO_THRESHOLD = 0.85
BEVEL_SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.05
BEVEL_SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.01
BEVEL_SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD = 1.05
BEVEL_SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD = 0.30
BEVEL_SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD = 1.60
BEVEL_DONUT_SHADOW_SECTOR_DEGREES = 30
BEVEL_MIN_DONUT_SECTOR_REFERENCE_SHADOW = 3.0
BEVEL_MINIMUM_BAND_WIDTH_PX = 4.0
CAMERA_CORNER_SCORE_THRESHOLD = 0.98
CAMERA_COLOR_SCORE_THRESHOLD = 0.97
CAMERA_GRADIENT_RANGE_RATIO_THRESHOLD = 0.65
CAMERA_GRADIENT_DIRECTION_THRESHOLD = 0.95
CAMERA_MINIMUM_REFERENCE_GRADIENT_RANGE = 4.0
CAMERA_TEXT_RASTER_TOLERANCE_RATIO = 0.0025
CAMERA_TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD = 0.90
CAMERA_TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD = 0.98
CAMERA_TEXT_INK_COVERAGE_RATIO_THRESHOLD = 0.90
CAMERA_SHADOW_RING_INNER_RATIO = 0.0018
CAMERA_SHADOW_RING_OUTER_RATIO = 0.016
CAMERA_SHADOW_BACKGROUND_LEVEL = 252.0
CAMERA_MINIMUM_REFERENCE_SHADOW_DENSITY = 0.25
CAMERA_SHADOW_ENERGY_RATIO_THRESHOLD = 0.70
CAMERA_SHADOW_DIRECTION_THRESHOLD = 0.95
CAMERA_PICTURE_CORNER_SCORE_THRESHOLD = 0.98
CAMERA_PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD = 0.95
CAMERA_PICTURE_RECTIFIED_EDGE_F1_THRESHOLD = 0.90
CAMERA_PICTURE_RECTIFIED_SIZE = 384
CAMERA_PICTURE_EDGE_TOLERANCE_RATIO = 0.008
CAMERA_PICTURE_CROP_MUTATION_RATIO = 0.12
CAMERA_BOTTOM_FRONT_CORNER_SCORE_THRESHOLD = 0.98
CAMERA_BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD = 1.0
CAMERA_BOTTOM_FRONT_SOURCE_FLAT_FILL = [68, 114, 196]
CAMERA_CUSTOM_RASTER_TOLERANCE_RATIO = 0.0025
CAMERA_CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD = 0.95
CAMERA_CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD = 0.98
CAMERA_CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD = 0.90
CAMERA_CUSTOM_CENTROID_SCORE_THRESHOLD = 0.99
CAMERA_CUSTOM_COLOR_SCORE_THRESHOLD = 0.98
CAMERA_CUSTOM_VERTICAL_SQUASH_RATIO = 0.20
SHADOW_RING_INNER_RATIO = 0.002
SHADOW_RING_OUTER_RATIO = 0.05
SHADOW_BACKGROUND_NOISE_FLOOR = 2.0
SHADOW_MINIMUM_REFERENCE_DENSITY = 0.25
SHADOW_INVISIBLE_DENSITY_THRESHOLD = 0.50
SHADOW_ENERGY_RATIO_THRESHOLD = 0.75
SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.25
SHADOW_FIELD_COSINE_THRESHOLD = 0.90
SHADOW_FIELD_IOU_THRESHOLD = 0.55
SHADOW_FIELD_ERROR_THRESHOLD = 0.35
SHADOW_CENTROID_ERROR_RATIO_THRESHOLD = 0.03
SHADOW_FIELD_BINARY_THRESHOLD = 2.0
REFLECTION_BLUR_PAD_MULTIPLIER = 2.0
REFLECTION_BACKGROUND_NOISE_FLOOR = 2.0
REFLECTION_MINIMUM_REFERENCE_DENSITY = 2.0
REFLECTION_INVISIBLE_DENSITY_THRESHOLD = 1.5
REFLECTION_ENERGY_RATIO_THRESHOLD = 0.85
REFLECTION_OVERSHOOT_RATIO_THRESHOLD = 1.20
REFLECTION_FIELD_COSINE_THRESHOLD = 0.95
REFLECTION_FIELD_IOU_THRESHOLD = 0.75
REFLECTION_FIELD_ERROR_THRESHOLD = 0.20
REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD = 0.03
REFLECTION_FIELD_BINARY_THRESHOLD = 2.0


class CapabilityVerificationError(ValueError):
    pass


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityVerificationError(f"{context} must be an object")
    return value


def _sha256(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CapabilityVerificationError(f"{context} must be a lowercase SHA-256")
    return value


def _finite_metric(value: Any, context: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise CapabilityVerificationError(f"{context} must be finite")
    return float(value)


def _color_bands(value: Any, context: str) -> list[list[float]]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(row, list) or len(row) != 3 for row in value)
    ):
        raise CapabilityVerificationError(f"{context} must contain three RGB bands")
    bands: list[list[float]] = []
    for row in value:
        parsed = [_finite_metric(channel, context) for channel in row]
        if any(channel < 0 or channel > 255 for channel in parsed):
            raise CapabilityVerificationError(f"{context} is outside the RGB domain")
        bands.append(parsed)
    return bands


def _runtime_environment(report: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    provenance = _mapping(report.get("provenance"), f"{case_id} provenance")
    return _mapping(provenance.get("runtime"), f"{case_id} runtime")


def _renderer_state(report: Mapping[str, Any], case_id: str) -> tuple[str, bool]:
    provenance = _mapping(report.get("provenance"), f"{case_id} provenance")
    renderer = _mapping(provenance.get("renderer"), f"{case_id} renderer")
    revision = renderer.get("revision")
    dirty = renderer.get("dirty")
    if not isinstance(revision, str) or len(revision) < 40 or dirty is not False:
        raise CapabilityVerificationError(
            f"{case_id} must come from one clean renderer revision"
        )
    return revision, dirty


def _case_id(report: Mapping[str, Any]) -> str:
    case_id = report.get("testFile")
    if not isinstance(case_id, str) or not case_id:
        raise CapabilityVerificationError("native evaluation report is missing testFile")
    return case_id


def _case_hashes(report: Mapping[str, Any], case_id: str) -> tuple[str, str]:
    provenance = _mapping(report.get("provenance"), f"{case_id} provenance")
    inputs = _mapping(provenance.get("inputs"), f"{case_id} inputs")
    source = _mapping(inputs.get("sourcePptx"), f"{case_id} sourcePptx")
    ground_truth = _mapping(inputs.get("groundTruth"), f"{case_id} groundTruth")
    if ground_truth.get("kind") not in {"pdf", "png"}:
        raise CapabilityVerificationError(f"{case_id} ground truth must be PDF or PNG")
    return (
        _sha256(source.get("sha256"), f"{case_id} source"),
        _sha256(ground_truth.get("combinedSha256"), f"{case_id} ground truth"),
    )


def _source_shape_effect_slide_indices(
    report: Mapping[str, Any],
    case_id: str,
    expected_source_hash: str,
    repo: Path,
    *,
    effect_name: str,
) -> set[int]:
    provenance = _mapping(report.get("provenance"), f"{case_id} provenance")
    inputs = _mapping(provenance.get("inputs"), f"{case_id} inputs")
    source = _mapping(inputs.get("sourcePptx"), f"{case_id} sourcePptx")
    path_value = source.get("path")
    expected_relative = (
        PurePosixPath("test/e2e/testdata/cases") / case_id / "source.pptx"
    )
    if (
        not isinstance(path_value, str)
        or "\\" in path_value
        or PurePosixPath(path_value) != expected_relative
    ):
        raise CapabilityVerificationError(
            f"{case_id} sourcePptx path must identify its local case source"
        )

    source_path = repo / expected_relative.as_posix()
    testdata_root = (repo / "test/e2e/testdata").resolve()
    try:
        resolved_source = source_path.resolve(strict=True)
        resolved_source.relative_to(testdata_root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise CapabilityVerificationError(
            f"{case_id} sourcePptx is missing or escapes local testdata"
        ) from error
    if not resolved_source.is_file():
        raise CapabilityVerificationError(f"{case_id} sourcePptx is missing")
    size_bytes = source.get("sizeBytes")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes != resolved_source.stat().st_size
    ):
        raise CapabilityVerificationError(f"{case_id} sourcePptx size changed")
    if hashlib.sha256(resolved_source.read_bytes()).hexdigest() != expected_source_hash:
        raise CapabilityVerificationError(f"{case_id} sourcePptx hash changed")

    presentation_namespace = (
        "http://schemas.openxmlformats.org/presentationml/2006/main"
    )
    drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    office_relationship_namespace = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    package_relationship_namespace = (
        "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    try:
        with ZipFile(resolved_source) as archive:
            presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
            relationships = ElementTree.fromstring(
                archive.read("ppt/_rels/presentation.xml.rels")
            )
            targets = {
                relationship.get("Id"): relationship.get("Target")
                for relationship in relationships.findall(
                    f"{{{package_relationship_namespace}}}Relationship"
                )
                if relationship.get("TargetMode") != "External"
            }
            slide_ids = presentation.findall(
                f"./{{{presentation_namespace}}}sldIdLst/"
                f"{{{presentation_namespace}}}sldId"
            )
            effect_slides: set[int] = set()
            for slide_index, slide_id in enumerate(slide_ids):
                relationship_id = slide_id.get(
                    f"{{{office_relationship_namespace}}}id"
                )
                target = targets.get(relationship_id)
                if not isinstance(target, str) or not target:
                    raise CapabilityVerificationError(
                        f"{case_id} source OOXML has an unresolved slide relationship"
                    )
                part_name = posixpath.normpath(posixpath.join("ppt", target))
                relative_part = PurePosixPath(part_name)
                if (
                    relative_part.is_absolute()
                    or ".." in relative_part.parts
                    or not relative_part.parts
                    or relative_part.parts[0] != "ppt"
                ):
                    raise CapabilityVerificationError(
                        f"{case_id} source OOXML has an invalid slide target"
                    )
                slide = ElementTree.fromstring(archive.read(relative_part.as_posix()))
                effect_path = (
                    f"./{{{presentation_namespace}}}spPr/"
                    f"{{{drawing_namespace}}}effectLst/"
                    f"{{{drawing_namespace}}}{effect_name}"
                )
                if any(
                    shape.find(effect_path) is not None
                    for shape in slide.iter(f"{{{presentation_namespace}}}sp")
                ):
                    effect_slides.add(slide_index)
    except CapabilityVerificationError:
        raise
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError) as error:
        raise CapabilityVerificationError(
            f"{case_id} source OOXML cannot be verified"
        ) from error
    return effect_slides


def _source_outer_shadow_slide_indices(
    report: Mapping[str, Any],
    case_id: str,
    expected_source_hash: str,
    repo: Path,
) -> set[int]:
    return _source_shape_effect_slide_indices(
        report,
        case_id,
        expected_source_hash,
        repo,
        effect_name="outerShdw",
    )


def _source_reflection_slide_indices(
    report: Mapping[str, Any],
    case_id: str,
    expected_source_hash: str,
    repo: Path,
) -> set[int]:
    return _source_shape_effect_slide_indices(
        report,
        case_id,
        expected_source_hash,
        repo,
        effect_name="reflection",
    )


def _case_result(
    report: Mapping[str, Any],
    manual_verdicts: Mapping[str, str],
) -> dict[str, Any]:
    case_id = _case_id(report)
    source_hash, ground_truth_hash = _case_hashes(report, case_id)
    quality = _mapping(report.get("quality"), f"{case_id} quality")
    errors = report.get("evaluationErrors")
    if not isinstance(errors, list):
        raise CapabilityVerificationError(f"{case_id} evaluationErrors must be a list")
    error_count = report.get("evaluationErrorCount")
    if not isinstance(error_count, int) or error_count != len(errors):
        raise CapabilityVerificationError(f"{case_id} evaluation error count is inconsistent")
    mismatch_count = report.get("oracleMismatchCount")
    if not isinstance(mismatch_count, int) or mismatch_count < 0:
        raise CapabilityVerificationError(f"{case_id} oracle mismatch count is invalid")
    needs_review = quality.get("needsReview") is True
    manual_verdict = manual_verdicts.get(case_id)
    if needs_review and manual_verdict not in {"passed", "accepted"}:
        raise CapabilityVerificationError(f"{case_id} requires an explicit passing manual verdict")
    if not needs_review:
        manual_verdict = "not-required"
    passed = (
        report.get("supported") is True
        and quality.get("passed") is True
        and not errors
        and mismatch_count == 0
    )
    return {
        "caseId": case_id,
        "sourceSha256": source_hash,
        "groundTruthSha256": ground_truth_hash,
        "skipped": False,
        "passed": passed,
        "needsReview": needs_review,
        "manualVerdict": manual_verdict,
        "runtimeErrors": list(errors),
        "metrics": {"ssim": _finite_metric(report.get("avgSsim"), f"{case_id} avgSsim")},
    }


def _reports_by_case(
    reports: Sequence[Mapping[str, Any]], context: str
) -> dict[str, Mapping[str, Any]]:
    if not reports:
        raise CapabilityVerificationError(f"{context} requires at least one report")
    by_case: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        if not isinstance(report, Mapping):
            raise CapabilityVerificationError(f"{context} entries must be objects")
        case_id = _case_id(report)
        if case_id in by_case:
            raise CapabilityVerificationError(f"{context} contains duplicate case {case_id}")
        by_case[case_id] = report
    return by_case


def _environment_key(report: Mapping[str, Any], case_id: str) -> str:
    runtime = dict(_runtime_environment(report, case_id))
    font_profile = runtime.get("fontProfile")
    if isinstance(font_profile, Mapping):
        # The resolved face list is the browser input. Keep the raw manifest
        # fingerprint in provenance, but do not make whitespace-only JSON
        # changes invalidate an otherwise identical regression environment.
        comparable_profile = dict(font_profile)
        comparable_profile.pop("manifest", None)
        runtime["fontProfile"] = comparable_profile
    return json.dumps(
        runtime,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_regression(
    current: Mapping[str, Mapping[str, Any]],
    baseline_reports: Sequence[Mapping[str, Any]],
    current_revision: str,
) -> None:
    baseline = _reports_by_case(baseline_reports, "regression baseline")
    if set(current) != set(baseline):
        raise CapabilityVerificationError("regression baseline case IDs must match current reports")
    baseline_revisions = {
        _renderer_state(report, case_id)[0] for case_id, report in baseline.items()
    }
    if len(baseline_revisions) != 1 or current_revision in baseline_revisions:
        raise CapabilityVerificationError(
            "regression baseline must use one earlier renderer revision"
        )
    for case_id in sorted(current):
        if _case_hashes(current[case_id], case_id) != _case_hashes(baseline[case_id], case_id):
            raise CapabilityVerificationError(
                f"{case_id} regression input hashes must match the current report"
            )
        if _environment_key(current[case_id], case_id) != _environment_key(
            baseline[case_id], case_id
        ):
            raise CapabilityVerificationError(
                f"{case_id} regression runtime environment must match the current report"
            )
        current_ssim = _finite_metric(current[case_id].get("avgSsim"), f"{case_id} current SSIM")
        baseline_ssim = _finite_metric(
            baseline[case_id].get("avgSsim"), f"{case_id} baseline SSIM"
        )
        if baseline_ssim - current_ssim > SSIM_REGRESSION_BUDGET + 1e-12:
            raise CapabilityVerificationError(
                f"{case_id} SSIM regression exceeds {SSIM_REGRESSION_BUDGET:.2f}: "
                f"{baseline_ssim:.4f} -> {current_ssim:.4f}"
            )


def _validate_bevel_local(
    report: Mapping[str, Any],
    current: Mapping[str, Mapping[str, Any]],
    current_revision: str,
    repo: Path,
) -> None:
    if report.get("schemaVersion") != 8:
        raise CapabilityVerificationError("bevel-local report requires schemaVersion=8")
    renderer = _mapping(report.get("renderer"), "bevel-local renderer")
    if renderer.get("revision") != current_revision or renderer.get("dirty") is not False:
        raise CapabilityVerificationError(
            "bevel-local report must match the clean native-report revision"
        )
    thresholds = _mapping(report.get("thresholds"), "bevel-local thresholds")
    if thresholds != {
        "score": BEVEL_SCORE_THRESHOLD,
        "cornerScore": BEVEL_CORNER_SCORE_THRESHOLD,
        "rangeRatio": BEVEL_RANGE_RATIO_THRESHOLD,
        "highlightAmplitudeRatio": BEVEL_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD,
        "pictureHighlightAmplitudeRatio": BEVEL_PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD,
        "shadowAmplitudeRatio": BEVEL_SHADOW_AMPLITUDE_RATIO_THRESHOLD,
        "shadowOvershootRatio": BEVEL_SHADOW_OVERSHOOT_RATIO_THRESHOLD,
        "solidDonutShadowOvershootRatio": (
            BEVEL_SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD
        ),
        "solidDonutShadowEnergyOvershootRatio": (
            BEVEL_SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD
        ),
        "solidDonutShadowLocalExcessRatio": (
            BEVEL_SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD
        ),
        "solidDonutShadowSectorOvershootRatio": (
            BEVEL_SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD
        ),
    }:
        raise CapabilityVerificationError("bevel-local report uses unexpected thresholds")
    values = report.get("caseResults")
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise CapabilityVerificationError("bevel-local caseResults must be a list of objects")
    by_case: dict[str, Mapping[str, Any]] = {}
    for value in values:
        case_id = value.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in by_case:
            raise CapabilityVerificationError("bevel-local case IDs must be unique strings")
        by_case[case_id] = value
    if set(by_case) != set(current):
        raise CapabilityVerificationError("bevel-local case IDs must match native reports")
    applicable_count = 0
    all_cases_passed = True
    for case_id, value in by_case.items():
        source_hash, ground_truth_hash = _case_hashes(current[case_id], case_id)
        if value.get("sourceSha256") != source_hash or value.get(
            "groundTruthSha256"
        ) != ground_truth_hash:
            raise CapabilityVerificationError(
                f"{case_id} bevel-local input hashes must match native reports"
            )
        slides = value.get("slides")
        if not isinstance(slides, list) or any(not isinstance(slide, Mapping) for slide in slides):
            raise CapabilityVerificationError(f"{case_id} bevel-local slides must be objects")
        native_slide_values = current[case_id].get("perSlide")
        if not isinstance(native_slide_values, list) or any(
            not isinstance(slide, Mapping) for slide in native_slide_values
        ):
            raise CapabilityVerificationError(
                f"{case_id} native report is missing per-slide artifacts"
            )
        native_slides = {
            slide.get("slideIdx"): slide
            for slide in native_slide_values
            if isinstance(slide.get("slideIdx"), int) and slide.get("hidden") is not True
        }
        evaluable_regions = 0
        slide_passes: list[bool] = []
        for slide_index, slide in enumerate(slides):
            context = f"{case_id} bevel-local slide {slide_index}"
            if not isinstance(slide.get("slideIdx"), int) or slide.get("slideIdx") < 0:
                raise CapabilityVerificationError(f"{context} index is invalid")
            native_slide = native_slides.get(slide.get("slideIdx"))
            if native_slide is None:
                raise CapabilityVerificationError(f"{context} is absent from the native report")
            native_artifacts = _mapping(
                native_slide.get("renderArtifacts"), f"{context} native render artifacts"
            )
            for kind in ("reference", "candidate"):
                path_value = slide.get(f"{kind}Path")
                expected_hash = _sha256(slide.get(f"{kind}Sha256"), f"{context} {kind}")
                native_artifact = _mapping(
                    native_artifacts.get(kind), f"{context} native {kind} artifact"
                )
                if (
                    native_artifact.get("path") != path_value
                    or native_artifact.get("sha256") != expected_hash
                ):
                    raise CapabilityVerificationError(
                        f"{context} does not match native report artifacts"
                    )
                if not isinstance(path_value, str) or "\\" in path_value:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                relative = PurePosixPath(path_value)
                if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                artifact = (repo / relative.as_posix()).resolve()
                try:
                    artifact.relative_to(repo.resolve())
                except ValueError as error:
                    raise CapabilityVerificationError(
                        f"{context} {kind} path escapes the repository"
                    ) from error
                if not artifact.is_file():
                    raise CapabilityVerificationError(f"{context} {kind} artifact is missing")
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if digest != expected_hash:
                    raise CapabilityVerificationError(f"{context} {kind} artifact hash changed")
            regions = slide.get("regions")
            if not isinstance(regions, list) or not regions or any(
                not isinstance(region, Mapping) for region in regions
            ):
                raise CapabilityVerificationError(f"{context} regions must be non-empty objects")
            region_passes: list[bool] = []
            for region_index, region in enumerate(regions):
                metric_context = f"{context} region {region_index}"
                geometry = _mapping(region.get("region"), f"{metric_context} geometry")
                surface = geometry.get("surface")
                if surface not in {"shape", "picture"}:
                    raise CapabilityVerificationError(
                        f"{metric_context} surface is unsupported"
                    )
                metrics = _mapping(region.get("metrics"), f"{metric_context} metrics")
                evaluable = metrics.get("evaluable")
                if not isinstance(evaluable, bool):
                    raise CapabilityVerificationError(f"{metric_context} evaluable is invalid")
                if not isinstance(metrics.get("passed"), bool):
                    raise CapabilityVerificationError(f"{metric_context} pass status is invalid")
                metric_thresholds = _mapping(
                    metrics.get("thresholds"), f"{metric_context} thresholds"
                )
                if evaluable:
                    evaluable_regions += 1
                    score = _finite_metric(metrics.get("score"), f"{metric_context} score")
                    corner_required = metrics.get("cornerRequired")
                    if not isinstance(corner_required, bool):
                        raise CapabilityVerificationError(
                            f"{metric_context} corner requirement is invalid"
                        )
                    corner_score = _finite_metric(
                        metrics.get("cornerScore"), f"{metric_context} corner score"
                    )
                    reference_range = _finite_metric(
                        metrics.get("referenceDynamicRange"),
                        f"{metric_context} reference dynamic range",
                    )
                    candidate_range = _finite_metric(
                        metrics.get("candidateDynamicRange"),
                        f"{metric_context} candidate dynamic range",
                    )
                    range_ratio = _finite_metric(
                        metrics.get("rangeRatio"), f"{metric_context} range ratio"
                    )
                    reference_highlight = _finite_metric(
                        metrics.get("referenceHighlightAmplitude"),
                        f"{metric_context} reference highlight amplitude",
                    )
                    candidate_highlight = _finite_metric(
                        metrics.get("candidateHighlightAmplitude"),
                        f"{metric_context} candidate highlight amplitude",
                    )
                    highlight_ratio = _finite_metric(
                        metrics.get("highlightAmplitudeRatio"),
                        f"{metric_context} highlight amplitude ratio",
                    )
                    reference_shadow = _finite_metric(
                        metrics.get("referenceShadowAmplitude"),
                        f"{metric_context} reference shadow amplitude",
                    )
                    candidate_shadow = _finite_metric(
                        metrics.get("candidateShadowAmplitude"),
                        f"{metric_context} candidate shadow amplitude",
                    )
                    shadow_ratio = _finite_metric(
                        metrics.get("shadowAmplitudeRatio"),
                        f"{metric_context} shadow amplitude ratio",
                    )
                    shadow_overshoot_ratio = _finite_metric(
                        metrics.get("shadowOvershootRatio"),
                        f"{metric_context} shadow overshoot ratio",
                    )
                    reference_shadow_energy = _finite_metric(
                        metrics.get("referenceShadowEnergy"),
                        f"{metric_context} reference shadow energy",
                    )
                    candidate_shadow_energy = _finite_metric(
                        metrics.get("candidateShadowEnergy"),
                        f"{metric_context} candidate shadow energy",
                    )
                    shadow_energy_overshoot_ratio = _finite_metric(
                        metrics.get("shadowEnergyOvershootRatio"),
                        f"{metric_context} shadow energy overshoot ratio",
                    )
                    shadow_local_excess_ratio = _finite_metric(
                        metrics.get("shadowLocalExcessRatio"),
                        f"{metric_context} shadow local excess ratio",
                    )
                    shadow_sector_overshoot_ratio = _finite_metric(
                        metrics.get("shadowSectorOvershootRatio"),
                        f"{metric_context} shadow sector overshoot ratio",
                    )
                    shadow_sector_values = metrics.get("shadowSectors")
                    if not isinstance(shadow_sector_values, list) or any(
                        not isinstance(sector, Mapping) for sector in shadow_sector_values
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} shadow sectors must be objects"
                        )
                    is_solid_donut = surface == "shape" and geometry.get("preset") == "donut"
                    sector_ratios: list[float] = []
                    seen_sectors: set[tuple[str, int]] = set()
                    for sector_index, sector in enumerate(shadow_sector_values):
                        sector_context = f"{metric_context} shadow sector {sector_index}"
                        contour = sector.get("contour")
                        start_angle = sector.get("startAngle")
                        end_angle = sector.get("endAngle")
                        pixel_count = sector.get("pixelCount")
                        if contour not in {"outer", "inner"}:
                            raise CapabilityVerificationError(
                                f"{sector_context} contour is invalid"
                            )
                        if (
                            not isinstance(start_angle, int)
                            or isinstance(start_angle, bool)
                            or start_angle < 0
                            or start_angle >= 360
                            or start_angle % BEVEL_DONUT_SHADOW_SECTOR_DEGREES != 0
                            or end_angle != start_angle + BEVEL_DONUT_SHADOW_SECTOR_DEGREES
                        ):
                            raise CapabilityVerificationError(
                                f"{sector_context} angles are invalid"
                            )
                        if (
                            not isinstance(pixel_count, int)
                            or isinstance(pixel_count, bool)
                            or pixel_count < 20
                        ):
                            raise CapabilityVerificationError(
                                f"{sector_context} pixel count is invalid"
                            )
                        sector_key = (contour, start_angle)
                        if sector_key in seen_sectors:
                            raise CapabilityVerificationError(
                                f"{metric_context} shadow sectors are duplicated"
                            )
                        seen_sectors.add(sector_key)
                        sector_reference = _finite_metric(
                            sector.get("referenceShadowEnergy"),
                            f"{sector_context} reference shadow energy",
                        )
                        sector_candidate = _finite_metric(
                            sector.get("candidateShadowEnergy"),
                            f"{sector_context} candidate shadow energy",
                        )
                        sector_ratio = _finite_metric(
                            sector.get("overshootRatio"),
                            f"{sector_context} overshoot ratio",
                        )
                        if (
                            sector_reference < BEVEL_MIN_DONUT_SECTOR_REFERENCE_SHADOW
                            or sector_candidate < 0
                            or sector_ratio < 0
                            or abs(sector_ratio - sector_candidate / sector_reference) > 1e-9
                        ):
                            raise CapabilityVerificationError(
                                f"{sector_context} metrics are inconsistent"
                            )
                        sector_ratios.append(sector_ratio)
                    if not is_solid_donut and (
                        shadow_sector_values or abs(shadow_sector_overshoot_ratio - 1.0) > 1e-9
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} has unexpected donut shadow sectors"
                        )
                    expected_sector_overshoot_ratio = max(sector_ratios, default=1.0)
                    if (
                        is_solid_donut
                        and abs(
                            shadow_sector_overshoot_ratio - expected_sector_overshoot_ratio
                        )
                        > 1e-9
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} shadow sector summary is inconsistent"
                        )
                    if (
                        reference_range < 0
                        or candidate_range < 0
                        or reference_highlight < 0
                        or candidate_highlight < 0
                        or reference_shadow < 0
                        or candidate_shadow < 0
                        or reference_shadow_energy < 0
                        or candidate_shadow_energy < 0
                        or not 0 <= range_ratio <= 1
                        or not 0 <= highlight_ratio <= 1
                        or not 0 <= shadow_ratio <= 1
                        or shadow_overshoot_ratio < 0
                        or shadow_energy_overshoot_ratio < 0
                        or shadow_local_excess_ratio < 0
                        or shadow_sector_overshoot_ratio < 0
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} metrics are outside their domains"
                        )
                    expected_range_ratio = (
                        min(reference_range, candidate_range) / max(reference_range, candidate_range)
                        if max(reference_range, candidate_range) > 1e-6
                        else 1.0
                    )
                    expected_highlight_ratio = (
                        min(reference_highlight, candidate_highlight)
                        / max(reference_highlight, candidate_highlight)
                        if max(reference_highlight, candidate_highlight) > 1e-6
                        else 1.0
                    )
                    expected_shadow_ratio = (
                        min(reference_shadow, candidate_shadow)
                        / max(reference_shadow, candidate_shadow)
                        if max(reference_shadow, candidate_shadow) > 1e-6
                        else 1.0
                    )
                    expected_shadow_overshoot_ratio = (
                        candidate_shadow / reference_shadow
                        if reference_shadow > 1e-6
                        else 1.0 + candidate_shadow
                    )
                    expected_shadow_energy_overshoot_ratio = (
                        candidate_shadow_energy / reference_shadow_energy
                        if reference_shadow_energy > 1e-6
                        else 1.0 + candidate_shadow_energy
                    )
                    if (
                        abs(range_ratio - expected_range_ratio) > 1e-9
                        or abs(highlight_ratio - expected_highlight_ratio) > 1e-9
                        or abs(shadow_ratio - expected_shadow_ratio) > 1e-9
                        or abs(shadow_overshoot_ratio - expected_shadow_overshoot_ratio) > 1e-9
                        or abs(
                            shadow_energy_overshoot_ratio
                            - expected_shadow_energy_overshoot_ratio
                        )
                        > 1e-9
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} amplitude metrics are inconsistent"
                        )
                    if metric_thresholds != thresholds:
                        raise CapabilityVerificationError(
                            f"{metric_context} uses unexpected thresholds"
                        )
                    shadow_overshoot_threshold = (
                        BEVEL_SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD
                        if surface == "shape" and geometry.get("preset") == "donut"
                        else BEVEL_SHADOW_OVERSHOOT_RATIO_THRESHOLD
                    )
                    shadow_energy_overshoot_threshold = (
                        BEVEL_SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD
                        if surface == "shape" and geometry.get("preset") == "donut"
                        else math.inf
                    )
                    shadow_local_excess_threshold = (
                        BEVEL_SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD
                        if surface == "shape" and geometry.get("preset") == "donut"
                        else math.inf
                    )
                    shadow_sector_overshoot_threshold = (
                        BEVEL_SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD
                        if is_solid_donut
                        else math.inf
                    )
                    expected_pass = (
                        score >= BEVEL_SCORE_THRESHOLD
                        and range_ratio >= BEVEL_RANGE_RATIO_THRESHOLD
                        and highlight_ratio
                        >= (
                            BEVEL_PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD
                            if surface == "picture"
                            else BEVEL_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD
                        )
                        and shadow_ratio >= BEVEL_SHADOW_AMPLITUDE_RATIO_THRESHOLD
                        and shadow_overshoot_ratio <= shadow_overshoot_threshold
                        and shadow_energy_overshoot_ratio
                        <= shadow_energy_overshoot_threshold
                        and shadow_local_excess_ratio <= shadow_local_excess_threshold
                        and shadow_sector_overshoot_ratio
                        <= shadow_sector_overshoot_threshold
                        and (
                            not corner_required
                            or corner_score >= BEVEL_CORNER_SCORE_THRESHOLD
                        )
                    )
                else:
                    if metrics.get("reason") != "bevel-band-below-resolution-floor":
                        raise CapabilityVerificationError(
                            f"{metric_context} has an unknown unevaluable reason"
                        )
                    band_width = _finite_metric(
                        metrics.get("bandWidthPx"), f"{metric_context} band width"
                    )
                    expected_thresholds = {
                        **thresholds,
                        "minimumBandWidthPx": BEVEL_MINIMUM_BAND_WIDTH_PX,
                    }
                    if (
                        band_width >= BEVEL_MINIMUM_BAND_WIDTH_PX
                        or metric_thresholds != expected_thresholds
                    ):
                        raise CapabilityVerificationError(
                            f"{metric_context} resolution limit is inconsistent"
                        )
                    expected_pass = True
                if metrics.get("passed") is not expected_pass:
                    raise CapabilityVerificationError(
                        f"{metric_context} metric pass status is inconsistent"
                    )
                region_passes.append(expected_pass)
            slide_passed = all(region_passes)
            if slide.get("passed") is not slide_passed:
                raise CapabilityVerificationError(f"{context} pass status is inconsistent")
            slide_passes.append(slide_passed)
        slides_by_index = {slide.get("slideIdx"): slide for slide in slides}
        equivalence_values = value.get("equivalencePairs")
        if not isinstance(equivalence_values, list) or any(
            not isinstance(pair, Mapping) for pair in equivalence_values
        ):
            raise CapabilityVerificationError(
                f"{case_id} bevel-local equivalence pairs must be objects"
            )
        seen_pairs: set[tuple[int, int]] = set()
        equivalence_passes: list[bool] = []
        for pair_index, pair in enumerate(equivalence_values):
            context = f"{case_id} bevel-local equivalence pair {pair_index}"
            left_index = pair.get("leftSlideIdx")
            right_index = pair.get("rightSlideIdx")
            if (
                not isinstance(left_index, int)
                or isinstance(left_index, bool)
                or left_index < 0
                or not isinstance(right_index, int)
                or isinstance(right_index, bool)
                or right_index < 0
                or left_index == right_index
                or (left_index, right_index) in seen_pairs
            ):
                raise CapabilityVerificationError(f"{context} indices are invalid")
            seen_pairs.add((left_index, right_index))
            left = slides_by_index.get(left_index)
            right = slides_by_index.get(right_index)
            if left is None or right is None:
                raise CapabilityVerificationError(f"{context} slides are missing")
            expected_reference_equal = (
                left.get("referenceSha256") == right.get("referenceSha256")
            )
            expected_candidate_equal = (
                left.get("candidateSha256") == right.get("candidateSha256")
            )
            expected_pair_pass = expected_reference_equal and expected_candidate_equal
            if (
                pair.get("referenceEqual") is not expected_reference_equal
                or pair.get("candidateEqual") is not expected_candidate_equal
                or pair.get("passed") is not expected_pair_pass
            ):
                raise CapabilityVerificationError(
                    f"{context} equivalence evidence is inconsistent"
                )
            equivalence_passes.append(expected_pair_pass)
        applicable = value.get("applicable")
        if not isinstance(applicable, bool) or applicable is not (evaluable_regions > 0):
            raise CapabilityVerificationError(f"{case_id} bevel-local applicability is inconsistent")
        if applicable:
            applicable_count += 1
        case_passed = all(slide_passes) and all(equivalence_passes)
        if value.get("passed") is not case_passed:
            raise CapabilityVerificationError(f"{case_id} bevel-local pass status is inconsistent")
        all_cases_passed = all_cases_passed and case_passed
        if not case_passed:
            raise CapabilityVerificationError(f"{case_id} bevel-local report failed")
    if applicable_count < 1 or report.get("applicableCaseCount") != applicable_count:
        raise CapabilityVerificationError("bevel-local report requires applicable case evidence")
    if report.get("passed") is not all_cases_passed or report.get("passed") is not True:
        raise CapabilityVerificationError("bevel-local report failed")


def _validate_camera_local(
    report: Mapping[str, Any],
    current: Mapping[str, Mapping[str, Any]],
    current_revision: str,
    repo: Path,
) -> None:
    schema_version = report.get("schemaVersion")
    if schema_version not in {6, 7}:
        raise CapabilityVerificationError("camera-local report requires schemaVersion=6 or 7")
    renderer = _mapping(report.get("renderer"), "camera-local renderer")
    if renderer.get("revision") != current_revision or renderer.get("dirty") is not False:
        raise CapabilityVerificationError(
            "camera-local report must match the clean native-report revision"
        )
    expected_thresholds = {
        "plane": {
            "cornerScore": CAMERA_CORNER_SCORE_THRESHOLD,
            "colorScore": CAMERA_COLOR_SCORE_THRESHOLD,
            "gradientRangeRatio": CAMERA_GRADIENT_RANGE_RATIO_THRESHOLD,
            "gradientDirection": CAMERA_GRADIENT_DIRECTION_THRESHOLD,
            "minimumReferenceGradientRange": CAMERA_MINIMUM_REFERENCE_GRADIENT_RANGE,
            "shadowRingInnerRatio": CAMERA_SHADOW_RING_INNER_RATIO,
            "shadowRingOuterRatio": CAMERA_SHADOW_RING_OUTER_RATIO,
            "shadowBackgroundLevel": CAMERA_SHADOW_BACKGROUND_LEVEL,
            "minimumReferenceShadowDensity": CAMERA_MINIMUM_REFERENCE_SHADOW_DENSITY,
            "shadowEnergyRatio": CAMERA_SHADOW_ENERGY_RATIO_THRESHOLD,
            "shadowDirectionCosine": CAMERA_SHADOW_DIRECTION_THRESHOLD,
        },
        "text": {
            "rasterToleranceRatio": CAMERA_TEXT_RASTER_TOLERANCE_RATIO,
            "tolerantForegroundF1": CAMERA_TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD,
            "tolerantBoundsScore": CAMERA_TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD,
            "inkCoverageRatio": CAMERA_TEXT_INK_COVERAGE_RATIO_THRESHOLD,
        },
        "picture": {
            "cornerScore": CAMERA_PICTURE_CORNER_SCORE_THRESHOLD,
            "rectifiedColorScore": CAMERA_PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
            "rectifiedEdgeF1": CAMERA_PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
            "rectifiedSize": CAMERA_PICTURE_RECTIFIED_SIZE,
            "edgeToleranceRatio": CAMERA_PICTURE_EDGE_TOLERANCE_RATIO,
            "cropMutationRatio": CAMERA_PICTURE_CROP_MUTATION_RATIO,
        },
        "bottom-material": {
            "cornerScore": CAMERA_BOTTOM_FRONT_CORNER_SCORE_THRESHOLD,
            "meanBandColorError": CAMERA_BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD,
            "sourceFlatFill": CAMERA_BOTTOM_FRONT_SOURCE_FLAT_FILL,
        },
        "custom-geometry": {
            "rasterToleranceRatio": CAMERA_CUSTOM_RASTER_TOLERANCE_RATIO,
            "tolerantForegroundF1": CAMERA_CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD,
            "tolerantBoundsScore": CAMERA_CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD,
            "foregroundAreaRatio": CAMERA_CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD,
            "centroidScore": CAMERA_CUSTOM_CENTROID_SCORE_THRESHOLD,
            "colorScore": CAMERA_CUSTOM_COLOR_SCORE_THRESHOLD,
            "verticalSquashRatio": CAMERA_CUSTOM_VERTICAL_SQUASH_RATIO,
        },
    }
    if schema_version >= 7:
        expected_thresholds["picture-group"] = dict(expected_thresholds["picture"])
    thresholds = _mapping(report.get("thresholds"), "camera-local thresholds")
    if thresholds != expected_thresholds:
        raise CapabilityVerificationError("camera-local report uses unexpected thresholds")

    values = report.get("caseResults")
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise CapabilityVerificationError("camera-local caseResults must be a list of objects")
    by_case: dict[str, Mapping[str, Any]] = {}
    for value in values:
        case_id = value.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in by_case:
            raise CapabilityVerificationError("camera-local case IDs must be unique strings")
        by_case[case_id] = value
    if set(by_case) != set(current):
        raise CapabilityVerificationError("camera-local case IDs must match native reports")

    applicable_count = 0
    all_cases_passed = True
    for case_id, value in by_case.items():
        source_hash, ground_truth_hash = _case_hashes(current[case_id], case_id)
        if value.get("sourceSha256") != source_hash or value.get(
            "groundTruthSha256"
        ) != ground_truth_hash:
            raise CapabilityVerificationError(
                f"{case_id} camera-local input hashes must match native reports"
            )
        slides = value.get("slides")
        if not isinstance(slides, list) or any(not isinstance(slide, Mapping) for slide in slides):
            raise CapabilityVerificationError(f"{case_id} camera-local slides must be objects")
        native_slide_values = current[case_id].get("perSlide")
        if not isinstance(native_slide_values, list) or any(
            not isinstance(slide, Mapping) for slide in native_slide_values
        ):
            raise CapabilityVerificationError(
                f"{case_id} native report is missing per-slide artifacts"
            )
        native_slides = {
            slide.get("slideIdx"): slide
            for slide in native_slide_values
            if isinstance(slide.get("slideIdx"), int) and slide.get("hidden") is not True
        }
        seen_slide_indices: set[int] = set()
        slide_passes: list[bool] = []
        for slide_index, slide in enumerate(slides):
            context = f"{case_id} camera-local slide {slide_index}"
            source_slide_index = slide.get("slideIdx")
            if (
                not isinstance(source_slide_index, int)
                or source_slide_index < 0
                or source_slide_index in seen_slide_indices
            ):
                raise CapabilityVerificationError(f"{context} index is invalid or duplicated")
            seen_slide_indices.add(source_slide_index)
            native_slide = native_slides.get(source_slide_index)
            if native_slide is None:
                raise CapabilityVerificationError(f"{context} is absent from the native report")
            native_artifacts = _mapping(
                native_slide.get("renderArtifacts"), f"{context} native render artifacts"
            )
            for kind in ("reference", "candidate"):
                path_value = slide.get(f"{kind}Path")
                expected_hash = _sha256(slide.get(f"{kind}Sha256"), f"{context} {kind}")
                native_artifact = _mapping(
                    native_artifacts.get(kind), f"{context} native {kind} artifact"
                )
                if (
                    native_artifact.get("path") != path_value
                    or native_artifact.get("sha256") != expected_hash
                ):
                    raise CapabilityVerificationError(
                        f"{context} does not match native report artifacts"
                    )
                if not isinstance(path_value, str) or "\\" in path_value:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                relative = PurePosixPath(path_value)
                if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                artifact = (repo / relative.as_posix()).resolve()
                try:
                    artifact.relative_to(repo.resolve())
                except ValueError as error:
                    raise CapabilityVerificationError(
                        f"{context} {kind} path escapes the repository"
                    ) from error
                if not artifact.is_file():
                    raise CapabilityVerificationError(f"{context} {kind} artifact is missing")
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if digest != expected_hash:
                    raise CapabilityVerificationError(f"{context} {kind} artifact hash changed")

            modality = slide.get("modality")
            if modality not in expected_thresholds:
                raise CapabilityVerificationError(f"{context} modality is unsupported")
            modality_thresholds = expected_thresholds[modality]
            metrics = _mapping(slide.get("metrics"), f"{context} metrics")
            if metrics.get("evaluable") is not True:
                raise CapabilityVerificationError(f"{context} must contain evaluable metrics")
            if (
                _mapping(metrics.get("thresholds"), f"{context} thresholds")
                != modality_thresholds
            ):
                raise CapabilityVerificationError(f"{context} uses unexpected thresholds")
            if modality == "plane":
                corner_score = _finite_metric(
                    metrics.get("cornerScore"), f"{context} corner score"
                )
                mean_corner_error = _finite_metric(
                    metrics.get("meanCornerErrorRatio"), f"{context} corner error"
                )
                color_score = _finite_metric(
                    metrics.get("colorScore"), f"{context} color score"
                )
                reference_range = _finite_metric(
                    metrics.get("referenceGradientRange"),
                    f"{context} reference gradient range",
                )
                candidate_range = _finite_metric(
                    metrics.get("candidateGradientRange"),
                    f"{context} candidate gradient range",
                )
                range_ratio = _finite_metric(
                    metrics.get("gradientRangeRatio"), f"{context} gradient range ratio"
                )
                direction = _finite_metric(
                    metrics.get("gradientDirection"), f"{context} gradient direction"
                )
                shadow_required = metrics.get("shadowRequired")
                shadow_measurable = metrics.get("shadowMeasurable")
                shadow_passed = metrics.get("shadowPassed")
                if (
                    not isinstance(shadow_required, bool)
                    or not isinstance(shadow_measurable, bool)
                    or not isinstance(shadow_passed, bool)
                ):
                    raise CapabilityVerificationError(
                        f"{context} shadow flags must be booleans"
                    )
                reference_shadow_density = _finite_metric(
                    metrics.get("referenceShadowDensity"),
                    f"{context} reference shadow density",
                )
                candidate_shadow_density = _finite_metric(
                    metrics.get("candidateShadowDensity"),
                    f"{context} candidate shadow density",
                )
                shadow_energy_ratio = _finite_metric(
                    metrics.get("shadowEnergyRatio"),
                    f"{context} shadow energy ratio",
                )
                shadow_direction = _finite_metric(
                    metrics.get("shadowDirectionCosine"),
                    f"{context} shadow direction cosine",
                )
                ring_values = {
                    name: metrics.get(name)
                    for name in (
                        "referenceShadowRingPixels",
                        "candidateShadowRingPixels",
                        "shadowRingInnerPx",
                        "shadowRingOuterPx",
                    )
                }
                if any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 1
                    for value in ring_values.values()
                ) or ring_values["shadowRingOuterPx"] <= ring_values["shadowRingInnerPx"]:
                    raise CapabilityVerificationError(
                        f"{context} shadow ring dimensions are invalid"
                    )
                if (
                    not 0 <= corner_score <= 1
                    or mean_corner_error < 0
                    or not 0 <= color_score <= 1
                    or reference_range < 0
                    or candidate_range < 0
                    or not 0 <= range_ratio <= 1
                    or not -1 <= direction <= 1
                    or reference_shadow_density < 0
                    or candidate_shadow_density < 0
                    or not 0 <= shadow_energy_ratio <= 1
                    or not -1 <= shadow_direction <= 1
                ):
                    raise CapabilityVerificationError(
                        f"{context} metrics are outside their domains"
                    )
                gradient_required = metrics.get("gradientRequired")
                if not isinstance(gradient_required, bool) or gradient_required is not (
                    reference_range >= CAMERA_MINIMUM_REFERENCE_GRADIENT_RANGE
                ):
                    raise CapabilityVerificationError(
                        f"{context} gradient requirement is inconsistent"
                    )
                expected_corner_score = max(0.0, 1.0 - mean_corner_error)
                expected_range_ratio = (
                    min(reference_range, candidate_range)
                    / max(reference_range, candidate_range)
                    if max(reference_range, candidate_range) > 0
                    else 1.0
                )
                if (
                    abs(corner_score - expected_corner_score) > 1e-9
                    or abs(range_ratio - expected_range_ratio) > 1e-9
                ):
                    raise CapabilityVerificationError(
                        f"{context} plane metrics are inconsistent"
                    )
                expected_shadow_measurable = (
                    shadow_required
                    and reference_shadow_density
                    >= CAMERA_MINIMUM_REFERENCE_SHADOW_DENSITY
                )
                maximum_shadow_density = max(
                    reference_shadow_density,
                    candidate_shadow_density,
                )
                expected_shadow_energy_ratio = (
                    min(reference_shadow_density, candidate_shadow_density)
                    / maximum_shadow_density
                    if maximum_shadow_density > 1e-9
                    else 1.0
                )
                expected_shadow_passed = not expected_shadow_measurable or (
                    shadow_energy_ratio >= CAMERA_SHADOW_ENERGY_RATIO_THRESHOLD
                    and shadow_direction >= CAMERA_SHADOW_DIRECTION_THRESHOLD
                )
                if (
                    abs(shadow_energy_ratio - expected_shadow_energy_ratio) > 1e-9
                    or shadow_measurable is not expected_shadow_measurable
                    or shadow_passed is not expected_shadow_passed
                ):
                    raise CapabilityVerificationError(
                        f"{context} shadow metrics are inconsistent"
                    )
                shadow_sensitivity = _mapping(
                    metrics.get("shadowSensitivity"),
                    f"{context} shadow sensitivity",
                )
                if (
                    shadow_sensitivity.get("mutation") != "erase-exterior-shadow"
                    or shadow_sensitivity.get("applicable")
                    is not expected_shadow_measurable
                ):
                    raise CapabilityVerificationError(
                        f"{context} shadow sensitivity is inconsistent"
                    )
                if expected_shadow_measurable:
                    mutated_density = _finite_metric(
                        shadow_sensitivity.get("mutatedCandidateShadowDensity"),
                        f"{context} mutated shadow density",
                    )
                    mutated_energy_ratio = _finite_metric(
                        shadow_sensitivity.get("mutatedShadowEnergyRatio"),
                        f"{context} mutated shadow energy ratio",
                    )
                    mutated_direction = _finite_metric(
                        shadow_sensitivity.get("mutatedShadowDirectionCosine"),
                        f"{context} mutated shadow direction",
                    )
                    mutated_passed = shadow_sensitivity.get("mutatedShadowPassed")
                    detected = shadow_sensitivity.get("detected")
                    if (
                        mutated_density < 0
                        or not 0 <= mutated_energy_ratio <= 1
                        or not -1 <= mutated_direction <= 1
                        or not isinstance(mutated_passed, bool)
                        or not isinstance(detected, bool)
                    ):
                        raise CapabilityVerificationError(
                            f"{context} shadow sensitivity is outside its domain"
                        )
                    mutated_maximum_density = max(
                        reference_shadow_density,
                        mutated_density,
                    )
                    expected_mutated_energy_ratio = (
                        min(reference_shadow_density, mutated_density)
                        / mutated_maximum_density
                        if mutated_maximum_density > 1e-9
                        else 1.0
                    )
                    expected_mutated_passed = (
                        mutated_energy_ratio >= CAMERA_SHADOW_ENERGY_RATIO_THRESHOLD
                        and mutated_direction >= CAMERA_SHADOW_DIRECTION_THRESHOLD
                    )
                    if (
                        abs(mutated_energy_ratio - expected_mutated_energy_ratio) > 1e-9
                        or mutated_passed is not expected_mutated_passed
                        or detected is not (not expected_mutated_passed)
                        or detected is not True
                    ):
                        raise CapabilityVerificationError(
                            f"{context} shadow sensitivity is inconsistent"
                        )
                elif shadow_sensitivity.get("detected") is not None:
                    raise CapabilityVerificationError(
                        f"{context} shadow sensitivity is inconsistent"
                    )
                expected_pass = (
                    corner_score >= CAMERA_CORNER_SCORE_THRESHOLD
                    and color_score >= CAMERA_COLOR_SCORE_THRESHOLD
                    and (
                        not gradient_required
                        or (
                            range_ratio >= CAMERA_GRADIENT_RANGE_RATIO_THRESHOLD
                            and direction >= CAMERA_GRADIENT_DIRECTION_THRESHOLD
                        )
                    )
                    and expected_shadow_passed
                )
            elif modality == "bottom-material":
                corner_score = _finite_metric(
                    metrics.get("cornerScore"), f"{context} corner score"
                )
                mean_corner_error = _finite_metric(
                    metrics.get("meanCornerErrorRatio"), f"{context} corner error"
                )
                mean_color_error = _finite_metric(
                    metrics.get("meanBandColorError"), f"{context} material color error"
                )
                if (
                    not 0 <= corner_score <= 1
                    or mean_corner_error < 0
                    or mean_color_error < 0
                    or abs(corner_score - max(0.0, 1.0 - mean_corner_error)) > 1e-9
                ):
                    raise CapabilityVerificationError(
                        f"{context} bottom material metrics are outside their domains"
                    )
                reference_bands = _color_bands(
                    metrics.get("referenceBands"), f"{context} reference bands"
                )
                candidate_bands = _color_bands(
                    metrics.get("candidateBands"), f"{context} candidate bands"
                )
                expected_color_error = sum(
                    abs(reference_bands[row][channel] - candidate_bands[row][channel])
                    for row in range(3)
                    for channel in range(3)
                ) / 9
                if abs(mean_color_error - expected_color_error) > 1e-9:
                    raise CapabilityVerificationError(
                        f"{context} bottom material color metrics are inconsistent"
                    )
                sensitivity = _mapping(
                    metrics.get("flatFillSensitivity"),
                    f"{context} flat fill sensitivity",
                )
                if (
                    sensitivity.get("mutation") != "restore-source-flat-fill"
                    or sensitivity.get("sourceFlatFill")
                    != CAMERA_BOTTOM_FRONT_SOURCE_FLAT_FILL
                ):
                    raise CapabilityVerificationError(
                        f"{context} flat fill sensitivity is inconsistent"
                    )
                mutated_bands = _color_bands(
                    sensitivity.get("mutatedBands"), f"{context} mutated bands"
                )
                mutated_error = _finite_metric(
                    sensitivity.get("mutatedMeanBandColorError"),
                    f"{context} mutated material color error",
                )
                expected_mutated_error = sum(
                    abs(reference_bands[row][channel] - mutated_bands[row][channel])
                    for row in range(3)
                    for channel in range(3)
                ) / 9
                mutated_passed = sensitivity.get("mutatedPassed")
                detected = sensitivity.get("detected")
                expected_mutated_passed = (
                    corner_score >= CAMERA_BOTTOM_FRONT_CORNER_SCORE_THRESHOLD
                    and mutated_error
                    <= CAMERA_BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD
                )
                if (
                    mutated_error < 0
                    or abs(mutated_error - expected_mutated_error) > 1e-9
                    or not isinstance(mutated_passed, bool)
                    or mutated_passed is not expected_mutated_passed
                    or not isinstance(detected, bool)
                    or detected is not (not expected_mutated_passed)
                    or detected is not True
                ):
                    raise CapabilityVerificationError(
                        f"{context} flat fill sensitivity is inconsistent"
                    )
                expected_pass = (
                    corner_score >= CAMERA_BOTTOM_FRONT_CORNER_SCORE_THRESHOLD
                    and mean_color_error
                    <= CAMERA_BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD
                    and detected
                )
            elif modality == "custom-geometry":
                raster_tolerance_px = metrics.get("rasterTolerancePx")
                reference_coverage = _finite_metric(
                    metrics.get("referenceCoverageAtTolerance"),
                    f"{context} custom reference coverage",
                )
                candidate_coverage = _finite_metric(
                    metrics.get("candidateCoverageAtTolerance"),
                    f"{context} custom candidate coverage",
                )
                tolerant_foreground_f1 = _finite_metric(
                    metrics.get("tolerantForegroundF1"),
                    f"{context} custom tolerant foreground F1",
                )
                tolerant_bounds_score = _finite_metric(
                    metrics.get("tolerantBoundsScore"),
                    f"{context} custom tolerant bounds score",
                )
                tolerant_bounds_error = _finite_metric(
                    metrics.get("tolerantMeanBoundsErrorRatio"),
                    f"{context} custom tolerant bounds error",
                )
                foreground_area_ratio = _finite_metric(
                    metrics.get("foregroundAreaRatio"),
                    f"{context} custom foreground area ratio",
                )
                centroid_score = _finite_metric(
                    metrics.get("centroidScore"), f"{context} custom centroid score"
                )
                centroid_error = _finite_metric(
                    metrics.get("centroidErrorRatio"),
                    f"{context} custom centroid error",
                )
                color_score = _finite_metric(
                    metrics.get("colorScore"), f"{context} custom color score"
                )
                mean_color_error = _finite_metric(
                    metrics.get("meanColorError"), f"{context} custom color error"
                )
                reference_pixels = metrics.get("referenceForegroundPixels")
                candidate_pixels = metrics.get("candidateForegroundPixels")
                if (
                    not isinstance(raster_tolerance_px, int)
                    or isinstance(raster_tolerance_px, bool)
                    or raster_tolerance_px < 1
                    or not isinstance(reference_pixels, int)
                    or isinstance(reference_pixels, bool)
                    or reference_pixels < 1
                    or not isinstance(candidate_pixels, int)
                    or isinstance(candidate_pixels, bool)
                    or candidate_pixels < 1
                    or any(
                        not 0 <= value <= 1
                        for value in (
                            reference_coverage,
                            candidate_coverage,
                            tolerant_foreground_f1,
                            tolerant_bounds_score,
                            foreground_area_ratio,
                            centroid_score,
                            color_score,
                        )
                    )
                    or tolerant_bounds_error < 0
                    or centroid_error < 0
                    or mean_color_error < 0
                ):
                    raise CapabilityVerificationError(
                        f"{context} custom metrics are outside their domains"
                    )

                colors: list[list[float]] = []
                for kind in ("reference", "candidate"):
                    color_value = metrics.get(f"{kind}Color")
                    if not isinstance(color_value, list) or len(color_value) != 3:
                        raise CapabilityVerificationError(
                            f"{context} custom {kind} color is invalid"
                        )
                    parsed = [
                        _finite_metric(channel, f"{context} custom {kind} color")
                        for channel in color_value
                    ]
                    if any(channel < 0 or channel > 255 for channel in parsed):
                        raise CapabilityVerificationError(
                            f"{context} custom {kind} color is invalid"
                        )
                    colors.append(parsed)
                expected_f1 = (
                    2
                    * reference_coverage
                    * candidate_coverage
                    / (reference_coverage + candidate_coverage)
                    if reference_coverage + candidate_coverage > 0
                    else 0.0
                )
                expected_area_ratio = min(reference_pixels, candidate_pixels) / max(
                    reference_pixels, candidate_pixels
                )
                expected_color_error = sum(
                    abs(colors[0][channel] - colors[1][channel]) for channel in range(3)
                ) / 3
                if (
                    abs(tolerant_foreground_f1 - expected_f1) > 1e-9
                    or abs(tolerant_bounds_score - max(0.0, 1.0 - tolerant_bounds_error))
                    > 1e-9
                    or abs(foreground_area_ratio - expected_area_ratio) > 1e-9
                    or abs(centroid_score - max(0.0, 1.0 - centroid_error)) > 1e-9
                    or abs(mean_color_error - expected_color_error) > 1e-9
                    or abs(color_score - max(0.0, 1.0 - mean_color_error / 255)) > 1e-9
                ):
                    raise CapabilityVerificationError(
                        f"{context} custom metrics are inconsistent"
                    )

                sensitivity = _mapping(
                    metrics.get("squashSensitivity"),
                    f"{context} custom squash sensitivity",
                )
                mutated_f1 = _finite_metric(
                    sensitivity.get("mutatedTolerantForegroundF1"),
                    f"{context} mutated custom foreground F1",
                )
                mutated_bounds = _finite_metric(
                    sensitivity.get("mutatedTolerantBoundsScore"),
                    f"{context} mutated custom bounds score",
                )
                mutated_area = _finite_metric(
                    sensitivity.get("mutatedForegroundAreaRatio"),
                    f"{context} mutated custom area ratio",
                )
                mutated_centroid = _finite_metric(
                    sensitivity.get("mutatedCentroidScore"),
                    f"{context} mutated custom centroid score",
                )
                mutated_color = _finite_metric(
                    sensitivity.get("mutatedColorScore"),
                    f"{context} mutated custom color score",
                )
                mutated_passed = sensitivity.get("mutatedPassed")
                detected = sensitivity.get("detected")
                expected_mutated_pass = (
                    mutated_f1 >= CAMERA_CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD
                    and mutated_bounds >= CAMERA_CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD
                    and mutated_area >= CAMERA_CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD
                    and mutated_centroid >= CAMERA_CUSTOM_CENTROID_SCORE_THRESHOLD
                    and mutated_color >= CAMERA_CUSTOM_COLOR_SCORE_THRESHOLD
                )
                if (
                    sensitivity.get("mutation") != "vertical-squash"
                    or sensitivity.get("ratio") != CAMERA_CUSTOM_VERTICAL_SQUASH_RATIO
                    or any(
                        not 0 <= value <= 1
                        for value in (
                            mutated_f1,
                            mutated_bounds,
                            mutated_area,
                            mutated_centroid,
                            mutated_color,
                        )
                    )
                    or not isinstance(mutated_passed, bool)
                    or mutated_passed is not expected_mutated_pass
                    or not isinstance(detected, bool)
                    or detected is not (not expected_mutated_pass)
                    or detected is not True
                ):
                    raise CapabilityVerificationError(
                        f"{context} custom squash sensitivity is inconsistent"
                    )
                expected_pass = (
                    tolerant_foreground_f1
                    >= CAMERA_CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD
                    and tolerant_bounds_score
                    >= CAMERA_CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD
                    and foreground_area_ratio
                    >= CAMERA_CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD
                    and centroid_score >= CAMERA_CUSTOM_CENTROID_SCORE_THRESHOLD
                    and color_score >= CAMERA_CUSTOM_COLOR_SCORE_THRESHOLD
                    and detected
                )
            elif modality == "text":
                foreground_iou = _finite_metric(
                    metrics.get("foregroundIou"), f"{context} foreground IoU"
                )
                bounds_score = _finite_metric(
                    metrics.get("boundsScore"), f"{context} bounds score"
                )
                mean_bounds_error = _finite_metric(
                    metrics.get("meanBoundsErrorRatio"), f"{context} bounds error"
                )
                raster_tolerance_px = metrics.get("rasterTolerancePx")
                if (
                    not isinstance(raster_tolerance_px, int)
                    or isinstance(raster_tolerance_px, bool)
                    or raster_tolerance_px < 1
                ):
                    raise CapabilityVerificationError(
                        f"{context} raster tolerance must be a positive integer"
                    )
                reference_coverage = _finite_metric(
                    metrics.get("referenceCoverageAtTolerance"),
                    f"{context} reference coverage at tolerance",
                )
                candidate_coverage = _finite_metric(
                    metrics.get("candidateCoverageAtTolerance"),
                    f"{context} candidate coverage at tolerance",
                )
                tolerant_foreground_f1 = _finite_metric(
                    metrics.get("tolerantForegroundF1"),
                    f"{context} tolerant foreground F1",
                )
                tolerant_bounds_score = _finite_metric(
                    metrics.get("tolerantBoundsScore"),
                    f"{context} tolerant bounds score",
                )
                tolerant_mean_bounds_error = _finite_metric(
                    metrics.get("tolerantMeanBoundsErrorRatio"),
                    f"{context} tolerant bounds error",
                )
                ink_coverage_ratio = _finite_metric(
                    metrics.get("inkCoverageRatio"), f"{context} ink coverage ratio"
                )
                reference_ink_density = _finite_metric(
                    metrics.get("referenceInkDensity"), f"{context} reference ink density"
                )
                candidate_ink_density = _finite_metric(
                    metrics.get("candidateInkDensity"), f"{context} candidate ink density"
                )
                if (
                    not 0 <= foreground_iou <= 1
                    or not 0 <= bounds_score <= 1
                    or mean_bounds_error < 0
                    or not 0 <= reference_coverage <= 1
                    or not 0 <= candidate_coverage <= 1
                    or not 0 <= tolerant_foreground_f1 <= 1
                    or not 0 <= tolerant_bounds_score <= 1
                    or tolerant_mean_bounds_error < 0
                    or not 0 <= ink_coverage_ratio <= 1
                    or reference_ink_density <= 0
                    or candidate_ink_density <= 0
                ):
                    raise CapabilityVerificationError(
                        f"{context} metrics are outside their domains"
                    )
                expected_tolerant_foreground_f1 = (
                    2
                    * reference_coverage
                    * candidate_coverage
                    / (reference_coverage + candidate_coverage)
                    if reference_coverage + candidate_coverage > 0
                    else 0.0
                )
                if (
                    abs(tolerant_foreground_f1 - expected_tolerant_foreground_f1) > 1e-9
                    or abs(
                        tolerant_bounds_score
                        - max(0.0, 1.0 - tolerant_mean_bounds_error)
                    )
                    > 1e-9
                ):
                    raise CapabilityVerificationError(
                        f"{context} tolerant metrics are inconsistent"
                    )
                for kind in ("reference", "candidate"):
                    bounds = metrics.get(f"{kind}Bounds")
                    if (
                        not isinstance(bounds, list)
                        or len(bounds) != 4
                        or any(
                            not isinstance(coordinate, (int, float))
                            or isinstance(coordinate, bool)
                            or not math.isfinite(coordinate)
                            or coordinate < 0
                            for coordinate in bounds
                        )
                        or bounds[0] > bounds[2]
                        or bounds[1] > bounds[3]
                    ):
                        raise CapabilityVerificationError(
                            f"{context} {kind} bounds are invalid"
                        )
                expected_pass = (
                    tolerant_foreground_f1
                    >= CAMERA_TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD
                    and tolerant_bounds_score >= CAMERA_TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD
                    and ink_coverage_ratio >= CAMERA_TEXT_INK_COVERAGE_RATIO_THRESHOLD
                )
            else:
                corner_score = _finite_metric(
                    metrics.get("cornerScore"), f"{context} picture corner score"
                )
                mean_corner_error = _finite_metric(
                    metrics.get("meanCornerErrorRatio"),
                    f"{context} picture corner error",
                )
                rectified_color_score = _finite_metric(
                    metrics.get("rectifiedColorScore"),
                    f"{context} rectified picture color score",
                )
                rectified_edge_f1 = _finite_metric(
                    metrics.get("rectifiedEdgeF1"),
                    f"{context} rectified picture edge F1",
                )
                reference_edge_coverage = _finite_metric(
                    metrics.get("referenceEdgeCoverageAtTolerance"),
                    f"{context} reference picture edge coverage",
                )
                candidate_edge_coverage = _finite_metric(
                    metrics.get("candidateEdgeCoverageAtTolerance"),
                    f"{context} candidate picture edge coverage",
                )
                edge_tolerance_px = metrics.get("edgeTolerancePx")
                rectified_size = metrics.get("rectifiedSize")
                if (
                    not 0 <= corner_score <= 1
                    or mean_corner_error < 0
                    or not 0 <= rectified_color_score <= 1
                    or not 0 <= rectified_edge_f1 <= 1
                    or not 0 <= reference_edge_coverage <= 1
                    or not 0 <= candidate_edge_coverage <= 1
                ):
                    raise CapabilityVerificationError(
                        f"{context} metrics are outside their domains"
                    )
                if (
                    not isinstance(edge_tolerance_px, int)
                    or isinstance(edge_tolerance_px, bool)
                    or edge_tolerance_px < 1
                    or edge_tolerance_px
                    != max(
                        1,
                        round(
                            CAMERA_PICTURE_RECTIFIED_SIZE
                            * CAMERA_PICTURE_EDGE_TOLERANCE_RATIO
                        ),
                    )
                    or rectified_size != CAMERA_PICTURE_RECTIFIED_SIZE
                ):
                    raise CapabilityVerificationError(
                        f"{context} picture raster dimensions are invalid"
                    )
                expected_corner_score = max(0.0, 1.0 - mean_corner_error)
                expected_edge_f1 = (
                    2
                    * reference_edge_coverage
                    * candidate_edge_coverage
                    / (reference_edge_coverage + candidate_edge_coverage)
                    if reference_edge_coverage + candidate_edge_coverage > 0
                    else 0.0
                )
                if abs(corner_score - expected_corner_score) > 1e-9:
                    raise CapabilityVerificationError(
                        f"{context} picture corner metrics are inconsistent"
                    )
                if abs(rectified_edge_f1 - expected_edge_f1) > 1e-9:
                    raise CapabilityVerificationError(
                        f"{context} picture edge metrics are inconsistent"
                    )
                crop_sensitivity = _mapping(
                    metrics.get("cropSensitivity"),
                    f"{context} crop sensitivity",
                )
                if (
                    crop_sensitivity.get("mutation") != "left-crop-and-rescale"
                    or crop_sensitivity.get("cropRatio")
                    != CAMERA_PICTURE_CROP_MUTATION_RATIO
                ):
                    raise CapabilityVerificationError(
                        f"{context} crop sensitivity is inconsistent"
                    )
                mutated_color_score = _finite_metric(
                    crop_sensitivity.get("mutatedRectifiedColorScore"),
                    f"{context} mutated picture color score",
                )
                mutated_edge_f1 = _finite_metric(
                    crop_sensitivity.get("mutatedRectifiedEdgeF1"),
                    f"{context} mutated picture edge F1",
                )
                mutated_reference_coverage = _finite_metric(
                    crop_sensitivity.get("mutatedReferenceEdgeCoverageAtTolerance"),
                    f"{context} mutated reference picture edge coverage",
                )
                mutated_candidate_coverage = _finite_metric(
                    crop_sensitivity.get("mutatedCandidateEdgeCoverageAtTolerance"),
                    f"{context} mutated candidate picture edge coverage",
                )
                mutated_passed = crop_sensitivity.get("mutatedPassed")
                detected = crop_sensitivity.get("detected")
                if (
                    not 0 <= mutated_color_score <= 1
                    or not 0 <= mutated_edge_f1 <= 1
                    or not 0 <= mutated_reference_coverage <= 1
                    or not 0 <= mutated_candidate_coverage <= 1
                    or not isinstance(mutated_passed, bool)
                    or not isinstance(detected, bool)
                ):
                    raise CapabilityVerificationError(
                        f"{context} crop sensitivity is outside its domain"
                    )
                expected_mutated_edge_f1 = (
                    2
                    * mutated_reference_coverage
                    * mutated_candidate_coverage
                    / (mutated_reference_coverage + mutated_candidate_coverage)
                    if mutated_reference_coverage + mutated_candidate_coverage > 0
                    else 0.0
                )
                expected_mutated_passed = (
                    mutated_color_score
                    >= CAMERA_PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD
                    and mutated_edge_f1 >= CAMERA_PICTURE_RECTIFIED_EDGE_F1_THRESHOLD
                )
                if (
                    abs(mutated_edge_f1 - expected_mutated_edge_f1) > 1e-9
                    or mutated_passed is not expected_mutated_passed
                    or detected is not (not expected_mutated_passed)
                    or detected is not True
                ):
                    raise CapabilityVerificationError(
                        f"{context} crop sensitivity is inconsistent"
                    )
                expected_pass = (
                    corner_score >= CAMERA_PICTURE_CORNER_SCORE_THRESHOLD
                    and rectified_color_score
                    >= CAMERA_PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD
                    and rectified_edge_f1
                    >= CAMERA_PICTURE_RECTIFIED_EDGE_F1_THRESHOLD
                    and detected
                )
            if (
                metrics.get("passed") is not expected_pass
                or slide.get("passed") is not expected_pass
            ):
                raise CapabilityVerificationError(f"{context} metric pass status is inconsistent")
            slide_passes.append(expected_pass)

        applicable = value.get("applicable")
        if not isinstance(applicable, bool) or applicable is not bool(slides):
            raise CapabilityVerificationError(
                f"{case_id} camera-local applicability is inconsistent"
            )
        if applicable:
            applicable_count += 1
        case_passed = applicable and all(slide_passes)
        if value.get("passed") is not case_passed:
            raise CapabilityVerificationError(f"{case_id} camera-local pass status is inconsistent")
        all_cases_passed = all_cases_passed and case_passed
        if not case_passed:
            raise CapabilityVerificationError(f"{case_id} camera-local report failed")
    if applicable_count < 1 or report.get("applicableCaseCount") != applicable_count:
        raise CapabilityVerificationError("camera-local report requires applicable case evidence")
    if report.get("passed") is not all_cases_passed or report.get("passed") is not True:
        raise CapabilityVerificationError("camera-local report failed")


def _validate_shadow_local(
    report: Mapping[str, Any],
    current: Mapping[str, Mapping[str, Any]],
    current_revision: str,
    repo: Path,
) -> None:
    if report.get("schemaVersion") != 1:
        raise CapabilityVerificationError("shadow-local report requires schemaVersion=1")
    renderer = _mapping(report.get("renderer"), "shadow-local renderer")
    if renderer.get("revision") != current_revision or renderer.get("dirty") is not False:
        raise CapabilityVerificationError(
            "shadow-local report must match the clean native-report revision"
        )
    expected_thresholds = {
        "ringInnerRatio": SHADOW_RING_INNER_RATIO,
        "ringOuterRatio": SHADOW_RING_OUTER_RATIO,
        "backgroundNoiseFloor": SHADOW_BACKGROUND_NOISE_FLOOR,
        "minimumReferenceShadowDensity": SHADOW_MINIMUM_REFERENCE_DENSITY,
        "invisibleShadowDensity": SHADOW_INVISIBLE_DENSITY_THRESHOLD,
        "shadowEnergyRatio": SHADOW_ENERGY_RATIO_THRESHOLD,
        "shadowOvershootRatio": SHADOW_OVERSHOOT_RATIO_THRESHOLD,
        "shadowFieldCosine": SHADOW_FIELD_COSINE_THRESHOLD,
        "shadowFieldIou": SHADOW_FIELD_IOU_THRESHOLD,
        "shadowFieldError": SHADOW_FIELD_ERROR_THRESHOLD,
        "shadowCentroidErrorRatio": SHADOW_CENTROID_ERROR_RATIO_THRESHOLD,
        "shadowFieldBinaryThreshold": SHADOW_FIELD_BINARY_THRESHOLD,
    }
    thresholds = _mapping(report.get("thresholds"), "shadow-local thresholds")
    if thresholds != expected_thresholds:
        raise CapabilityVerificationError("shadow-local report uses unexpected thresholds")

    values = report.get("caseResults")
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise CapabilityVerificationError("shadow-local caseResults must be a list of objects")
    by_case: dict[str, Mapping[str, Any]] = {}
    for value in values:
        case_id = value.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in by_case:
            raise CapabilityVerificationError("shadow-local case IDs must be unique strings")
        by_case[case_id] = value
    if set(by_case) != set(current):
        raise CapabilityVerificationError("shadow-local case IDs must match native reports")

    applicable_count = 0
    all_cases_passed = True
    for case_id, value in by_case.items():
        source_hash, ground_truth_hash = _case_hashes(current[case_id], case_id)
        source_shadow_slides = _source_outer_shadow_slide_indices(
            current[case_id], case_id, source_hash, repo
        )
        if value.get("sourceSha256") != source_hash or value.get(
            "groundTruthSha256"
        ) != ground_truth_hash:
            raise CapabilityVerificationError(
                f"{case_id} shadow-local input hashes must match native reports"
            )
        slides = value.get("slides")
        if not isinstance(slides, list) or any(not isinstance(slide, Mapping) for slide in slides):
            raise CapabilityVerificationError(f"{case_id} shadow-local slides must be objects")
        native_slide_values = current[case_id].get("perSlide")
        if not isinstance(native_slide_values, list) or any(
            not isinstance(slide, Mapping) for slide in native_slide_values
        ):
            raise CapabilityVerificationError(
                f"{case_id} native report is missing per-slide artifacts"
            )
        native_slides = {
            slide.get("slideIdx"): slide
            for slide in native_slide_values
            if isinstance(slide.get("slideIdx"), int) and slide.get("hidden") is not True
        }
        seen_slide_indices: set[int] = set()
        slide_passes: list[bool] = []
        shadow_required_count = 0
        for ordinal, slide in enumerate(slides):
            context = f"{case_id} shadow-local slide {ordinal}"
            source_slide_index = slide.get("slideIdx")
            if (
                not isinstance(source_slide_index, int)
                or isinstance(source_slide_index, bool)
                or source_slide_index < 0
                or source_slide_index in seen_slide_indices
            ):
                raise CapabilityVerificationError(f"{context} index is invalid or duplicated")
            seen_slide_indices.add(source_slide_index)
            native_slide = native_slides.get(source_slide_index)
            if native_slide is None:
                raise CapabilityVerificationError(f"{context} is absent from the native report")
            native_artifacts = _mapping(
                native_slide.get("renderArtifacts"), f"{context} native render artifacts"
            )
            for kind in ("reference", "candidate"):
                path_value = slide.get(f"{kind}Path")
                expected_hash = _sha256(slide.get(f"{kind}Sha256"), f"{context} {kind}")
                native_artifact = _mapping(
                    native_artifacts.get(kind), f"{context} native {kind} artifact"
                )
                if (
                    native_artifact.get("path") != path_value
                    or native_artifact.get("sha256") != expected_hash
                ):
                    raise CapabilityVerificationError(
                        f"{context} does not match native report artifacts"
                    )
                if not isinstance(path_value, str) or "\\" in path_value:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                relative = PurePosixPath(path_value)
                if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                artifact = (repo / relative.as_posix()).resolve()
                try:
                    artifact.relative_to(repo.resolve())
                except ValueError as error:
                    raise CapabilityVerificationError(
                        f"{context} {kind} path escapes the repository"
                    ) from error
                if not artifact.is_file():
                    raise CapabilityVerificationError(f"{context} {kind} artifact is missing")
                digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if digest != expected_hash:
                    raise CapabilityVerificationError(f"{context} {kind} artifact hash changed")

            shadow_required = slide.get("shadowRequired")
            if not isinstance(shadow_required, bool):
                raise CapabilityVerificationError(f"{context} shadow requirement is invalid")
            if shadow_required is not (source_slide_index in source_shadow_slides):
                raise CapabilityVerificationError(
                    f"{context} shadow requirement does not match source OOXML"
                )
            if shadow_required:
                shadow_required_count += 1
            metrics = _mapping(slide.get("metrics"), f"{context} metrics")
            if metrics.get("shadowRequired") is not shadow_required:
                raise CapabilityVerificationError(f"{context} shadow requirement is inconsistent")
            measurable = metrics.get("shadowMeasurable")
            if not isinstance(measurable, bool):
                raise CapabilityVerificationError(f"{context} measurability is invalid")
            reference_density = _finite_metric(
                metrics.get("referenceShadowDensity"), f"{context} reference density"
            )
            candidate_density = _finite_metric(
                metrics.get("candidateShadowDensity"), f"{context} candidate density"
            )
            energy_ratio = _finite_metric(
                metrics.get("shadowEnergyRatio"), f"{context} energy ratio"
            )
            overshoot_ratio = _finite_metric(
                metrics.get("shadowOvershootRatio"), f"{context} overshoot ratio"
            )
            field_cosine = _finite_metric(
                metrics.get("shadowFieldCosine"), f"{context} field cosine"
            )
            field_iou = _finite_metric(metrics.get("shadowFieldIou"), f"{context} field IoU")
            field_error = _finite_metric(
                metrics.get("shadowFieldError"), f"{context} field error"
            )
            centroid_error = _finite_metric(
                metrics.get("shadowCentroidErrorRatio"), f"{context} centroid error"
            )
            ring_pixels = metrics.get("shadowRingPixels")
            ring_inner = metrics.get("shadowRingInnerPx")
            ring_outer = metrics.get("shadowRingOuterPx")
            if (
                reference_density < 0
                or candidate_density < 0
                or not 0 <= energy_ratio <= 1
                or overshoot_ratio < 0
                or not 0 <= field_cosine <= 1
                or not 0 <= field_iou <= 1
                or not 0 <= field_error <= 1
                or centroid_error < 0
                or not isinstance(ring_pixels, int)
                or isinstance(ring_pixels, bool)
                or ring_pixels < 64
                or not isinstance(ring_inner, int)
                or isinstance(ring_inner, bool)
                or ring_inner < 1
                or not isinstance(ring_outer, int)
                or isinstance(ring_outer, bool)
                or ring_outer <= ring_inner
            ):
                raise CapabilityVerificationError(f"{context} metrics are outside their domains")
            maximum_density = max(reference_density, candidate_density)
            expected_energy_ratio = (
                min(reference_density, candidate_density) / maximum_density
                if maximum_density > 1e-9
                else 1.0
            )
            expected_overshoot = (
                candidate_density / reference_density
                if reference_density > 1e-9
                else 1.0 + candidate_density
            )
            expected_measurable = (
                shadow_required and reference_density >= SHADOW_MINIMUM_REFERENCE_DENSITY
            )
            if (
                abs(energy_ratio - expected_energy_ratio) > 1e-9
                or abs(overshoot_ratio - expected_overshoot) > 1e-9
                or measurable is not expected_measurable
            ):
                raise CapabilityVerificationError(f"{context} metrics are inconsistent")
            if _mapping(metrics.get("thresholds"), f"{context} thresholds") != expected_thresholds:
                raise CapabilityVerificationError(f"{context} uses unexpected thresholds")

            sensitivity = _mapping(
                metrics.get("shadowSensitivity"), f"{context} sensitivity"
            )
            if (
                sensitivity.get("mutation") != "erase-exterior-shadow"
                or sensitivity.get("applicable") is not measurable
            ):
                raise CapabilityVerificationError(f"{context} sensitivity is inconsistent")
            sensitivity_detected = sensitivity.get("detected")
            if measurable:
                mutated_density = _finite_metric(
                    sensitivity.get("mutatedCandidateShadowDensity"),
                    f"{context} mutated candidate density",
                )
                mutated_energy_ratio = _finite_metric(
                    sensitivity.get("mutatedShadowEnergyRatio"),
                    f"{context} mutated energy ratio",
                )
                mutated_passed = sensitivity.get("mutatedShadowPassed")
                mutated_maximum_density = max(reference_density, mutated_density)
                expected_mutated_energy_ratio = (
                    min(reference_density, mutated_density) / mutated_maximum_density
                    if mutated_maximum_density > 1e-9
                    else 1.0
                )
                if (
                    mutated_density < 0
                    or not 0 <= mutated_energy_ratio <= 1
                    or abs(mutated_energy_ratio - expected_mutated_energy_ratio) > 1e-9
                    or mutated_density > SHADOW_INVISIBLE_DENSITY_THRESHOLD
                    or mutated_energy_ratio >= SHADOW_ENERGY_RATIO_THRESHOLD
                    or mutated_passed is not False
                    or sensitivity_detected is not True
                ):
                    raise CapabilityVerificationError(f"{context} sensitivity is invalid")
            elif sensitivity_detected is not None:
                raise CapabilityVerificationError(f"{context} sensitivity is invalid")

            if measurable:
                expected_pass = (
                    energy_ratio >= SHADOW_ENERGY_RATIO_THRESHOLD
                    and overshoot_ratio <= SHADOW_OVERSHOOT_RATIO_THRESHOLD
                    and field_cosine >= SHADOW_FIELD_COSINE_THRESHOLD
                    and field_iou >= SHADOW_FIELD_IOU_THRESHOLD
                    and field_error <= SHADOW_FIELD_ERROR_THRESHOLD
                    and centroid_error <= SHADOW_CENTROID_ERROR_RATIO_THRESHOLD
                    and sensitivity_detected is True
                )
            else:
                invisible_limit = max(
                    SHADOW_INVISIBLE_DENSITY_THRESHOLD,
                    reference_density * SHADOW_OVERSHOOT_RATIO_THRESHOLD,
                )
                expected_pass = candidate_density <= invisible_limit
            if (
                metrics.get("passed") is not expected_pass
                or slide.get("passed") is not expected_pass
            ):
                raise CapabilityVerificationError(f"{context} pass status is inconsistent")
            slide_passes.append(expected_pass)

        if seen_slide_indices != set(native_slides):
            raise CapabilityVerificationError(
                f"{case_id} shadow-local slides must match visible native slides"
            )
        applicable = shadow_required_count > 0
        if value.get("applicable") is not applicable:
            raise CapabilityVerificationError(
                f"{case_id} shadow-local applicability is inconsistent"
            )
        if applicable:
            applicable_count += 1
        case_passed = applicable and all(slide_passes)
        if value.get("passed") is not case_passed:
            raise CapabilityVerificationError(
                f"{case_id} shadow-local pass status is inconsistent"
            )
        all_cases_passed = all_cases_passed and case_passed
        if not case_passed:
            raise CapabilityVerificationError(f"{case_id} shadow-local report failed")
    if applicable_count < 1 or report.get("applicableCaseCount") != applicable_count:
        raise CapabilityVerificationError("shadow-local report requires applicable case evidence")
    if report.get("passed") is not all_cases_passed or report.get("passed") is not True:
        raise CapabilityVerificationError("shadow-local report failed")


def _validate_reflection_local(
    report: Mapping[str, Any],
    current: Mapping[str, Mapping[str, Any]],
    current_revision: str,
    repo: Path,
) -> None:
    if report.get("schemaVersion") != 1:
        raise CapabilityVerificationError("reflection-local report requires schemaVersion=1")
    renderer = _mapping(report.get("renderer"), "reflection-local renderer")
    if renderer.get("revision") != current_revision or renderer.get("dirty") is not False:
        raise CapabilityVerificationError(
            "reflection-local report must match the clean native-report revision"
        )
    expected_thresholds = {
        "blurPadMultiplier": REFLECTION_BLUR_PAD_MULTIPLIER,
        "backgroundNoiseFloor": REFLECTION_BACKGROUND_NOISE_FLOOR,
        "minimumReferenceReflectionDensity": REFLECTION_MINIMUM_REFERENCE_DENSITY,
        "invisibleReflectionDensity": REFLECTION_INVISIBLE_DENSITY_THRESHOLD,
        "reflectionEnergyRatio": REFLECTION_ENERGY_RATIO_THRESHOLD,
        "reflectionOvershootRatio": REFLECTION_OVERSHOOT_RATIO_THRESHOLD,
        "reflectionFieldCosine": REFLECTION_FIELD_COSINE_THRESHOLD,
        "reflectionFieldIou": REFLECTION_FIELD_IOU_THRESHOLD,
        "reflectionFieldError": REFLECTION_FIELD_ERROR_THRESHOLD,
        "reflectionCentroidErrorRatio": REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD,
        "reflectionFieldBinaryThreshold": REFLECTION_FIELD_BINARY_THRESHOLD,
    }
    thresholds = _mapping(report.get("thresholds"), "reflection-local thresholds")
    if thresholds != expected_thresholds:
        raise CapabilityVerificationError("reflection-local report uses unexpected thresholds")

    values = report.get("caseResults")
    if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
        raise CapabilityVerificationError("reflection-local caseResults must be a list of objects")
    by_case: dict[str, Mapping[str, Any]] = {}
    for value in values:
        case_id = value.get("caseId")
        if not isinstance(case_id, str) or not case_id or case_id in by_case:
            raise CapabilityVerificationError("reflection-local case IDs must be unique strings")
        by_case[case_id] = value
    if set(by_case) != set(current):
        raise CapabilityVerificationError("reflection-local case IDs must match native reports")

    applicable_count = 0
    all_cases_passed = True
    for case_id, value in by_case.items():
        source_hash, ground_truth_hash = _case_hashes(current[case_id], case_id)
        source_reflection_slides = _source_reflection_slide_indices(
            current[case_id], case_id, source_hash, repo
        )
        if value.get("sourceSha256") != source_hash or value.get(
            "groundTruthSha256"
        ) != ground_truth_hash:
            raise CapabilityVerificationError(
                f"{case_id} reflection-local input hashes must match native reports"
            )
        slides = value.get("slides")
        if not isinstance(slides, list) or any(not isinstance(slide, Mapping) for slide in slides):
            raise CapabilityVerificationError(f"{case_id} reflection-local slides must be objects")
        native_slide_values = current[case_id].get("perSlide")
        if not isinstance(native_slide_values, list) or any(
            not isinstance(slide, Mapping) for slide in native_slide_values
        ):
            raise CapabilityVerificationError(
                f"{case_id} native report is missing per-slide artifacts"
            )
        native_slides = {
            slide.get("slideIdx"): slide
            for slide in native_slide_values
            if isinstance(slide.get("slideIdx"), int) and slide.get("hidden") is not True
        }
        seen_slide_indices: set[int] = set()
        slide_passes: list[bool] = []
        for ordinal, slide in enumerate(slides):
            context = f"{case_id} reflection-local slide {ordinal}"
            source_slide_index = slide.get("slideIdx")
            if (
                not isinstance(source_slide_index, int)
                or isinstance(source_slide_index, bool)
                or source_slide_index < 0
                or source_slide_index in seen_slide_indices
            ):
                raise CapabilityVerificationError(f"{context} index is invalid or duplicated")
            seen_slide_indices.add(source_slide_index)
            if source_slide_index not in source_reflection_slides:
                raise CapabilityVerificationError(
                    f"{context} reflection requirement does not match source OOXML"
                )
            native_slide = native_slides.get(source_slide_index)
            if native_slide is None:
                raise CapabilityVerificationError(f"{context} is absent from the native report")
            native_artifacts = _mapping(
                native_slide.get("renderArtifacts"), f"{context} native render artifacts"
            )
            for kind in ("reference", "candidate"):
                path_value = slide.get(f"{kind}Path")
                expected_hash = _sha256(slide.get(f"{kind}Sha256"), f"{context} {kind}")
                native_artifact = _mapping(
                    native_artifacts.get(kind), f"{context} native {kind} artifact"
                )
                if (
                    native_artifact.get("path") != path_value
                    or native_artifact.get("sha256") != expected_hash
                ):
                    raise CapabilityVerificationError(
                        f"{context} does not match native report artifacts"
                    )
                if not isinstance(path_value, str) or "\\" in path_value:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                relative = PurePosixPath(path_value)
                if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
                    raise CapabilityVerificationError(f"{context} {kind} path is invalid")
                artifact = (repo / relative.as_posix()).resolve()
                try:
                    artifact.relative_to(repo.resolve())
                except ValueError as error:
                    raise CapabilityVerificationError(
                        f"{context} {kind} path escapes the repository"
                    ) from error
                if not artifact.is_file():
                    raise CapabilityVerificationError(f"{context} {kind} artifact is missing")
                if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_hash:
                    raise CapabilityVerificationError(f"{context} {kind} artifact hash changed")

            if slide.get("reflectionRequired") is not True:
                raise CapabilityVerificationError(f"{context} reflection requirement is invalid")
            regions = slide.get("regions")
            if not isinstance(regions, list) or not regions:
                raise CapabilityVerificationError(f"{context} regions must be non-empty")
            for region_index, region_value in enumerate(regions):
                region = _mapping(region_value, f"{context} region {region_index}")
                if set(region) != {"x0", "y0", "x1", "y1"}:
                    raise CapabilityVerificationError(f"{context} region keys are invalid")
                x0 = _finite_metric(region.get("x0"), f"{context} region x0")
                y0 = _finite_metric(region.get("y0"), f"{context} region y0")
                x1 = _finite_metric(region.get("x1"), f"{context} region x1")
                y1 = _finite_metric(region.get("y1"), f"{context} region y1")
                if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                    raise CapabilityVerificationError(f"{context} region is outside slide bounds")

            metrics = _mapping(slide.get("metrics"), f"{context} metrics")
            if metrics.get("reflectionRequired") is not True:
                raise CapabilityVerificationError(f"{context} reflection requirement is inconsistent")
            measurable = metrics.get("reflectionMeasurable")
            if measurable is not True:
                raise CapabilityVerificationError(f"{context} reflection must be measurable")
            reference_density = _finite_metric(
                metrics.get("referenceReflectionDensity"), f"{context} reference density"
            )
            candidate_density = _finite_metric(
                metrics.get("candidateReflectionDensity"), f"{context} candidate density"
            )
            energy_ratio = _finite_metric(
                metrics.get("reflectionEnergyRatio"), f"{context} energy ratio"
            )
            overshoot_ratio = _finite_metric(
                metrics.get("reflectionOvershootRatio"), f"{context} overshoot ratio"
            )
            field_cosine = _finite_metric(
                metrics.get("reflectionFieldCosine"), f"{context} field cosine"
            )
            field_iou = _finite_metric(
                metrics.get("reflectionFieldIou"), f"{context} field IoU"
            )
            field_error = _finite_metric(
                metrics.get("reflectionFieldError"), f"{context} field error"
            )
            centroid_error = _finite_metric(
                metrics.get("reflectionCentroidErrorRatio"), f"{context} centroid error"
            )
            region_pixels = metrics.get("reflectionRegionPixels")
            if (
                reference_density < REFLECTION_MINIMUM_REFERENCE_DENSITY
                or candidate_density < 0
                or not 0 <= energy_ratio <= 1
                or overshoot_ratio < 0
                or not 0 <= field_cosine <= 1
                or not 0 <= field_iou <= 1
                or not 0 <= field_error <= 1
                or centroid_error < 0
                or not isinstance(region_pixels, int)
                or isinstance(region_pixels, bool)
                or region_pixels < 64
            ):
                raise CapabilityVerificationError(f"{context} metrics are outside their domains")
            maximum_density = max(reference_density, candidate_density)
            expected_energy_ratio = (
                min(reference_density, candidate_density) / maximum_density
                if maximum_density > 1e-9
                else 1.0
            )
            expected_overshoot = candidate_density / reference_density
            if (
                abs(energy_ratio - expected_energy_ratio) > 1e-9
                or abs(overshoot_ratio - expected_overshoot) > 1e-9
            ):
                raise CapabilityVerificationError(f"{context} metrics are inconsistent")
            if _mapping(metrics.get("thresholds"), f"{context} thresholds") != expected_thresholds:
                raise CapabilityVerificationError(f"{context} uses unexpected thresholds")

            sensitivity = _mapping(
                metrics.get("reflectionSensitivity"), f"{context} sensitivity"
            )
            if (
                sensitivity.get("mutation") != "erase-reflection-region"
                or sensitivity.get("applicable") is not True
            ):
                raise CapabilityVerificationError(f"{context} sensitivity is inconsistent")
            mutated_density = _finite_metric(
                sensitivity.get("mutatedCandidateReflectionDensity"),
                f"{context} mutated candidate density",
            )
            mutated_energy_ratio = _finite_metric(
                sensitivity.get("mutatedReflectionEnergyRatio"),
                f"{context} mutated energy ratio",
            )
            mutated_passed = sensitivity.get("mutatedReflectionPassed")
            expected_mutated_energy_ratio = (
                min(reference_density, mutated_density) / max(reference_density, mutated_density)
                if max(reference_density, mutated_density) > 1e-9
                else 1.0
            )
            if (
                mutated_density < 0
                or not 0 <= mutated_energy_ratio <= 1
                or abs(mutated_energy_ratio - expected_mutated_energy_ratio) > 1e-9
                or mutated_density > REFLECTION_INVISIBLE_DENSITY_THRESHOLD
                or mutated_energy_ratio >= REFLECTION_ENERGY_RATIO_THRESHOLD
                or mutated_passed is not False
                or sensitivity.get("detected") is not True
            ):
                raise CapabilityVerificationError(f"{context} sensitivity is invalid")

            expected_pass = (
                energy_ratio >= REFLECTION_ENERGY_RATIO_THRESHOLD
                and overshoot_ratio <= REFLECTION_OVERSHOOT_RATIO_THRESHOLD
                and field_cosine >= REFLECTION_FIELD_COSINE_THRESHOLD
                and field_iou >= REFLECTION_FIELD_IOU_THRESHOLD
                and field_error <= REFLECTION_FIELD_ERROR_THRESHOLD
                and centroid_error <= REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD
                and sensitivity.get("detected") is True
            )
            if (
                metrics.get("passed") is not expected_pass
                or slide.get("passed") is not expected_pass
            ):
                raise CapabilityVerificationError(f"{context} pass status is inconsistent")
            slide_passes.append(expected_pass)

        if seen_slide_indices != source_reflection_slides:
            raise CapabilityVerificationError(
                f"{case_id} reflection-local slides must match source OOXML"
            )
        applicable = bool(source_reflection_slides)
        if value.get("applicable") is not applicable:
            raise CapabilityVerificationError(
                f"{case_id} reflection-local applicability is inconsistent"
            )
        if applicable:
            applicable_count += 1
        case_passed = applicable and bool(slide_passes) and all(slide_passes)
        if value.get("passed") is not case_passed:
            raise CapabilityVerificationError(
                f"{case_id} reflection-local pass status is inconsistent"
            )
        all_cases_passed = all_cases_passed and case_passed
        if not case_passed:
            raise CapabilityVerificationError(f"{case_id} reflection-local report failed")
    if applicable_count < 1 or report.get("applicableCaseCount") != applicable_count:
        raise CapabilityVerificationError("reflection-local report requires applicable case evidence")
    if report.get("passed") is not all_cases_passed or report.get("passed") is not True:
        raise CapabilityVerificationError("reflection-local report failed")


def normalize_native_evaluation_reports(
    capability: CapabilityDefinition,
    reports: Sequence[Mapping[str, Any]],
    repo: Path,
    *,
    oracle: str,
    baseline_reports: Sequence[Mapping[str, Any]] = (),
    passed_gates: Iterable[str] = (),
    manual_verdicts: Mapping[str, str] | None = None,
    bevel_report: Mapping[str, Any] | None = None,
    camera_report: Mapping[str, Any] | None = None,
    shadow_report: Mapping[str, Any] | None = None,
    reflection_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if oracle not in {"powerpoint-macos", "powerpoint-windows"}:
        raise CapabilityVerificationError(
            "oracle must identify native PowerPoint on macOS or Windows"
        )
    supplied_gates = tuple(passed_gates)
    if len(supplied_gates) != len(set(supplied_gates)):
        raise CapabilityVerificationError("passed gates must be unique")
    unknown_gates = set(supplied_gates) - set(capability.required_gates)
    if unknown_gates:
        raise CapabilityVerificationError(
            f"passed gate is not required by capability: {', '.join(sorted(unknown_gates))}"
        )
    derived = set(supplied_gates) & DERIVED_GATES
    if derived:
        raise CapabilityVerificationError(
            f"derived gate cannot be self-attested: {', '.join(sorted(derived))}"
        )

    current = _reports_by_case(reports, "native verification")
    verdicts = dict(manual_verdicts or {})
    unknown_verdicts = set(verdicts) - set(current)
    if unknown_verdicts:
        raise CapabilityVerificationError(
            f"manual verdict references unknown case: {', '.join(sorted(unknown_verdicts))}"
        )
    revisions = {_renderer_state(report, case_id)[0] for case_id, report in current.items()}
    if len(revisions) != 1:
        raise CapabilityVerificationError("native reports must use one renderer revision")
    runtime_environments = {
        _environment_key(report, case_id) for case_id, report in current.items()
    }
    if len(runtime_environments) != 1:
        raise CapabilityVerificationError("native reports must use one runtime environment")

    case_results = tuple(
        _case_result(current[case_id], verdicts) for case_id in sorted(current)
    )
    required = set(capability.required_gates)
    if "regression" in required:
        _validate_regression(current, baseline_reports, next(iter(revisions)))
    bevel_local_passed = False
    if bevel_report is not None:
        _validate_bevel_local(bevel_report, current, next(iter(revisions)), repo)
        bevel_local_passed = True
    camera_local_passed = False
    if camera_report is not None:
        _validate_camera_local(camera_report, current, next(iter(revisions)), repo)
        camera_local_passed = True
    shadow_local_passed = False
    if shadow_report is not None:
        _validate_shadow_local(shadow_report, current, next(iter(revisions)), repo)
        shadow_local_passed = True
    reflection_local_passed = False
    if reflection_report is not None:
        _validate_reflection_local(reflection_report, current, next(iter(revisions)), repo)
        reflection_local_passed = True
    gates = {
        gate: (
            "passed"
            if gate in supplied_gates
            or (gate == "native-powerpoint" and all(case["passed"] for case in case_results))
            or (gate == "manual-visual")
            or (gate == "regression" and baseline_reports)
            or (gate == "bevel-local" and bevel_local_passed)
            or (gate == "camera-local" and camera_local_passed)
            or (gate == "shadow-local" and shadow_local_passed)
            or (gate == "reflection-local" and reflection_local_passed)
            else "failed"
            if gate == "native-powerpoint"
            else "missing"
        )
        for gate in capability.required_gates
    }
    first_case = sorted(current)[0]
    runtime = dict(_runtime_environment(current[first_case], first_case))
    environment = {
        "oracle": oracle,
        "platform": runtime.get("platform"),
        "python": runtime.get("python"),
        "browser": runtime.get("browser"),
        "capture": runtime.get("capture"),
        "fontProfile": runtime.get("fontProfile"),
    }
    return {
        "schemaVersion": 2,
        "capabilityId": capability.id,
        "renderer": {
            "revision": next(iter(revisions)),
            "dirty": False,
        },
        "environment": environment,
        "gates": gates,
        "caseResults": list(case_results),
    }
