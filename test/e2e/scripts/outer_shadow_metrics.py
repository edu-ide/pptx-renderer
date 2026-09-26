#!/usr/bin/env python3
"""Measure bounded ordinary-shape outer-shadow fidelity against native PowerPoint rasters."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import ZipFile

import cv2
import numpy as np
from lxml import etree
from PIL import Image


E2E_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = E2E_DIR.parents[1]

PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": PML_NS, "a": DML_NS}
SLIDE_PART = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")

SHADOW_RING_INNER_RATIO = 0.002
SHADOW_RING_OUTER_RATIO = 0.05
SHADOW_BACKGROUND_NOISE_FLOOR = 2.0
MIN_REFERENCE_SHADOW_DENSITY = 0.25
INVISIBLE_SHADOW_DENSITY_THRESHOLD = 0.50
SHADOW_ENERGY_RATIO_THRESHOLD = 0.75
SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.25
SHADOW_FIELD_COSINE_THRESHOLD = 0.90
SHADOW_FIELD_IOU_THRESHOLD = 0.55
SHADOW_FIELD_ERROR_THRESHOLD = 0.35
SHADOW_CENTROID_ERROR_RATIO_THRESHOLD = 0.03
SHADOW_FIELD_BINARY_THRESHOLD = 2.0


def _normalize_candidate(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    if reference.ndim != 3 or reference.shape[2] < 3:
        raise ValueError("reference shadow raster must be RGB")
    if candidate.ndim != 3 or candidate.shape[2] < 3:
        raise ValueError("candidate shadow raster must be RGB")
    if candidate.shape[:2] == reference.shape[:2]:
        return candidate[..., :3]
    return cv2.resize(
        candidate[..., :3],
        (reference.shape[1], reference.shape[0]),
        interpolation=cv2.INTER_AREA,
    )


def _shape_mask(image: np.ndarray) -> np.ndarray:
    """Select the blue fixture surface while excluding neutral or theme-colored shadows."""
    rgb = image[..., :3].astype(np.int16)
    mask = (
        (rgb[..., 2] - rgb[..., 0] > 35)
        & (rgb[..., 2] - rgb[..., 1] > 8)
        & (rgb.min(axis=2) < 210)
    )
    if int(mask.sum()) < 64:
        raise ValueError("outer-shadow fixture surface is below the resolution floor")
    return mask


def _ring_for_mask(mask: np.ndarray) -> tuple[np.ndarray, int, int]:
    scale = max(mask.shape)
    inner_radius = max(1, round(scale * SHADOW_RING_INNER_RATIO))
    outer_radius = max(inner_radius + 1, round(scale * SHADOW_RING_OUTER_RATIO))
    distance = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)
    ring = (distance > inner_radius) & (distance <= outer_radius)
    if int(ring.sum()) < 64:
        raise ValueError("outer-shadow measurement ring is below the resolution floor")
    return ring, inner_radius, outer_radius


def _background_luma(image: np.ndarray) -> float:
    rgb = image[..., :3].astype(np.float64)
    border = np.concatenate(
        (
            rgb[0, :, :],
            rgb[-1, :, :],
            rgb[:, 0, :],
            rgb[:, -1, :],
        ),
        axis=0,
    )
    median = np.median(border, axis=0)
    return float(0.2126 * median[0] + 0.7152 * median[1] + 0.0722 * median[2])


def _shadow_darkness(image: np.ndarray, ring: np.ndarray) -> np.ndarray:
    rgb = image[..., :3].astype(np.float64)
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    darkness = np.maximum(
        0.0,
        _background_luma(image) - SHADOW_BACKGROUND_NOISE_FLOOR - luma,
    )
    return darkness * ring


def _centroid(field: np.ndarray) -> np.ndarray:
    total = float(field.sum())
    if total <= 1e-9:
        return np.zeros(2, dtype=np.float64)
    yy, xx = np.indices(field.shape)
    return np.asarray(
        [float((field * xx).sum() / total), float((field * yy).sum() / total)],
        dtype=np.float64,
    )


def _metric_core(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    required: bool,
) -> tuple[dict[str, Any], np.ndarray]:
    candidate = _normalize_candidate(reference, candidate)
    surface_mask = _shape_mask(reference) | _shape_mask(candidate)
    ring, inner_radius, outer_radius = _ring_for_mask(surface_mask)
    reference_field = _shadow_darkness(reference, ring)
    candidate_field = _shadow_darkness(candidate, ring)
    ring_pixels = int(ring.sum())
    reference_energy = float(reference_field.sum())
    candidate_energy = float(candidate_field.sum())
    reference_density = reference_energy / ring_pixels
    candidate_density = candidate_energy / ring_pixels
    maximum_energy = max(reference_energy, candidate_energy)
    energy_ratio = (
        min(reference_energy, candidate_energy) / maximum_energy
        if maximum_energy > 1e-9
        else 1.0
    )
    overshoot_ratio = (
        candidate_energy / reference_energy
        if reference_energy > 1e-9
        else 1.0 + candidate_density
    )
    field_denominator = math.sqrt(
        float((reference_field * reference_field).sum())
        * float((candidate_field * candidate_field).sum())
    )
    if field_denominator > 1e-9:
        field_cosine = float((reference_field * candidate_field).sum() / field_denominator)
    elif reference_energy <= 1e-9 and candidate_energy <= 1e-9:
        field_cosine = 1.0
    else:
        field_cosine = 0.0
    reference_binary = reference_field > SHADOW_FIELD_BINARY_THRESHOLD
    candidate_binary = candidate_field > SHADOW_FIELD_BINARY_THRESHOLD
    union = int((reference_binary | candidate_binary).sum())
    field_iou = float((reference_binary & candidate_binary).sum() / union) if union else 1.0
    field_error = float(
        np.abs(reference_field - candidate_field).sum()
        / max(reference_energy + candidate_energy, 1.0)
    )
    centroid_error_ratio = float(
        np.linalg.norm(_centroid(reference_field) - _centroid(candidate_field))
        / max(reference.shape[:2])
    )
    measurable = required and reference_density >= MIN_REFERENCE_SHADOW_DENSITY
    invisible_limit = max(
        INVISIBLE_SHADOW_DENSITY_THRESHOLD,
        reference_density * SHADOW_OVERSHOOT_RATIO_THRESHOLD,
    )
    if not measurable:
        passed = candidate_density <= invisible_limit
    else:
        passed = (
            energy_ratio >= SHADOW_ENERGY_RATIO_THRESHOLD
            and overshoot_ratio <= SHADOW_OVERSHOOT_RATIO_THRESHOLD
            and field_cosine >= SHADOW_FIELD_COSINE_THRESHOLD
            and field_iou >= SHADOW_FIELD_IOU_THRESHOLD
            and field_error <= SHADOW_FIELD_ERROR_THRESHOLD
            and centroid_error_ratio <= SHADOW_CENTROID_ERROR_RATIO_THRESHOLD
        )
    return (
        {
            "shadowRequired": required,
            "shadowMeasurable": measurable,
            "referenceShadowDensity": reference_density,
            "candidateShadowDensity": candidate_density,
            "shadowEnergyRatio": energy_ratio,
            "shadowOvershootRatio": overshoot_ratio,
            "shadowFieldCosine": field_cosine,
            "shadowFieldIou": field_iou,
            "shadowFieldError": field_error,
            "shadowCentroidErrorRatio": centroid_error_ratio,
            "shadowRingPixels": ring_pixels,
            "shadowRingInnerPx": inner_radius,
            "shadowRingOuterPx": outer_radius,
            "passed": passed,
        },
        surface_mask,
    )


def _erase_exterior_shadow(image: np.ndarray) -> np.ndarray:
    surface_mask = _shape_mask(image)
    ring, _, _ = _ring_for_mask(surface_mask)
    mutated = image.copy()
    mutated[ring] = np.asarray((255, 255, 255), dtype=mutated.dtype)
    return mutated


def compute_outer_shadow_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    required: bool,
) -> dict[str, Any]:
    metrics, _ = _metric_core(reference, candidate, required=required)
    sensitivity: dict[str, Any] = {
        "mutation": "erase-exterior-shadow",
        "applicable": bool(metrics["shadowMeasurable"]),
        "detected": None,
    }
    if metrics["shadowMeasurable"]:
        normalized_candidate = _normalize_candidate(reference, candidate)
        mutated, _ = _metric_core(
            reference,
            _erase_exterior_shadow(normalized_candidate),
            required=required,
        )
        sensitivity.update(
            {
                "mutatedCandidateShadowDensity": mutated["candidateShadowDensity"],
                "mutatedShadowEnergyRatio": mutated["shadowEnergyRatio"],
                "mutatedShadowPassed": mutated["passed"],
                "detected": not mutated["passed"],
            }
        )
        metrics["passed"] = bool(metrics["passed"] and sensitivity["detected"])
    metrics["shadowSensitivity"] = sensitivity
    metrics["thresholds"] = {
        "ringInnerRatio": SHADOW_RING_INNER_RATIO,
        "ringOuterRatio": SHADOW_RING_OUTER_RATIO,
        "backgroundNoiseFloor": SHADOW_BACKGROUND_NOISE_FLOOR,
        "minimumReferenceShadowDensity": MIN_REFERENCE_SHADOW_DENSITY,
        "invisibleShadowDensity": INVISIBLE_SHADOW_DENSITY_THRESHOLD,
        "shadowEnergyRatio": SHADOW_ENERGY_RATIO_THRESHOLD,
        "shadowOvershootRatio": SHADOW_OVERSHOOT_RATIO_THRESHOLD,
        "shadowFieldCosine": SHADOW_FIELD_COSINE_THRESHOLD,
        "shadowFieldIou": SHADOW_FIELD_IOU_THRESHOLD,
        "shadowFieldError": SHADOW_FIELD_ERROR_THRESHOLD,
        "shadowCentroidErrorRatio": SHADOW_CENTROID_ERROR_RATIO_THRESHOLD,
        "shadowFieldBinaryThreshold": SHADOW_FIELD_BINARY_THRESHOLD,
    }
    return metrics


def extract_outer_shadow_slide_indices(source_pptx: Path) -> set[int]:
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        for name in archive.namelist():
            match = SLIDE_PART.fullmatch(name)
            if not match:
                continue
            root = etree.fromstring(archive.read(name))
            if root.xpath(
                "boolean(.//p:sp/p:spPr/a:effectLst/a:outerShdw)",
                namespaces=NS,
            ):
                indices.add(int(match.group(1)) - 1)
    return indices


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repository_relative(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"outer-shadow artifact must be inside the repository: {path}") from error


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON report must be an object: {path}")
    return value


def _verified_artifact(
    *,
    case_id: str,
    slide_index: int,
    kind: str,
    suffix: str,
    artifacts: Mapping[str, Any],
    reports_dir: Path,
    repo: Path,
) -> tuple[Path, dict[str, Any]]:
    path = reports_dir / f"{case_id}_slide{slide_index}_{suffix}.png"
    expected = artifacts.get(kind)
    if not isinstance(expected, Mapping):
        raise ValueError(
            f"native report is missing {kind} artifact: {case_id} slide {slide_index}"
        )
    actual = {
        "path": _repository_relative(path, repo),
        "sizeBytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if any(expected.get(key) != value for key, value in actual.items()):
        raise ValueError(
            f"native report artifact does not match {kind} raster: "
            f"{case_id} slide {slide_index}"
        )
    return path, actual


def build_outer_shadow_report(
    case_report_paths: Sequence[Path],
    repo: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    if not case_report_paths:
        raise ValueError("at least one case report is required")
    cases: list[dict[str, Any]] = []
    renderer: Mapping[str, Any] | None = None
    for case_report_path in case_report_paths:
        case_report = _load_json(case_report_path)
        case_id = case_report.get("testFile")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"case report is missing testFile: {case_report_path}")
        provenance = case_report.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError(f"case report is missing provenance: {case_id}")
        report_renderer = provenance.get("renderer")
        if not isinstance(report_renderer, Mapping):
            raise ValueError(f"case report is missing renderer provenance: {case_id}")
        if renderer is None:
            renderer = dict(report_renderer)
        elif dict(renderer) != dict(report_renderer):
            raise ValueError("case reports must use one renderer revision")
        inputs = provenance.get("inputs")
        if not isinstance(inputs, Mapping):
            raise ValueError(f"case report is missing input provenance: {case_id}")
        source_info = inputs.get("sourcePptx")
        ground_truth_info = inputs.get("groundTruth")
        if not isinstance(source_info, Mapping) or not isinstance(ground_truth_info, Mapping):
            raise ValueError(f"case report is missing source or ground-truth provenance: {case_id}")
        source_value = source_info.get("path")
        if not isinstance(source_value, str):
            raise ValueError(f"case report is missing source path: {case_id}")
        source_path = repo / source_value
        source_sha256 = _sha256(source_path)
        if source_info.get("sha256") != source_sha256:
            raise ValueError(f"case report source fingerprint is stale: {case_id}")
        shadow_slides = extract_outer_shadow_slide_indices(source_path)
        slide_results: list[dict[str, Any]] = []
        visible_indices: set[int] = set()
        for slide in case_report.get("perSlide", []):
            if not isinstance(slide, Mapping) or slide.get("hidden") is True:
                continue
            slide_index = slide.get("slideIdx")
            if not isinstance(slide_index, int):
                raise ValueError(f"case report has invalid slide index: {case_id}")
            visible_indices.add(slide_index)
            render_artifacts = slide.get("renderArtifacts")
            if not isinstance(render_artifacts, Mapping):
                raise ValueError(
                    f"native report is missing render artifacts: {case_id} slide {slide_index}"
                )
            reference_path, reference_artifact = _verified_artifact(
                case_id=case_id,
                slide_index=slide_index,
                kind="reference",
                suffix="pdf",
                artifacts=render_artifacts,
                reports_dir=reports_dir,
                repo=repo,
            )
            candidate_path, candidate_artifact = _verified_artifact(
                case_id=case_id,
                slide_index=slide_index,
                kind="candidate",
                suffix="html",
                artifacts=render_artifacts,
                reports_dir=reports_dir,
                repo=repo,
            )
            reference = np.asarray(Image.open(reference_path).convert("RGB"))
            candidate = np.asarray(Image.open(candidate_path).convert("RGB"))
            metrics = compute_outer_shadow_metrics(
                reference,
                candidate,
                required=slide_index in shadow_slides,
            )
            slide_results.append(
                {
                    "slideIdx": slide_index,
                    "shadowRequired": slide_index in shadow_slides,
                    "referencePath": reference_artifact["path"],
                    "candidatePath": candidate_artifact["path"],
                    "referenceSha256": reference_artifact["sha256"],
                    "candidateSha256": candidate_artifact["sha256"],
                    "metrics": metrics,
                    "passed": metrics["passed"],
                }
            )
        missing_slides = shadow_slides - visible_indices
        if missing_slides:
            raise ValueError(
                f"case report omits outer-shadow slides for {case_id}: {sorted(missing_slides)}"
            )
        applicable = bool(shadow_slides)
        passed = applicable and bool(slide_results) and all(
            bool(slide["passed"]) for slide in slide_results
        )
        cases.append(
            {
                "caseId": case_id,
                "sourceSha256": source_sha256,
                "groundTruthSha256": ground_truth_info.get("combinedSha256"),
                "applicable": applicable,
                "passed": passed,
                "slides": slide_results,
            }
        )
    applicable_count = sum(1 for case in cases if case["applicable"])
    return {
        "schemaVersion": 1,
        "renderer": dict(renderer or {}),
        "thresholds": compute_outer_shadow_metrics(
            _synthetic_threshold_image(),
            _synthetic_threshold_image(),
            required=False,
        )["thresholds"],
        "caseResults": sorted(cases, key=lambda case: case["caseId"]),
        "applicableCaseCount": applicable_count,
        "passed": applicable_count > 0 and all(case["passed"] for case in cases),
    }


def _synthetic_threshold_image() -> np.ndarray:
    image = np.full((32, 32, 3), 255, dtype=np.uint8)
    image[8:24, 8:24] = np.asarray((58, 123, 213), dtype=np.uint8)
    return image


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate ordinary-shape outer-shadow fidelity")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--case-report", action="append", required=True)
    parser.add_argument("--reports-dir", default="test/e2e/reports")
    parser.add_argument(
        "--out",
        default="test/e2e/reports/capability-loop/outer-shadow-local.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo_root).resolve()
    reports_dir = Path(args.reports_dir)
    if not reports_dir.is_absolute():
        reports_dir = repo / reports_dir
    output = Path(args.out)
    if not output.is_absolute():
        output = repo / output
    try:
        report = build_outer_shadow_report(
            [Path(path) if Path(path).is_absolute() else repo / path for path in args.case_report],
            repo,
            reports_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"evaluated {report['applicableCaseCount']} applicable outer-shadow case(s); "
        f"passed={report['passed']} -> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
