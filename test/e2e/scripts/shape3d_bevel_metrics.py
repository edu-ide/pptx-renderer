#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import ZipFile

import cv2
import numpy as np
from lxml import etree
from PIL import Image


E2E_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = E2E_DIR.parents[1]
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": P_NS, "a": A_NS}
SCORE_THRESHOLD = 0.60
CORNER_SCORE_THRESHOLD = 0.78
RANGE_RATIO_THRESHOLD = 0.85
HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD = 0.80
PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD = 0.70
SHADOW_AMPLITUDE_RATIO_THRESHOLD = 0.85
SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.05
SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD = 1.01
SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD = 1.05
SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD = 0.30
SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD = 1.60
DONUT_SHADOW_SECTOR_DEGREES = 30
MIN_DONUT_SECTOR_REFERENCE_SHADOW = 3.0
MIN_EVALUABLE_BAND_PX = 4.0
DEFAULT_BEVEL_DIMENSION_EMU = 76200.0


@dataclass(frozen=True)
class BevelRegion:
    slide_index: int
    x: float
    y: float
    width: float
    height: float
    bevel_width_x: float
    bevel_width_y: float
    preset: str
    surface: str = "shape"
    corner_radius_ratio: float = 0.0
    geometry_adjustment: float = 0.0
    geometry_inset_x_ratio: float = 0.0
    geometry_inset_y_ratio: float = 0.0
    rotation_degrees: float = 0.0


def _float_attr(node: etree._Element | None, name: str, default: float = 0.0) -> float:
    if node is None:
        return default
    try:
        value = float(node.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _slide_number(path: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", path)
    return int(match.group(1)) if match else 0


def _compose_transform(
    parent: tuple[float, float, float, float],
    local: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    parent_sx, parent_sy, parent_tx, parent_ty = parent
    local_sx, local_sy, local_tx, local_ty = local
    return (
        parent_sx * local_sx,
        parent_sy * local_sy,
        parent_tx + parent_sx * local_tx,
        parent_ty + parent_sy * local_ty,
    )


def _group_transform(group: etree._Element) -> tuple[float, float, float, float] | None:
    xfrm = group.find("p:grpSpPr/a:xfrm", NS)
    if xfrm is None:
        return None
    offset = xfrm.find("a:off", NS)
    extent = xfrm.find("a:ext", NS)
    child_offset = xfrm.find("a:chOff", NS)
    child_extent = xfrm.find("a:chExt", NS)
    child_width = _float_attr(child_extent, "cx")
    child_height = _float_attr(child_extent, "cy")
    if child_width <= 0 or child_height <= 0:
        return None
    scale_x = _float_attr(extent, "cx") / child_width
    scale_y = _float_attr(extent, "cy") / child_height
    return (
        scale_x,
        scale_y,
        _float_attr(offset, "x") - _float_attr(child_offset, "x") * scale_x,
        _float_attr(offset, "y") - _float_attr(child_offset, "y") * scale_y,
    )


def _roundrect_adjustment(shape_properties: etree._Element) -> float:
    guide = shape_properties.find(
        "a:prstGeom/a:avLst/a:gd[@name='adj']",
        NS,
    )
    formula = guide.get("fmla", "") if guide is not None else ""
    match = re.fullmatch(r"\s*val\s+(-?\d+(?:\.\d+)?)\s*", formula)
    value = float(match.group(1)) if match else 16667.0
    return max(0.0, min(0.5, value / 100000.0))


def _donut_adjustment(shape_properties: etree._Element) -> float:
    guide = shape_properties.find(
        "a:prstGeom/a:avLst/a:gd[@name='adj']",
        NS,
    )
    formula = guide.get("fmla", "") if guide is not None else ""
    match = re.fullmatch(r"\s*val\s+(-?\d+(?:\.\d+)?)\s*", formula)
    value = float(match.group(1)) if match else 25000.0
    return max(0.0, min(0.5, value / 100000.0))


def _shape_regions(
    container: etree._Element,
    slide_index: int,
    slide_width: float,
    slide_height: float,
    parent_transform: tuple[float, float, float, float],
) -> list[BevelRegion]:
    regions: list[BevelRegion] = []
    for child in container:
        local_name = etree.QName(child).localname
        if local_name == "grpSp":
            transform = _group_transform(child)
            if transform is not None:
                regions.extend(
                    _shape_regions(
                        child,
                        slide_index,
                        slide_width,
                        slide_height,
                        _compose_transform(parent_transform, transform),
                    )
                )
            continue
        if local_name not in {"sp", "pic"}:
            continue
        shape_properties = child.find("p:spPr", NS)
        if shape_properties is None:
            continue
        bevel = shape_properties.find("a:sp3d/a:bevelT", NS)
        if bevel is None or bevel.get("prst", "circle") != "circle":
            continue
        bevel_width = _float_attr(bevel, "w", DEFAULT_BEVEL_DIMENSION_EMU)
        xfrm = shape_properties.find("a:xfrm", NS)
        offset = xfrm.find("a:off", NS) if xfrm is not None else None
        extent = xfrm.find("a:ext", NS) if xfrm is not None else None
        width = _float_attr(extent, "cx")
        height = _float_attr(extent, "cy")
        if bevel_width <= 0 or width <= 0 or height <= 0:
            continue
        scale_x, scale_y, translate_x, translate_y = parent_transform
        absolute_x = translate_x + scale_x * _float_attr(offset, "x")
        absolute_y = translate_y + scale_y * _float_attr(offset, "y")
        absolute_width = abs(scale_x * width)
        absolute_height = abs(scale_y * height)
        preset_node = shape_properties.find("a:prstGeom", NS)
        if preset_node is not None:
            preset = preset_node.get("prst", "rect")
        elif shape_properties.find("a:custGeom", NS) is not None:
            preset = "custom"
        else:
            preset = "rect"
        corner_radius = _roundrect_adjustment(shape_properties) if preset == "roundRect" else 0.0
        geometry_adjustment = _donut_adjustment(shape_properties) if preset == "donut" else 0.0
        # adj=0 collapses the inner and outer donut paths to a zero-area surface. The full-page
        # native gate still checks that degenerate result; there is no interior bevel band to score.
        if preset == "donut" and geometry_adjustment <= 0:
            continue
        geometry_inset = min(width, height) * geometry_adjustment
        regions.append(
            BevelRegion(
                slide_index=slide_index,
                x=absolute_x / slide_width,
                y=absolute_y / slide_height,
                width=absolute_width / slide_width,
                height=absolute_height / slide_height,
                bevel_width_x=abs(scale_x * bevel_width) / slide_width,
                bevel_width_y=abs(scale_y * bevel_width) / slide_height,
                preset=preset,
                surface="picture" if local_name == "pic" else "shape",
                corner_radius_ratio=corner_radius,
                geometry_adjustment=geometry_adjustment,
                geometry_inset_x_ratio=geometry_inset / width,
                geometry_inset_y_ratio=geometry_inset / height,
                rotation_degrees=_float_attr(xfrm, "rot") / 60000.0,
            )
        )
    return regions


def extract_shape3d_regions(source_pptx: Path) -> list[BevelRegion]:
    with ZipFile(source_pptx) as archive:
        presentation = etree.fromstring(archive.read("ppt/presentation.xml"))
        slide_size = presentation.find("p:sldSz", NS)
        slide_width = _float_attr(slide_size, "cx")
        slide_height = _float_attr(slide_size, "cy")
        if slide_width <= 0 or slide_height <= 0:
            raise ValueError("presentation slide size must be positive")
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        regions: list[BevelRegion] = []
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            shape_tree = slide.find("p:cSld/p:spTree", NS)
            if shape_tree is not None:
                regions.extend(
                    _shape_regions(
                        shape_tree,
                        slide_index,
                        slide_width,
                        slide_height,
                        (1.0, 1.0, 0.0, 0.0),
                    )
                )
    return regions


def _common_images(reference: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if reference.ndim != 3 or candidate.ndim != 3:
        raise ValueError("reference and candidate must be RGB images")
    height = min(reference.shape[0], candidate.shape[0])
    width = min(reference.shape[1], candidate.shape[1])
    if height < 8 or width < 8:
        raise ValueError("reference and candidate are too small")
    resized = []
    for image in (reference[..., :3], candidate[..., :3]):
        if image.shape[:2] == (height, width):
            resized.append(image.astype(np.uint8, copy=False))
        else:
            resized.append(
                np.asarray(Image.fromarray(image).resize((width, height), Image.Resampling.LANCZOS))
            )
    return resized[0], resized[1]


def _geometry_mask(width: int, height: int, region: BevelRegion) -> np.ndarray | None:
    if region.preset == "rect":
        return np.ones((height, width), dtype=bool)
    yy, xx = np.mgrid[:height, :width]
    if region.preset in {"ellipse", "donut"}:
        center_x = (width - 1) / 2
        center_y = (height - 1) / 2
        outer = (
            ((xx - center_x) / max(width / 2, 1.0)) ** 2
            + ((yy - center_y) / max(height / 2, 1.0)) ** 2
            <= 1
        )
        if region.preset == "ellipse":
            return outer
        inner_radius_x = width / 2 - width * region.geometry_inset_x_ratio
        inner_radius_y = height / 2 - height * region.geometry_inset_y_ratio
        if inner_radius_x <= 0 or inner_radius_y <= 0:
            return outer
        inner = (
            ((xx - center_x) / inner_radius_x) ** 2
            + ((yy - center_y) / inner_radius_y) ** 2
            < 1
        )
        return outer & ~inner
    if region.preset != "roundRect" or region.corner_radius_ratio <= 0:
        return None
    radius = max(1.0, min(width, height) * region.corner_radius_ratio)
    nearest_x = np.clip(xx, radius, width - radius - 1)
    nearest_y = np.clip(yy, radius, height - radius - 1)
    return (xx - nearest_x) ** 2 + (yy - nearest_y) ** 2 <= radius**2


def _luminance(image: np.ndarray) -> np.ndarray:
    return (
        image[..., 0].astype(np.float32) * 0.2126
        + image[..., 1].astype(np.float32) * 0.7152
        + image[..., 2].astype(np.float32) * 0.0722
    )


def _field_score(
    reference_delta: np.ndarray,
    candidate_delta: np.ndarray,
    mask: np.ndarray,
) -> dict[str, float]:
    reference_values = reference_delta[mask]
    candidate_values = candidate_delta[mask]
    if reference_values.size < 20:
        raise ValueError("bevel ring contains too few pixels")
    reference_p95 = float(np.percentile(reference_values, 95))
    reference_p05 = float(np.percentile(reference_values, 5))
    candidate_p95 = float(np.percentile(candidate_values, 95))
    candidate_p05 = float(np.percentile(candidate_values, 5))
    reference_range = reference_p95 - reference_p05
    candidate_range = candidate_p95 - candidate_p05
    if np.std(reference_values) > 0.5 and np.std(candidate_values) > 0.5:
        correlation = float(np.corrcoef(reference_values, candidate_values)[0, 1])
        if not math.isfinite(correlation):
            correlation = 0.0
    else:
        correlation = 0.0
    mean_absolute_error = float(np.mean(np.abs(reference_values - candidate_values)))
    error_score = max(0.0, 1.0 - mean_absolute_error / max(reference_range, 8.0))
    range_ratio = (
        min(reference_range, candidate_range) / max(reference_range, candidate_range)
        if max(reference_range, candidate_range) > 1e-6
        else 1.0
    )
    reference_highlight = max(reference_p95, 0.0)
    candidate_highlight = max(candidate_p95, 0.0)
    reference_shadow = max(-reference_p05, 0.0)
    candidate_shadow = max(-candidate_p05, 0.0)

    def amplitude_ratio(reference_amplitude: float, candidate_amplitude: float) -> float:
        maximum = max(reference_amplitude, candidate_amplitude)
        return min(reference_amplitude, candidate_amplitude) / maximum if maximum > 1e-6 else 1.0

    highlight_amplitude_ratio = amplitude_ratio(reference_highlight, candidate_highlight)
    shadow_amplitude_ratio = amplitude_ratio(reference_shadow, candidate_shadow)
    shadow_overshoot_ratio = (
        candidate_shadow / reference_shadow
        if reference_shadow > 1e-6
        else 1.0 + candidate_shadow
    )
    reference_shadow_energy = float(np.mean(np.maximum(-reference_values, 0.0)))
    candidate_shadow_energy = float(np.mean(np.maximum(-candidate_values, 0.0)))
    shadow_energy_overshoot_ratio = (
        candidate_shadow_energy / reference_shadow_energy
        if reference_shadow_energy > 1e-6
        else 1.0 + candidate_shadow_energy
    )
    shadow_local_excess = float(
        np.mean(
            np.maximum(
                np.maximum(-candidate_values, 0.0) - np.maximum(-reference_values, 0.0),
                0.0,
            )
        )
    )
    shadow_local_excess_ratio = (
        shadow_local_excess / reference_shadow_energy
        if reference_shadow_energy > 1e-6
        else shadow_local_excess
    )
    salient = np.abs(reference_values) >= max(3.0, reference_range * 0.15)
    sign_agreement = (
        float(np.mean(np.sign(reference_values[salient]) == np.sign(candidate_values[salient])))
        if np.any(salient)
        else 0.0
    )
    score = (
        max(0.0, correlation) * 0.40
        + error_score * 0.25
        + range_ratio * 0.20
        + sign_agreement * 0.15
    )
    return {
        "score": float(score),
        "correlation": correlation,
        "errorScore": float(error_score),
        "rangeRatio": float(range_ratio),
        "signAgreement": sign_agreement,
        "referenceDynamicRange": reference_range,
        "candidateDynamicRange": candidate_range,
        "referenceHighlightAmplitude": reference_highlight,
        "candidateHighlightAmplitude": candidate_highlight,
        "highlightAmplitudeRatio": float(highlight_amplitude_ratio),
        "referenceShadowAmplitude": reference_shadow,
        "candidateShadowAmplitude": candidate_shadow,
        "shadowAmplitudeRatio": float(shadow_amplitude_ratio),
        "shadowOvershootRatio": float(shadow_overshoot_ratio),
        "referenceShadowEnergy": reference_shadow_energy,
        "candidateShadowEnergy": candidate_shadow_energy,
        "shadowEnergyOvershootRatio": float(shadow_energy_overshoot_ratio),
        "shadowLocalExcessRatio": float(shadow_local_excess_ratio),
        "meanAbsoluteError": mean_absolute_error,
    }


def _donut_shadow_sector_metrics(
    reference_delta: np.ndarray,
    candidate_delta: np.ndarray,
    ring: np.ndarray,
    region: BevelRegion,
) -> dict[str, Any]:
    if region.preset != "donut":
        return {"shadowSectorOvershootRatio": 1.0, "shadowSectors": []}

    height, width = ring.shape
    yy, xx = np.mgrid[:height, :width]
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    radius_x = max(width / 2, 1.0)
    radius_y = max(height / 2, 1.0)
    outer = ((xx - center_x) / radius_x) ** 2 + ((yy - center_y) / radius_y) ** 2 <= 1
    inner_radius_x = radius_x - width * region.geometry_inset_x_ratio
    inner_radius_y = radius_y - height * region.geometry_inset_y_ratio
    if inner_radius_x <= 0 or inner_radius_y <= 0:
        return {"shadowSectorOvershootRatio": 1.0, "shadowSectors": []}
    inner = (
        ((xx - center_x) / inner_radius_x) ** 2
        + ((yy - center_y) / inner_radius_y) ** 2
        < 1
    )
    outer_distance = cv2.distanceTransform(
        np.pad(outer.astype(np.uint8), 1), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
    )[1:-1, 1:-1]
    inner_distance = cv2.distanceTransform(
        np.pad((~inner).astype(np.uint8), 1), cv2.DIST_L2, cv2.DIST_MASK_PRECISE
    )[1:-1, 1:-1]
    angle = (
        np.degrees(
            np.arctan2(
                (yy - center_y) / radius_y,
                (xx - center_x) / radius_x,
            )
        )
        + 360
    ) % 360
    sectors: list[dict[str, Any]] = []
    for contour, contour_mask in (
        ("outer", ring & (outer_distance <= inner_distance)),
        ("inner", ring & (inner_distance < outer_distance)),
    ):
        for start_angle in range(0, 360, DONUT_SHADOW_SECTOR_DEGREES):
            mask = (
                contour_mask
                & (angle >= start_angle)
                & (angle < start_angle + DONUT_SHADOW_SECTOR_DEGREES)
            )
            pixel_count = int(np.count_nonzero(mask))
            if pixel_count < 20:
                continue
            reference_shadow = float(np.mean(np.maximum(-reference_delta[mask], 0.0)))
            if reference_shadow < MIN_DONUT_SECTOR_REFERENCE_SHADOW:
                continue
            candidate_shadow = float(np.mean(np.maximum(-candidate_delta[mask], 0.0)))
            sectors.append(
                {
                    "contour": contour,
                    "startAngle": start_angle,
                    "endAngle": start_angle + DONUT_SHADOW_SECTOR_DEGREES,
                    "pixelCount": pixel_count,
                    "referenceShadowEnergy": reference_shadow,
                    "candidateShadowEnergy": candidate_shadow,
                    "overshootRatio": candidate_shadow / reference_shadow,
                }
            )
    return {
        "shadowSectorOvershootRatio": max(
            (sector["overshootRatio"] for sector in sectors), default=1.0
        ),
        "shadowSectors": sectors,
    }


def compute_bevel_ring_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    region: BevelRegion,
    *,
    score_threshold: float = SCORE_THRESHOLD,
    corner_score_threshold: float = CORNER_SCORE_THRESHOLD,
    range_ratio_threshold: float = RANGE_RATIO_THRESHOLD,
    highlight_amplitude_ratio_threshold: float = HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD,
    picture_highlight_amplitude_ratio_threshold: float = (
        PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD
    ),
    shadow_amplitude_ratio_threshold: float = SHADOW_AMPLITUDE_RATIO_THRESHOLD,
    shadow_overshoot_ratio_threshold: float = SHADOW_OVERSHOOT_RATIO_THRESHOLD,
    solid_donut_shadow_overshoot_ratio_threshold: float = (
        SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD
    ),
    solid_donut_shadow_energy_overshoot_ratio_threshold: float = (
        SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD
    ),
    solid_donut_shadow_local_excess_ratio_threshold: float = (
        SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD
    ),
    solid_donut_shadow_sector_overshoot_ratio_threshold: float = (
        SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD
    ),
) -> dict[str, Any]:
    reference, candidate = _common_images(reference, candidate)
    image_height, image_width = reference.shape[:2]
    left = max(0, min(image_width - 1, round(region.x * image_width)))
    top = max(0, min(image_height - 1, round(region.y * image_height)))
    right = max(left + 1, min(image_width, round((region.x + region.width) * image_width)))
    bottom = max(top + 1, min(image_height, round((region.y + region.height) * image_height)))
    reference_crop = reference[top:bottom, left:right]
    candidate_crop = candidate[top:bottom, left:right]
    crop_height, crop_width = reference_crop.shape[:2]
    band_width = math.sqrt(
        max(region.bevel_width_x * image_width, 0.0)
        * max(region.bevel_width_y * image_height, 0.0)
    )
    if band_width < MIN_EVALUABLE_BAND_PX:
        return {
            "evaluable": False,
            "passed": True,
            "reason": "bevel-band-below-resolution-floor",
            "boundsPx": [left, top, right - left, bottom - top],
            "bandWidthPx": float(band_width),
            "thresholds": {
                "minimumBandWidthPx": MIN_EVALUABLE_BAND_PX,
                "score": score_threshold,
                "cornerScore": corner_score_threshold,
                "rangeRatio": range_ratio_threshold,
                "highlightAmplitudeRatio": highlight_amplitude_ratio_threshold,
                "pictureHighlightAmplitudeRatio": picture_highlight_amplitude_ratio_threshold,
                "shadowAmplitudeRatio": shadow_amplitude_ratio_threshold,
                "shadowOvershootRatio": shadow_overshoot_ratio_threshold,
                "solidDonutShadowOvershootRatio": (
                    solid_donut_shadow_overshoot_ratio_threshold
                ),
                "solidDonutShadowEnergyOvershootRatio": (
                    solid_donut_shadow_energy_overshoot_ratio_threshold
                ),
                "solidDonutShadowLocalExcessRatio": (
                    solid_donut_shadow_local_excess_ratio_threshold
                ),
                "solidDonutShadowSectorOvershootRatio": (
                    solid_donut_shadow_sector_overshoot_ratio_threshold
                ),
            },
        }
    unsupported_reason = None
    if not math.isclose(region.rotation_degrees % 360.0, 0.0, abs_tol=1e-6):
        unsupported_reason = "shape-rotation-unsupported"
    geometry = _geometry_mask(crop_width, crop_height, region)
    if geometry is None:
        unsupported_reason = unsupported_reason or "geometry-mask-unsupported"
    if unsupported_reason is not None:
        return {
            "evaluable": False,
            "passed": False,
            "reason": unsupported_reason,
            "boundsPx": [left, top, right - left, bottom - top],
            "bandWidthPx": float(band_width),
            "thresholds": {
                "score": score_threshold,
                "cornerScore": corner_score_threshold,
                "rangeRatio": range_ratio_threshold,
                "highlightAmplitudeRatio": highlight_amplitude_ratio_threshold,
                "pictureHighlightAmplitudeRatio": picture_highlight_amplitude_ratio_threshold,
                "shadowAmplitudeRatio": shadow_amplitude_ratio_threshold,
                "shadowOvershootRatio": shadow_overshoot_ratio_threshold,
                "solidDonutShadowOvershootRatio": (
                    solid_donut_shadow_overshoot_ratio_threshold
                ),
                "solidDonutShadowEnergyOvershootRatio": (
                    solid_donut_shadow_energy_overshoot_ratio_threshold
                ),
                "solidDonutShadowLocalExcessRatio": (
                    solid_donut_shadow_local_excess_ratio_threshold
                ),
                "solidDonutShadowSectorOvershootRatio": (
                    solid_donut_shadow_sector_overshoot_ratio_threshold
                ),
            },
        }
    padded = np.pad(geometry.astype(np.uint8), 1)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
    ring = geometry & (distance > max(1.0, band_width * 0.10)) & (distance <= band_width * 1.35)
    core = geometry & (distance >= band_width * 1.75)
    if np.count_nonzero(core) < 20:
        core = geometry & (distance >= np.percentile(distance[geometry], 70))
    sigma = max(0.6, band_width / 8.0)
    reference_luma = cv2.GaussianBlur(_luminance(reference_crop), (0, 0), sigma)
    candidate_luma = cv2.GaussianBlur(_luminance(candidate_crop), (0, 0), sigma)
    reference_delta = reference_luma - float(np.median(reference_luma[core]))
    candidate_delta = candidate_luma - float(np.median(candidate_luma[core]))
    overall = {
        **_field_score(reference_delta, candidate_delta, ring),
        **_donut_shadow_sector_metrics(reference_delta, candidate_delta, ring, region),
    }

    corner_required = region.preset == "roundRect" and region.corner_radius_ratio > 0
    corner_mask = ring
    if corner_required:
        radius = min(crop_width, crop_height) * region.corner_radius_ratio
        extent = radius + band_width * 1.35
        yy, xx = np.mgrid[:crop_height, :crop_width]
        corner_mask = ring & (
            ((xx < extent) & (yy < extent))
            | ((xx >= crop_width - extent) & (yy < extent))
            | ((xx < extent) & (yy >= crop_height - extent))
            | ((xx >= crop_width - extent) & (yy >= crop_height - extent))
        )
    corner = _field_score(reference_delta, candidate_delta, corner_mask)
    highlight_threshold = (
        picture_highlight_amplitude_ratio_threshold
        if region.surface == "picture"
        else highlight_amplitude_ratio_threshold
    )
    shadow_overshoot_threshold = (
        solid_donut_shadow_overshoot_ratio_threshold
        if region.surface == "shape" and region.preset == "donut"
        else shadow_overshoot_ratio_threshold
    )
    shadow_energy_overshoot_threshold = (
        solid_donut_shadow_energy_overshoot_ratio_threshold
        if region.surface == "shape" and region.preset == "donut"
        else math.inf
    )
    shadow_local_excess_threshold = (
        solid_donut_shadow_local_excess_ratio_threshold
        if region.surface == "shape" and region.preset == "donut"
        else math.inf
    )
    shadow_sector_overshoot_threshold = (
        solid_donut_shadow_sector_overshoot_ratio_threshold
        if region.surface == "shape" and region.preset == "donut"
        else math.inf
    )
    passed = (
        overall["score"] >= score_threshold
        and overall["rangeRatio"] >= range_ratio_threshold
        and overall["highlightAmplitudeRatio"] >= highlight_threshold
        and overall["shadowAmplitudeRatio"] >= shadow_amplitude_ratio_threshold
        and overall["shadowOvershootRatio"] <= shadow_overshoot_threshold
        and overall["shadowEnergyOvershootRatio"] <= shadow_energy_overshoot_threshold
        and overall["shadowLocalExcessRatio"] <= shadow_local_excess_threshold
        and overall["shadowSectorOvershootRatio"] <= shadow_sector_overshoot_threshold
        and (not corner_required or corner["score"] >= corner_score_threshold)
    )
    return {
        **overall,
        "evaluable": True,
        "cornerScore": corner["score"],
        "cornerCorrelation": corner["correlation"],
        "cornerRequired": corner_required,
        "ringPixelCount": int(np.count_nonzero(ring)),
        "cornerPixelCount": int(np.count_nonzero(corner_mask)),
        "boundsPx": [left, top, right - left, bottom - top],
        "bandWidthPx": float(band_width),
        "thresholds": {
            "score": score_threshold,
            "cornerScore": corner_score_threshold,
            "rangeRatio": range_ratio_threshold,
            "highlightAmplitudeRatio": highlight_amplitude_ratio_threshold,
            "pictureHighlightAmplitudeRatio": picture_highlight_amplitude_ratio_threshold,
            "shadowAmplitudeRatio": shadow_amplitude_ratio_threshold,
            "shadowOvershootRatio": shadow_overshoot_ratio_threshold,
            "solidDonutShadowOvershootRatio": solid_donut_shadow_overshoot_ratio_threshold,
            "solidDonutShadowEnergyOvershootRatio": (
                solid_donut_shadow_energy_overshoot_ratio_threshold
            ),
            "solidDonutShadowLocalExcessRatio": (
                solid_donut_shadow_local_excess_ratio_threshold
            ),
            "solidDonutShadowSectorOvershootRatio": (
                solid_donut_shadow_sector_overshoot_ratio_threshold
            ),
        },
        "passed": passed,
    }


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
        raise ValueError(f"bevel artifact must be inside the repository: {path}") from error


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON report must be an object: {path}")
    return value


def _equivalent_slide_pairs(repo: Path, case_id: str) -> list[tuple[int, int]]:
    oracle_root = repo / "test/e2e/oracle"
    matches = sorted(oracle_root.glob(f"*/{case_id}.json"))
    if not matches:
        return []
    if len(matches) > 1:
        raise ValueError(f"case metadata is ambiguous: {case_id}")
    payload = _load_json(matches[0])
    assertions = payload.get("assertions")
    if assertions is None:
        return []
    if not isinstance(assertions, Mapping):
        raise ValueError(f"case assertions must be an object: {case_id}")
    values = assertions.get("equivalentSlidePairs", [])
    if not isinstance(values, list):
        raise ValueError(f"equivalentSlidePairs must be a list: {case_id}")
    pairs: list[tuple[int, int]] = []
    for value in values:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(not isinstance(index, int) or isinstance(index, bool) or index < 0 for index in value)
            or value[0] == value[1]
        ):
            raise ValueError(f"equivalent slide pair is invalid: {case_id}")
        pair = (value[0], value[1])
        if pair in pairs:
            raise ValueError(f"equivalent slide pair is duplicated: {case_id}")
        pairs.append(pair)
    return pairs


def build_bevel_report(
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
        source_path_value = source_info.get("path")
        if not isinstance(source_path_value, str):
            raise ValueError(f"case report is missing source path: {case_id}")
        source_path = repo / source_path_value
        regions = extract_shape3d_regions(source_path)
        slide_results: list[dict[str, Any]] = []
        for slide in case_report.get("perSlide", []):
            if not isinstance(slide, Mapping) or slide.get("hidden") is True:
                continue
            slide_index = slide.get("slideIdx")
            if not isinstance(slide_index, int):
                continue
            slide_regions = [region for region in regions if region.slide_index == slide_index]
            if not slide_regions:
                continue
            reference_path = reports_dir / f"{case_id}_slide{slide_index}_pdf.png"
            candidate_path = reports_dir / f"{case_id}_slide{slide_index}_html.png"
            render_artifacts = slide.get("renderArtifacts")
            if not isinstance(render_artifacts, Mapping):
                raise ValueError(f"native report is missing render artifacts: {case_id} slide {slide_index}")
            artifact_values = {}
            for kind, path in (("reference", reference_path), ("candidate", candidate_path)):
                artifact = render_artifacts.get(kind)
                if not isinstance(artifact, Mapping):
                    raise ValueError(
                        f"native report is missing {kind} artifact: {case_id} slide {slide_index}"
                    )
                actual = {
                    "path": _repository_relative(path, repo),
                    "sizeBytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                if any(artifact.get(key) != value for key, value in actual.items()):
                    raise ValueError(
                        f"native report artifact does not match {kind} raster: "
                        f"{case_id} slide {slide_index}"
                    )
                artifact_values[kind] = actual
            reference = np.asarray(Image.open(reference_path).convert("RGB"))
            candidate = np.asarray(Image.open(candidate_path).convert("RGB"))
            region_results = [
                {
                    "region": asdict(region),
                    "metrics": compute_bevel_ring_metrics(reference, candidate, region),
                }
                for region in slide_regions
            ]
            slide_results.append(
                {
                    "slideIdx": slide_index,
                    "referencePath": artifact_values["reference"]["path"],
                    "candidatePath": artifact_values["candidate"]["path"],
                    "referenceSha256": artifact_values["reference"]["sha256"],
                    "candidateSha256": artifact_values["candidate"]["sha256"],
                    "regions": region_results,
                    "passed": all(item["metrics"]["passed"] for item in region_results),
                }
            )
        slides_by_index = {slide["slideIdx"]: slide for slide in slide_results}
        equivalence_pairs: list[dict[str, Any]] = []
        for left_index, right_index in _equivalent_slide_pairs(repo, case_id):
            left = slides_by_index.get(left_index)
            right = slides_by_index.get(right_index)
            if left is None or right is None:
                raise ValueError(
                    f"equivalent slide pair is missing bevel evidence: "
                    f"{case_id} slides {left_index}/{right_index}"
                )
            reference_equal = left["referenceSha256"] == right["referenceSha256"]
            candidate_equal = left["candidateSha256"] == right["candidateSha256"]
            equivalence_pairs.append(
                {
                    "leftSlideIdx": left_index,
                    "rightSlideIdx": right_index,
                    "referenceEqual": reference_equal,
                    "candidateEqual": candidate_equal,
                    "passed": reference_equal and candidate_equal,
                }
            )
        applicable = any(
            region["metrics"].get("evaluable") is True
            for slide in slide_results
            for region in slide["regions"]
        )
        case_passed = all(slide["passed"] for slide in slide_results) and (
            not regions or bool(slide_results)
        ) and all(pair["passed"] for pair in equivalence_pairs)
        cases.append(
            {
                "caseId": case_id,
                "sourceSha256": source_info.get("sha256"),
                "groundTruthSha256": ground_truth_info.get("combinedSha256"),
                "applicable": applicable,
                "passed": case_passed,
                "slides": slide_results,
                "equivalencePairs": equivalence_pairs,
            }
        )
    applicable_count = sum(1 for case in cases if case["applicable"])
    return {
        "schemaVersion": 8,
        "renderer": dict(renderer or {}),
        "thresholds": {
            "score": SCORE_THRESHOLD,
            "cornerScore": CORNER_SCORE_THRESHOLD,
            "rangeRatio": RANGE_RATIO_THRESHOLD,
            "highlightAmplitudeRatio": HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD,
            "pictureHighlightAmplitudeRatio": PICTURE_HIGHLIGHT_AMPLITUDE_RATIO_THRESHOLD,
            "shadowAmplitudeRatio": SHADOW_AMPLITUDE_RATIO_THRESHOLD,
            "shadowOvershootRatio": SHADOW_OVERSHOOT_RATIO_THRESHOLD,
            "solidDonutShadowOvershootRatio": (
                SOLID_DONUT_SHADOW_OVERSHOOT_RATIO_THRESHOLD
            ),
            "solidDonutShadowEnergyOvershootRatio": (
                SOLID_DONUT_SHADOW_ENERGY_OVERSHOOT_RATIO_THRESHOLD
            ),
            "solidDonutShadowLocalExcessRatio": (
                SOLID_DONUT_SHADOW_LOCAL_EXCESS_RATIO_THRESHOLD
            ),
            "solidDonutShadowSectorOvershootRatio": (
                SOLID_DONUT_SHADOW_SECTOR_OVERSHOOT_RATIO_THRESHOLD
            ),
        },
        "caseResults": sorted(cases, key=lambda case: case["caseId"]),
        "applicableCaseCount": applicable_count,
        "passed": applicable_count > 0 and all(case["passed"] for case in cases),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate local Shape 3D bevel-ring fidelity")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--case-report", action="append", required=True)
    parser.add_argument("--reports-dir", default="test/e2e/reports")
    parser.add_argument(
        "--out",
        default="test/e2e/reports/capability-loop/shape3d-bevel-local.json",
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
        report = build_bevel_report(
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
        f"evaluated {report['applicableCaseCount']} applicable bevel case(s); "
        f"passed={report['passed']} -> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
