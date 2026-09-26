#!/usr/bin/env python3
"""Measure bounded ordinary-shape reflection fidelity against native PowerPoint rasters."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
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

REFLECTION_BLUR_PAD_MULTIPLIER = 2.0
REFLECTION_BACKGROUND_NOISE_FLOOR = 2.0
MIN_REFERENCE_REFLECTION_DENSITY = 2.0
INVISIBLE_REFLECTION_DENSITY_THRESHOLD = 1.5
REFLECTION_ENERGY_RATIO_THRESHOLD = 0.85
REFLECTION_OVERSHOOT_RATIO_THRESHOLD = 1.20
REFLECTION_FIELD_COSINE_THRESHOLD = 0.95
REFLECTION_FIELD_IOU_THRESHOLD = 0.75
REFLECTION_FIELD_ERROR_THRESHOLD = 0.20
REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD = 0.03
REFLECTION_FIELD_BINARY_THRESHOLD = 2.0


@dataclass(frozen=True)
class ReflectionRegion:
    """One normalized slide-space rectangle containing a reflected shape."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class ShapeReflectionSlideSpec:
    reflection_regions: tuple[ReflectionRegion, ...]


def _normalize_candidate(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    if reference.ndim != 3 or reference.shape[2] < 3:
        raise ValueError("reference reflection raster must be RGB")
    if candidate.ndim != 3 or candidate.shape[2] < 3:
        raise ValueError("candidate reflection raster must be RGB")
    if candidate.shape[:2] == reference.shape[:2]:
        return candidate[..., :3]
    return cv2.resize(
        candidate[..., :3],
        (reference.shape[1], reference.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )


def _region_mask(shape: tuple[int, int], regions: Sequence[ReflectionRegion]) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=bool)
    for region in regions:
        x0 = max(0, min(width, math.floor(region.x0 * width)))
        y0 = max(0, min(height, math.floor(region.y0 * height)))
        x1 = max(0, min(width, math.ceil(region.x1 * width)))
        y1 = max(0, min(height, math.ceil(region.y1 * height)))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    if int(mask.sum()) < 64:
        raise ValueError("reflection measurement region is below the resolution floor")
    return mask


def _background_luma(image: np.ndarray) -> float:
    rgb = image[..., :3].astype(np.float64)
    border = np.concatenate(
        (rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]),
        axis=0,
    )
    median = np.median(border, axis=0)
    return float(0.2126 * median[0] + 0.7152 * median[1] + 0.0722 * median[2])


def _reflection_darkness(image: np.ndarray, region_mask: np.ndarray) -> np.ndarray:
    rgb = image[..., :3].astype(np.float64)
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    darkness = np.maximum(
        0.0,
        _background_luma(image) - REFLECTION_BACKGROUND_NOISE_FLOOR - luma,
    )
    return darkness * region_mask


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
    regions: Sequence[ReflectionRegion],
) -> dict[str, Any]:
    candidate = _normalize_candidate(reference, candidate)
    region_mask = _region_mask(reference.shape[:2], regions)
    reference_field = _reflection_darkness(reference, region_mask)
    candidate_field = _reflection_darkness(candidate, region_mask)
    region_pixels = int(region_mask.sum())
    reference_energy = float(reference_field.sum())
    candidate_energy = float(candidate_field.sum())
    reference_density = reference_energy / region_pixels
    candidate_density = candidate_energy / region_pixels
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
    denominator = math.sqrt(
        float((reference_field * reference_field).sum())
        * float((candidate_field * candidate_field).sum())
    )
    if denominator > 1e-9:
        field_cosine = float((reference_field * candidate_field).sum() / denominator)
    elif reference_energy <= 1e-9 and candidate_energy <= 1e-9:
        field_cosine = 1.0
    else:
        field_cosine = 0.0
    reference_binary = reference_field > REFLECTION_FIELD_BINARY_THRESHOLD
    candidate_binary = candidate_field > REFLECTION_FIELD_BINARY_THRESHOLD
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
    measurable = required and reference_density >= MIN_REFERENCE_REFLECTION_DENSITY
    invisible_limit = max(
        INVISIBLE_REFLECTION_DENSITY_THRESHOLD,
        reference_density * REFLECTION_OVERSHOOT_RATIO_THRESHOLD,
    )
    if not measurable:
        passed = candidate_density <= invisible_limit
    else:
        passed = (
            energy_ratio >= REFLECTION_ENERGY_RATIO_THRESHOLD
            and overshoot_ratio <= REFLECTION_OVERSHOOT_RATIO_THRESHOLD
            and field_cosine >= REFLECTION_FIELD_COSINE_THRESHOLD
            and field_iou >= REFLECTION_FIELD_IOU_THRESHOLD
            and field_error <= REFLECTION_FIELD_ERROR_THRESHOLD
            and centroid_error_ratio <= REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD
        )
    return {
        "reflectionRequired": required,
        "reflectionMeasurable": measurable,
        "referenceReflectionDensity": reference_density,
        "candidateReflectionDensity": candidate_density,
        "reflectionEnergyRatio": energy_ratio,
        "reflectionOvershootRatio": overshoot_ratio,
        "reflectionFieldCosine": field_cosine,
        "reflectionFieldIou": field_iou,
        "reflectionFieldError": field_error,
        "reflectionCentroidErrorRatio": centroid_error_ratio,
        "reflectionRegionPixels": region_pixels,
        "passed": passed,
    }


def _erase_reflection_regions(
    image: np.ndarray,
    regions: Sequence[ReflectionRegion],
) -> np.ndarray:
    mutated = image.copy()
    mutated[_region_mask(image.shape[:2], regions)] = np.asarray(
        (255, 255, 255), dtype=mutated.dtype
    )
    return mutated


def compute_reflection_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    required: bool,
    regions: Sequence[ReflectionRegion],
) -> dict[str, Any]:
    metrics = _metric_core(reference, candidate, required=required, regions=regions)
    sensitivity: dict[str, Any] = {
        "mutation": "erase-reflection-region",
        "applicable": bool(metrics["reflectionMeasurable"]),
        "detected": None,
    }
    if metrics["reflectionMeasurable"]:
        normalized_candidate = _normalize_candidate(reference, candidate)
        mutated = _metric_core(
            reference,
            _erase_reflection_regions(normalized_candidate, regions),
            required=required,
            regions=regions,
        )
        sensitivity.update(
            {
                "mutatedCandidateReflectionDensity": mutated[
                    "candidateReflectionDensity"
                ],
                "mutatedReflectionEnergyRatio": mutated["reflectionEnergyRatio"],
                "mutatedReflectionPassed": mutated["passed"],
                "detected": not mutated["passed"],
            }
        )
        metrics["passed"] = bool(metrics["passed"] and sensitivity["detected"])
    metrics["reflectionSensitivity"] = sensitivity
    metrics["thresholds"] = {
        "blurPadMultiplier": REFLECTION_BLUR_PAD_MULTIPLIER,
        "backgroundNoiseFloor": REFLECTION_BACKGROUND_NOISE_FLOOR,
        "minimumReferenceReflectionDensity": MIN_REFERENCE_REFLECTION_DENSITY,
        "invisibleReflectionDensity": INVISIBLE_REFLECTION_DENSITY_THRESHOLD,
        "reflectionEnergyRatio": REFLECTION_ENERGY_RATIO_THRESHOLD,
        "reflectionOvershootRatio": REFLECTION_OVERSHOOT_RATIO_THRESHOLD,
        "reflectionFieldCosine": REFLECTION_FIELD_COSINE_THRESHOLD,
        "reflectionFieldIou": REFLECTION_FIELD_IOU_THRESHOLD,
        "reflectionFieldError": REFLECTION_FIELD_ERROR_THRESHOLD,
        "reflectionCentroidErrorRatio": REFLECTION_CENTROID_ERROR_RATIO_THRESHOLD,
        "reflectionFieldBinaryThreshold": REFLECTION_FIELD_BINARY_THRESHOLD,
    }
    return metrics


def _number(node: etree._Element, name: str, default: float) -> float:
    value = node.get(name)
    return float(value) if value is not None else default


def _alignment_anchor(alignment: str, width: float, height: float) -> tuple[float, float]:
    horizontal = (
        0.0
        if alignment.endswith("l") or alignment == "l"
        else width
        if alignment.endswith("r") or alignment == "r"
        else width / 2
    )
    vertical = (
        0.0
        if alignment.startswith("t") or alignment == "t"
        else height
        if alignment.startswith("b") or alignment == "b"
        else height / 2
    )
    return horizontal, vertical


def _reflection_bounds(
    box: tuple[float, float, float, float],
    reflection: etree._Element,
) -> tuple[float, float, float, float]:
    x, y, width, height = box
    scale_x = max(-16.0, min(16.0, _number(reflection, "sx", 100000) / 100000))
    scale_y = max(-16.0, min(16.0, _number(reflection, "sy", 100000) / 100000))
    skew_x = max(
        -16.0,
        min(16.0, math.tan(math.radians((_number(reflection, "kx", 0) / 60000) % 360))),
    )
    skew_y = max(
        -16.0,
        min(16.0, math.tan(math.radians((_number(reflection, "ky", 0) / 60000) % 360))),
    )
    anchor_x, anchor_y = _alignment_anchor(
        reflection.get("algn", "b").lower(), width, height
    )
    direction = math.radians((_number(reflection, "dir", 0) / 60000) % 360)
    distance = max(0.0, _number(reflection, "dist", 0))
    offset_x = distance * math.cos(direction)
    offset_y = distance * math.sin(direction)
    translate_x = anchor_x + offset_x - scale_x * anchor_x - skew_x * anchor_y
    translate_y = anchor_y + offset_y - skew_y * anchor_x - scale_y * anchor_y
    points = [
        (
            scale_x * point_x + skew_x * point_y + translate_x,
            skew_y * point_x + scale_y * point_y + translate_y,
        )
        for point_x, point_y in ((0.0, 0.0), (width, 0.0), (0.0, height), (width, height))
    ]
    blur_pad = max(0.0, _number(reflection, "blurRad", 0)) * REFLECTION_BLUR_PAD_MULTIPLIER
    return (
        x + min(point[0] for point in points) - blur_pad,
        y + min(point[1] for point in points) - blur_pad,
        x + max(point[0] for point in points) + blur_pad,
        y + max(point[1] for point in points) + blur_pad,
    )


def _xfrm_box(xfrm: etree._Element | None) -> tuple[float, float, float, float] | None:
    if xfrm is None:
        return None
    offset = xfrm.find("a:off", namespaces=NS)
    extent = xfrm.find("a:ext", namespaces=NS)
    if offset is None or extent is None:
        return None
    try:
        return (
            float(offset.get("x", "0")),
            float(offset.get("y", "0")),
            float(extent.get("cx", "0")),
            float(extent.get("cy", "0")),
        )
    except ValueError:
        return None


def _normalized_region(
    bounds: tuple[float, float, float, float],
    slide_width: float,
    slide_height: float,
) -> ReflectionRegion | None:
    x0, y0, x1, y1 = bounds
    values = (
        max(0.0, min(1.0, x0 / slide_width)),
        max(0.0, min(1.0, y0 / slide_height)),
        max(0.0, min(1.0, x1 / slide_width)),
        max(0.0, min(1.0, y1 / slide_height)),
    )
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return ReflectionRegion(*values)


def extract_shape_reflection_specs(
    source_pptx: Path,
) -> dict[int, ShapeReflectionSlideSpec]:
    """Extract direct-shape and one-level grouped-shape reflection regions."""
    specs: dict[int, ShapeReflectionSlideSpec] = {}
    with ZipFile(source_pptx) as archive:
        presentation = etree.fromstring(archive.read("ppt/presentation.xml"))
        slide_size = presentation.find("p:sldSz", namespaces=NS)
        if slide_size is None:
            raise ValueError("presentation is missing p:sldSz")
        slide_width = float(slide_size.get("cx", "0"))
        slide_height = float(slide_size.get("cy", "0"))
        if slide_width <= 0 or slide_height <= 0:
            raise ValueError("presentation slide size is invalid")

        for name in archive.namelist():
            match = SLIDE_PART.fullmatch(name)
            if not match:
                continue
            root = etree.fromstring(archive.read(name))
            regions: list[ReflectionRegion] = []
            direct_shapes = root.xpath("./p:cSld/p:spTree/p:sp", namespaces=NS)
            for shape in direct_shapes:
                reflection = shape.find(
                    "./p:spPr/a:effectLst/a:reflection", namespaces=NS
                )
                box = _xfrm_box(shape.find("./p:spPr/a:xfrm", namespaces=NS))
                if reflection is None or box is None:
                    continue
                region = _normalized_region(
                    _reflection_bounds(box, reflection), slide_width, slide_height
                )
                if region is not None:
                    regions.append(region)

            groups = root.xpath("./p:cSld/p:spTree/p:grpSp", namespaces=NS)
            for group in groups:
                group_xfrm = group.find("./p:grpSpPr/a:xfrm", namespaces=NS)
                group_box = _xfrm_box(group_xfrm)
                child_offset = (
                    group_xfrm.find("a:chOff", namespaces=NS)
                    if group_xfrm is not None
                    else None
                )
                child_extent = (
                    group_xfrm.find("a:chExt", namespaces=NS)
                    if group_xfrm is not None
                    else None
                )
                if (
                    group_box is None
                    or child_offset is None
                    or child_extent is None
                ):
                    continue
                group_x, group_y, group_width, group_height = group_box
                child_x = float(child_offset.get("x", "0"))
                child_y = float(child_offset.get("y", "0"))
                child_width = float(child_extent.get("cx", "0"))
                child_height = float(child_extent.get("cy", "0"))
                if child_width == 0 or child_height == 0:
                    continue
                scale_x = group_width / child_width
                scale_y = group_height / child_height
                for shape in group.findall("p:sp", namespaces=NS):
                    reflection = shape.find(
                        "./p:spPr/a:effectLst/a:reflection", namespaces=NS
                    )
                    box = _xfrm_box(shape.find("./p:spPr/a:xfrm", namespaces=NS))
                    if reflection is None or box is None:
                        continue
                    local = _reflection_bounds(box, reflection)
                    mapped = (
                        group_x + (local[0] - child_x) * scale_x,
                        group_y + (local[1] - child_y) * scale_y,
                        group_x + (local[2] - child_x) * scale_x,
                        group_y + (local[3] - child_y) * scale_y,
                    )
                    region = _normalized_region(mapped, slide_width, slide_height)
                    if region is not None:
                        regions.append(region)
            if regions:
                specs[int(match.group(1)) - 1] = ShapeReflectionSlideSpec(tuple(regions))
    return specs


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
        raise ValueError(f"reflection artifact must be inside the repository: {path}") from error


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


def build_reflection_report(
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
        specs = extract_shape_reflection_specs(source_path)
        slide_results: list[dict[str, Any]] = []
        seen_indices: set[int] = set()
        for slide in case_report.get("perSlide", []):
            if not isinstance(slide, Mapping) or slide.get("hidden") is True:
                continue
            slide_index = slide.get("slideIdx")
            if not isinstance(slide_index, int):
                raise ValueError(f"case report has invalid slide index: {case_id}")
            spec = specs.get(slide_index)
            if spec is None:
                continue
            seen_indices.add(slide_index)
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
            metrics = compute_reflection_metrics(
                reference,
                candidate,
                required=True,
                regions=spec.reflection_regions,
            )
            slide_results.append(
                {
                    "slideIdx": slide_index,
                    "reflectionRequired": True,
                    "referencePath": reference_artifact["path"],
                    "candidatePath": candidate_artifact["path"],
                    "referenceSha256": reference_artifact["sha256"],
                    "candidateSha256": candidate_artifact["sha256"],
                    "regions": [region.__dict__ for region in spec.reflection_regions],
                    "metrics": metrics,
                    "passed": metrics["passed"],
                }
            )
        missing_slides = set(specs) - seen_indices
        if missing_slides:
            raise ValueError(
                f"case report omits reflection slides for {case_id}: {sorted(missing_slides)}"
            )
        applicable = bool(specs)
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
        "thresholds": compute_reflection_metrics(
            _synthetic_threshold_image(),
            _synthetic_threshold_image(),
            required=False,
            regions=(ReflectionRegion(0.25, 0.25, 0.75, 0.75),),
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
    parser = argparse.ArgumentParser(description="Evaluate ordinary-shape reflection fidelity")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--case-report", action="append", required=True)
    parser.add_argument("--reports-dir", default="test/e2e/reports")
    parser.add_argument(
        "--out",
        default="test/e2e/reports/capability-loop/reflection-local.json",
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
        report = build_reflection_report(
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
        f"evaluated {report['applicableCaseCount']} applicable reflection case(s); "
        f"passed={report['passed']} -> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
