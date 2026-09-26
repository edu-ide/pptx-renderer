"""Scoped visual evidence for ordinary single-chart Cartesian slides.

The full-slide oracle remains the primary regression gate.  These metrics expose
whether the plot rectangle and chromatic data series agree with the native
PowerPoint raster when page whitespace and text antialiasing dominate SSIM.
"""

from __future__ import annotations

import math
import posixpath
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import cv2
import numpy as np
from scipy.spatial import cKDTree
from skimage.metrics import structural_similarity as ssim


CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {
    "a": DRAWING_NS,
    "c": CHART_NS,
    "p": PRESENTATION_NS,
    "r": OFFICE_REL_NS,
}
SLIDE_PART = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")
SUPPORTED_CHART_ELEMENTS = {"barChart", "lineChart", "areaChart"}
MAX_AGGREGATE_SERIES_COUNT = 8
LEGEND_POSITIONS = {
    "r": "right",
    "l": "left",
    "t": "top",
    "b": "bottom",
    "tr": "topRight",
}

PLOT_BOUND_MAX_ERROR_RATIO = 0.01
MIN_SERIES_PIXEL_COUNT = 32
SERIES_SATURATION_FLOOR = 45
SERIES_VALUE_CEILING = 249
SERIES_THRESHOLDS = {
    "column": {
        "fgIouTolerant": 0.90,
        "areaRatio": 0.88,
        "chamferScore": 0.995,
        "centroidDistance": 0.005,
        "colorMatchedPixelRatio": 0.95,
        "meanLabDistance": 0.12,
    },
    "bar": {
        "fgIouTolerant": 0.90,
        "areaRatio": 0.88,
        "chamferScore": 0.995,
        "centroidDistance": 0.005,
        "colorMatchedPixelRatio": 0.95,
        "meanLabDistance": 0.12,
    },
    "area": {
        "fgIouTolerant": 0.90,
        "areaRatio": 0.88,
        "chamferScore": 0.995,
        "centroidDistance": 0.005,
        "colorMatchedPixelRatio": 0.95,
        "meanLabDistance": 0.12,
    },
    "line": {
        "fgIouTolerant": 0.65,
        "areaRatio": 0.88,
        "chamferScore": 0.995,
        "centroidDistance": 0.005,
        "colorMatchedPixelRatio": 0.95,
        "meanLabDistance": 0.12,
    },
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_value(node: ET.Element, name: str, default: str) -> str:
    child = node.find(f"c:{name}", NS)
    if child is None:
        return default
    return child.attrib.get("val", default)


def _resolve_internal_target(source_part: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _ordered_slide_parts(
    archive: ZipFile,
) -> tuple[list[tuple[int, str]], tuple[int, int] | None]:
    fallback = []
    for name in archive.namelist():
        match = SLIDE_PART.match(name)
        if match:
            fallback.append((int(match.group(1)) - 1, name))
    fallback.sort()

    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        relationships = ET.fromstring(
            archive.read("ppt/_rels/presentation.xml.rels")
        )
    except (KeyError, ET.ParseError):
        return fallback, None

    slide_size_node = presentation.find("p:sldSz", NS)
    slide_size = None
    if slide_size_node is not None:
        try:
            slide_width = int(slide_size_node.attrib["cx"])
            slide_height = int(slide_size_node.attrib["cy"])
            if slide_width > 0 and slide_height > 0:
                slide_size = (slide_width, slide_height)
        except (KeyError, TypeError, ValueError):
            slide_size = None

    targets = {}
    for relationship in relationships.findall(f"{{{REL_NS}}}Relationship"):
        if relationship.attrib.get("TargetMode", "Internal") != "Internal":
            continue
        if not relationship.attrib.get("Type", "").endswith("/slide"):
            continue
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if relationship_id and target:
            targets[relationship_id] = _resolve_internal_target(
                "ppt/presentation.xml",
                target,
            )

    ordered = []
    slide_ids = presentation.findall("p:sldIdLst/p:sldId", NS)
    for slide_index, slide_id in enumerate(slide_ids):
        relationship_id = slide_id.attrib.get(f"{{{OFFICE_REL_NS}}}id")
        slide_path = targets.get(relationship_id)
        if slide_path and slide_path.startswith("ppt/slides/"):
            ordered.append((slide_index, slide_path))
    return (ordered or fallback), slide_size


def _chart_frame(
    graphic_frame: ET.Element,
    slide_size: tuple[int, int] | None,
) -> dict[str, float] | None:
    if slide_size is None:
        return None
    transform = graphic_frame.find("p:xfrm", NS)
    if transform is None:
        return None
    offset = transform.find("a:off", NS)
    extent = transform.find("a:ext", NS)
    if offset is None or extent is None:
        return None
    try:
        x = int(offset.attrib["x"])
        y = int(offset.attrib["y"])
        width = int(extent.attrib["cx"])
        height = int(extent.attrib["cy"])
    except (KeyError, TypeError, ValueError):
        return None
    slide_width, slide_height = slide_size
    if width <= 0 or height <= 0:
        return None
    return {
        "left": x / slide_width,
        "top": y / slide_height,
        "right": (x + width) / slide_width,
        "bottom": (y + height) / slide_height,
    }


def _has_ancestor(
    node: ET.Element,
    parent_by_child: dict[ET.Element, ET.Element],
    ancestor_name: str,
) -> bool:
    parent = parent_by_child.get(node)
    while parent is not None:
        if _local_name(parent.tag) == ancestor_name:
            return True
        parent = parent_by_child.get(parent)
    return False


def _chart_target(
    archive: ZipFile,
    slide_path: str,
    relationship_id: str,
) -> str | None:
    slide_name = posixpath.basename(slide_path)
    rels_path = posixpath.join(
        posixpath.dirname(slide_path),
        "_rels",
        f"{slide_name}.rels",
    )
    try:
        rels_root = ET.fromstring(archive.read(rels_path))
    except (KeyError, ET.ParseError):
        return None
    for relationship in rels_root.findall(f"{{{REL_NS}}}Relationship"):
        if relationship.attrib.get("Id") != relationship_id:
            continue
        if relationship.attrib.get("TargetMode", "Internal") != "Internal":
            return None
        if not relationship.attrib.get("Type", "").endswith("/chart"):
            return None
        target = relationship.attrib.get("Target")
        if not target:
            return None
        resolved = _resolve_internal_target(slide_path, target)
        if not resolved.startswith("ppt/charts/"):
            return None
        return resolved
    return None


def _profile_for_chart(archive: ZipFile, chart_path: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(archive.read(chart_path))
    except (KeyError, ET.ParseError):
        return {
            "evaluable": False,
            "reason": "chart-part-unreadable",
            "chartPath": chart_path,
        }

    plot_area = root.find(".//c:plotArea", NS)
    if plot_area is None:
        return {
            "evaluable": False,
            "reason": "plot-area-missing",
            "chartPath": chart_path,
        }
    chart_elements = [
        child
        for child in list(plot_area)
        if _local_name(child.tag).endswith("Chart")
    ]
    if len(chart_elements) > 1:
        return {
            "evaluable": False,
            "reason": "combo-chart",
            "chartPath": chart_path,
        }
    if not chart_elements:
        return {
            "evaluable": False,
            "reason": "chart-family-missing",
            "chartPath": chart_path,
        }

    chart_element = chart_elements[0]
    chart_kind = _local_name(chart_element.tag)
    if chart_kind not in SUPPORTED_CHART_ELEMENTS:
        return {
            "evaluable": False,
            "reason": "unsupported-chart-family",
            "chartPath": chart_path,
        }

    if chart_kind == "barChart":
        bar_direction = _child_value(chart_element, "barDir", "col")
        family = "bar" if bar_direction == "bar" else "column"
        orientation = "horizontal" if family == "bar" else "vertical"
        grouping_default = "clustered"
    else:
        family = "line" if chart_kind == "lineChart" else "area"
        orientation = "vertical"
        grouping_default = "standard"

    chart = root.find(".//c:chart", NS)
    legend = chart.find("c:legend", NS) if chart is not None else None
    legend_position_value = (
        _child_value(legend, "legendPos", "right") if legend is not None else "none"
    )
    legend_position = LEGEND_POSITIONS.get(legend_position_value, legend_position_value)
    return {
        "evaluable": True,
        "family": family,
        "orientation": orientation,
        "grouping": _child_value(chart_element, "grouping", grouping_default),
        "legendPosition": legend_position,
        "seriesCount": len(chart_element.findall("c:ser", NS)),
        "chartPath": chart_path,
    }


def extract_cartesian_chart_profiles(pptx_path: Path) -> dict[int, dict[str, Any]]:
    """Return a zero-based profile for each slide containing a chart frame."""
    profiles: dict[int, dict[str, Any]] = {}
    try:
        archive = ZipFile(pptx_path)
    except (OSError, ValueError, BadZipFile):
        return profiles
    with archive:
        slide_parts, slide_size = _ordered_slide_parts(archive)
        for slide_index, slide_path in slide_parts:
            try:
                slide_root = ET.fromstring(archive.read(slide_path))
            except (KeyError, ET.ParseError):
                continue
            parent_by_child = {
                child: parent
                for parent in slide_root.iter()
                for child in list(parent)
            }
            chart_frames = []
            for graphic_frame in slide_root.findall(".//p:graphicFrame", NS):
                chart_frames.extend(
                    (chart_ref, graphic_frame)
                    for chart_ref in graphic_frame.findall(".//c:chart", NS)
                )
            chart_refs = [chart_ref for chart_ref, _frame in chart_frames]
            if not chart_refs:
                continue
            if len(chart_refs) != 1:
                profiles[slide_index] = {
                    "evaluable": False,
                    "reason": "multiple-chart-frames",
                }
                continue
            chart_ref, graphic_frame = chart_frames[0]
            relationship_id = chart_ref.attrib.get(f"{{{OFFICE_REL_NS}}}id")
            chart_path = (
                _chart_target(archive, slide_path, relationship_id)
                if relationship_id
                else None
            )
            if chart_path is None:
                profiles[slide_index] = {
                    "evaluable": False,
                    "reason": "chart-relationship-unresolved",
                }
                continue
            profile = _profile_for_chart(archive, chart_path)
            if not profile.get("evaluable"):
                profiles[slide_index] = profile
                continue
            if _has_ancestor(graphic_frame, parent_by_child, "grpSp"):
                profile.update(
                    {
                        "evaluable": False,
                        "reason": "grouped-chart-frame-unsupported",
                    }
                )
                profiles[slide_index] = profile
                continue
            frame = _chart_frame(graphic_frame, slide_size)
            if frame is None:
                profile.update(
                    {
                        "evaluable": False,
                        "reason": "chart-frame-unresolved",
                    }
                )
                profiles[slide_index] = profile
                continue
            profile["chartFrame"] = frame
            profiles[slide_index] = profile
    return profiles


def _normalize_candidate(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    if reference.ndim != 3 or reference.shape[2] < 3:
        raise ValueError("reference chart raster must be RGB")
    if candidate.ndim != 3 or candidate.shape[2] < 3:
        raise ValueError("candidate chart raster must be RGB")
    if candidate.shape[:2] == reference.shape[:2]:
        return candidate[..., :3]
    return cv2.resize(
        candidate[..., :3],
        (reference.shape[1], reference.shape[0]),
        interpolation=cv2.INTER_AREA,
    )


def _long_components(mask: np.ndarray, orientation: str) -> list[tuple[int, int, int, int]]:
    height, width = mask.shape
    if orientation == "horizontal":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, width // 12), 1))
        minimum = width * 0.20
        component_length = lambda component_width, _component_height: component_width
    else:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(3, height // 12)))
        minimum = height * 0.18
        component_length = lambda _component_width, component_height: component_height
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    _, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    return [
        (int(x), int(y), int(component_width), int(component_height))
        for x, y, component_width, component_height, _area in stats[1:]
        if component_length(component_width, component_height) >= minimum
    ]


def detect_cartesian_plot_bounds(
    image: np.ndarray,
    orientation: str,
    search_bounds: dict[str, int] | None = None,
) -> dict[str, int] | None:
    """Detect neutral plot axes/grid while excluding chromatic chart series."""
    if orientation not in {"horizontal", "vertical"}:
        raise ValueError(f"unsupported Cartesian orientation: {orientation}")
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("chart raster must be RGB")
    height, width = image.shape[:2]
    if search_bounds is None:
        crop_left, crop_top, crop_right, crop_bottom = 0, 0, width, height
    else:
        crop_left = max(0, min(width - 1, search_bounds["left"]))
        crop_top = max(0, min(height - 1, search_bounds["top"]))
        crop_right = max(crop_left + 1, min(width, search_bounds["right"]))
        crop_bottom = max(crop_top + 1, min(height, search_bounds["bottom"]))
    rgb = image[crop_top:crop_bottom, crop_left:crop_right, :3].astype(np.int16)
    neutral_dark = (
        (rgb.max(axis=2) - rgb.min(axis=2) <= 10)
        & (rgb.mean(axis=2) < 220)
    )
    horizontal = _long_components(neutral_dark, "horizontal")
    vertical = _long_components(neutral_dark, "vertical")
    if not horizontal or not vertical:
        return None
    if orientation == "vertical" and len(horizontal) < 3:
        return None
    if orientation == "horizontal" and len(vertical) < 3:
        return None

    if orientation == "horizontal":
        left = min([segment[0] for segment in horizontal] + [segment[0] for segment in vertical])
        top = min(y for _x, y, _width, _height in vertical)
        right = max(x + segment_width - 1 for x, _y, segment_width, _height in vertical)
        bottom = max(y + segment_height - 1 for _x, y, _width, segment_height in horizontal)
    else:
        left = min(x for x, _y, _width, _height in vertical)
        top = min(y for _x, y, _width, _height in vertical)
        right = max(x + segment_width - 1 for x, _y, segment_width, _height in horizontal)
        bottom = max(y + segment_height - 1 for _x, y, _width, segment_height in horizontal)
    if right <= left or bottom <= top:
        return None
    return {
        "left": left + crop_left,
        "top": top + crop_top,
        "right": right + crop_left,
        "bottom": bottom + crop_top,
    }


def _plot_background(
    image: np.ndarray,
    bounds: dict[str, int],
) -> tuple[str, np.ndarray | None]:
    left, top, right, bottom = (
        bounds["left"],
        bounds["top"],
        bounds["right"],
        bounds["bottom"],
    )
    plot_width = max(1, right - left + 1)
    plot_height = max(1, bottom - top + 1)
    patch_radius = max(2, min(plot_width, plot_height) // 60)
    inset = max(patch_radius + 2, min(plot_width, plot_height) // 30)
    centers = (
        (left + inset, top + inset),
        (right - inset, top + inset),
        (left + inset, bottom - inset),
        (right - inset, bottom - inset),
    )
    colors = []
    for center_x, center_y in centers:
        x0 = max(left, center_x - patch_radius)
        x1 = min(right + 1, center_x + patch_radius + 1)
        y0 = max(top, center_y - patch_radius)
        y1 = min(bottom + 1, center_y + patch_radius + 1)
        colors.append(np.median(image[y0:y1, x0:x1, :3], axis=(0, 1)))
    samples = np.asarray(colors, dtype=np.uint8).reshape(-1, 1, 3)
    hsv = cv2.cvtColor(samples, cv2.COLOR_RGB2HSV).reshape(-1, 3)
    chromatic = hsv[:, 1] >= SERIES_SATURATION_FLOOR
    if int(chromatic.sum()) < 3:
        return "neutral", None
    chromatic_colors = samples.reshape(-1, 3)[chromatic].astype(np.float64)
    background = np.median(chromatic_colors, axis=0)
    if np.max(np.linalg.norm(chromatic_colors - background, axis=1)) > 24.0:
        return "unsupported-chromatic", None
    return "solid-chromatic", background


def _series_mask(
    image: np.ndarray,
    background: np.ndarray | None = None,
) -> np.ndarray:
    hsv = cv2.cvtColor(image[..., :3], cv2.COLOR_RGB2HSV)
    mask = (hsv[..., 1] >= SERIES_SATURATION_FLOOR) & (hsv[..., 2] <= SERIES_VALUE_CEILING)
    if background is not None:
        rgb_distance = np.linalg.norm(
            image[..., :3].astype(np.float64) - background,
            axis=2,
        )
        mask &= rgb_distance > 24.0
    return mask


def _mask_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    reference_image: np.ndarray | None = None,
    candidate_image: np.ndarray | None = None,
) -> dict[str, float | int]:
    reference_count = int(reference.sum())
    candidate_count = int(candidate.sum())
    union = int(np.logical_or(reference, candidate).sum())
    intersection = int(np.logical_and(reference, candidate).sum())
    raw_iou = float(intersection / union) if union else 1.0

    kernel = np.ones((3, 3), dtype=np.uint8)
    reference_dilated = cv2.dilate(reference.astype(np.uint8), kernel, iterations=1).astype(bool)
    candidate_dilated = cv2.dilate(candidate.astype(np.uint8), kernel, iterations=1).astype(bool)
    tolerant_union = int(np.logical_or(reference_dilated, candidate_dilated).sum())
    tolerant_intersection = int(np.logical_and(reference_dilated, candidate_dilated).sum())
    tolerant_iou = (
        float(tolerant_intersection / tolerant_union) if tolerant_union else 1.0
    )

    if max(reference_count, candidate_count) == 0:
        area_ratio = 1.0
    else:
        area_ratio = float(min(reference_count, candidate_count) / max(reference_count, candidate_count))

    height, width = reference.shape
    diagonal = math.hypot(height, width)
    if reference_count and candidate_count:
        reference_points = np.argwhere(reference)
        candidate_points = np.argwhere(candidate)
        reference_centroid = reference_points.mean(axis=0)
        candidate_centroid = candidate_points.mean(axis=0)
        centroid_distance = float(
            np.linalg.norm(reference_centroid - candidate_centroid) / max(diagonal, 1.0)
        )
        reference_tree = cKDTree(reference_points)
        candidate_tree = cKDTree(candidate_points)
        reference_to_candidate, nearest_candidate = candidate_tree.query(reference_points)
        candidate_to_reference, _ = reference_tree.query(candidate_points)
        chamfer_distance = float(
            (reference_to_candidate.mean() + candidate_to_reference.mean()) / 2.0
        )
        chamfer_score = max(0.0, 1.0 - chamfer_distance / max(diagonal, 1.0))
        if reference_image is not None and candidate_image is not None:
            color_matches = reference_to_candidate <= 3.0
            color_matched_pixel_ratio = float(color_matches.mean())
            matching_indexes = np.flatnonzero(color_matches)
            if len(matching_indexes) == 0:
                mean_lab_distance = 1.0
            else:
                if len(matching_indexes) > 50_000:
                    stride = math.ceil(len(matching_indexes) / 50_000)
                    matching_indexes = matching_indexes[::stride]
                reference_lab = cv2.cvtColor(
                    reference_image[..., :3],
                    cv2.COLOR_RGB2LAB,
                )
                candidate_lab = cv2.cvtColor(
                    candidate_image[..., :3],
                    cv2.COLOR_RGB2LAB,
                )
                reference_colors = reference_lab[
                    tuple(reference_points[matching_indexes].T)
                ].astype(np.float64)
                candidate_colors = candidate_lab[
                    tuple(candidate_points[nearest_candidate[matching_indexes]].T)
                ].astype(np.float64)
                mean_lab_distance = float(
                    np.linalg.norm(reference_colors - candidate_colors, axis=1).mean()
                    / 255.0
                )
        else:
            color_matched_pixel_ratio = 0.0
            mean_lab_distance = 1.0
    else:
        centroid_distance = 0.0 if not reference_count and not candidate_count else 1.0
        chamfer_score = 1.0 if not reference_count and not candidate_count else 0.0
        color_matched_pixel_ratio = 0.0
        mean_lab_distance = 1.0

    return {
        "referencePixelCount": reference_count,
        "candidatePixelCount": candidate_count,
        "fgIou": raw_iou,
        "fgIouTolerant": tolerant_iou,
        "areaRatio": area_ratio,
        "chamferScore": float(chamfer_score),
        "centroidDistance": centroid_distance,
        "colorMatchedPixelRatio": color_matched_pixel_ratio,
        "meanLabDistance": mean_lab_distance,
    }


def _series_passed(metrics: dict[str, float | int], family: str) -> bool:
    thresholds = SERIES_THRESHOLDS[family]
    return bool(
        metrics["referencePixelCount"] >= MIN_SERIES_PIXEL_COUNT
        and metrics["candidatePixelCount"] >= MIN_SERIES_PIXEL_COUNT
        and metrics["fgIouTolerant"] >= thresholds["fgIouTolerant"]
        and metrics["areaRatio"] >= thresholds["areaRatio"]
        and metrics["chamferScore"] >= thresholds["chamferScore"]
        and metrics["centroidDistance"] <= thresholds["centroidDistance"]
        and metrics["colorMatchedPixelRatio"] >= thresholds["colorMatchedPixelRatio"]
        and metrics["meanLabDistance"] <= thresholds["meanLabDistance"]
    )


def _region_ssim(
    reference: np.ndarray,
    candidate: np.ndarray,
    bounds: tuple[int, int, int, int],
) -> float | None:
    left, top, right, bottom = bounds
    if right - left < 3 or bottom - top < 3:
        return None
    reference_crop = reference[top:bottom, left:right, :3]
    candidate_crop = candidate[top:bottom, left:right, :3]
    window = min(7, reference_crop.shape[0], reference_crop.shape[1])
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return None
    return float(ssim(reference_crop, candidate_crop, channel_axis=2, win_size=window))


def _region_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    plot: dict[str, int],
    legend_position: str,
    orientation: str,
) -> dict[str, float | None]:
    height, width = reference.shape[:2]
    left, top, right, bottom = (
        plot["left"],
        plot["top"],
        plot["right"] + 1,
        plot["bottom"] + 1,
    )
    plot_width = right - left
    plot_height = bottom - top
    value_axis_left = max(0, left - round(plot_width * 0.18))
    category_axis_bottom = min(height, bottom + round(plot_height * 0.14))
    legend_right = min(width, right + round(plot_width * 0.28))
    left_axis_ssim = _region_ssim(
        reference,
        candidate,
        (value_axis_left, top, left, bottom),
    )
    bottom_axis_ssim = _region_ssim(
        reference,
        candidate,
        (left, bottom, right, category_axis_bottom),
    )
    if orientation == "horizontal":
        value_axis_ssim = bottom_axis_ssim
        category_axis_ssim = left_axis_ssim
    else:
        value_axis_ssim = left_axis_ssim
        category_axis_ssim = bottom_axis_ssim
    return {
        "plotSsim": _region_ssim(reference, candidate, (left, top, right, bottom)),
        "valueAxisSsim": value_axis_ssim,
        "categoryAxisSsim": category_axis_ssim,
        "rightLegendSsim": (
            _region_ssim(reference, candidate, (right, top, legend_right, bottom))
            if legend_position in {"r", "right"}
            else None
        ),
    }


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    return value


def _chart_frame_bounds(
    profile: dict[str, Any],
    image_shape: tuple[int, ...],
) -> dict[str, int] | None:
    frame = profile.get("chartFrame")
    if not isinstance(frame, dict):
        return None
    height, width = image_shape[:2]
    try:
        left = round(float(frame["left"]) * width)
        top = round(float(frame["top"]) * height)
        right = round(float(frame["right"]) * width)
        bottom = round(float(frame["bottom"]) * height)
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return {
        "left": max(0, min(width - 1, left)),
        "top": max(0, min(height - 1, top)),
        "right": max(1, min(width, right)),
        "bottom": max(1, min(height, bottom)),
    }


def compute_cartesian_chart_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Compute a local usability signal without altering the full-slide gate."""
    if not profile.get("evaluable"):
        return {
            "evaluable": False,
            "reason": profile.get("reason", "profile-not-evaluable"),
        }
    family = str(profile.get("family", ""))
    orientation = str(profile.get("orientation", ""))
    if family not in SERIES_THRESHOLDS:
        return {"evaluable": False, "reason": "unsupported-chart-family"}
    try:
        series_count = int(profile.get("seriesCount", 0))
    except (TypeError, ValueError):
        series_count = 0
    if series_count < 1 or series_count > MAX_AGGREGATE_SERIES_COUNT:
        return {
            "evaluable": False,
            "reason": "series-count-out-of-scope",
            "seriesCount": series_count,
        }

    normalized_candidate = _normalize_candidate(reference, candidate)
    normalized_reference = reference[..., :3]
    chart_frame = _chart_frame_bounds(profile, normalized_reference.shape)
    if chart_frame is None:
        return {
            "evaluable": False,
            "reason": "chart-frame-unresolved",
        }
    reference_bounds = detect_cartesian_plot_bounds(
        normalized_reference,
        orientation,
        chart_frame,
    )
    candidate_bounds = detect_cartesian_plot_bounds(
        normalized_candidate,
        orientation,
        chart_frame,
    )
    if reference_bounds is None or candidate_bounds is None:
        return {"evaluable": False, "reason": "plot-bounds-not-detectable"}

    minimum_dimension = min(normalized_reference.shape[:2])
    side_errors = {
        side: abs(reference_bounds[side] - candidate_bounds[side])
        for side in ("left", "top", "right", "bottom")
    }
    max_error_ratio = max(side_errors.values()) / max(minimum_dimension, 1)
    plot_bounds_passed = max_error_ratio <= PLOT_BOUND_MAX_ERROR_RATIO

    left, top, right, bottom = (
        reference_bounds["left"],
        reference_bounds["top"],
        reference_bounds["right"] + 1,
        reference_bounds["bottom"] + 1,
    )
    region_mask = np.zeros(normalized_reference.shape[:2], dtype=bool)
    region_mask[top:bottom, left:right] = True
    reference_background_kind, reference_background = _plot_background(
        normalized_reference,
        reference_bounds,
    )
    candidate_background_kind, candidate_background = _plot_background(
        normalized_candidate,
        candidate_bounds,
    )
    if "unsupported-chromatic" in {
        reference_background_kind,
        candidate_background_kind,
    }:
        return {
            "evaluable": False,
            "reason": "chromatic-plot-background-not-supported",
        }
    reference_series = (
        _series_mask(normalized_reference, reference_background) & region_mask
    )
    candidate_series = (
        _series_mask(normalized_candidate, candidate_background) & region_mask
    )
    series_metrics = _mask_metrics(
        reference_series,
        candidate_series,
        normalized_reference,
        normalized_candidate,
    )
    if series_metrics["referencePixelCount"] < MIN_SERIES_PIXEL_COUNT:
        return {
            "evaluable": False,
            "reason": "reference-series-ink-not-detectable",
            "plotBounds": {
                "reference": reference_bounds,
                "candidate": candidate_bounds,
            },
        }
    series_passed = _series_passed(series_metrics, family)
    mutated_candidate = normalized_candidate.copy()
    mutation_fill = (
        candidate_background.astype(np.uint8)
        if candidate_background is not None
        else np.asarray((255, 255, 255), dtype=np.uint8)
    )
    mutated_candidate[candidate_series] = mutation_fill
    mutated_series = (
        _series_mask(mutated_candidate, candidate_background) & region_mask
    )
    mutated_metrics = _mask_metrics(reference_series, mutated_series)
    mutation_passed = _series_passed(mutated_metrics, family)
    sensitivity_detected = series_passed and not mutation_passed

    result = {
        "evaluable": True,
        "family": family,
        "orientation": orientation,
        "passed": bool(plot_bounds_passed and series_passed and sensitivity_detected),
        "plotBounds": {
            "reference": reference_bounds,
            "candidate": candidate_bounds,
            "sideErrorsPx": side_errors,
            "maxErrorRatio": max_error_ratio,
            "threshold": PLOT_BOUND_MAX_ERROR_RATIO,
            "passed": plot_bounds_passed,
        },
        "seriesInk": {
            **series_metrics,
            "thresholds": SERIES_THRESHOLDS[family],
            "passed": series_passed,
        },
        "seriesSensitivity": {
            "mutation": "erase-detected-candidate-series-mask",
            "detected": sensitivity_detected,
            "mutatedPassed": mutation_passed,
        },
        "regions": _region_metrics(
            normalized_reference,
            normalized_candidate,
            reference_bounds,
            str(profile.get("legendPosition", "none")),
            orientation,
        ),
    }
    return _rounded(result)
