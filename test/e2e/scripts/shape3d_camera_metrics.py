#!/usr/bin/env python3
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
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": P_NS, "a": A_NS, "r": R_NS}

CORNER_SCORE_THRESHOLD = 0.98
COLOR_SCORE_THRESHOLD = 0.97
GRADIENT_RANGE_RATIO_THRESHOLD = 0.65
GRADIENT_DIRECTION_THRESHOLD = 0.95
MIN_REFERENCE_GRADIENT_RANGE = 4.0
TEXT_RASTER_TOLERANCE_RATIO = 0.0025
TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD = 0.90
TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD = 0.98
TEXT_INK_COVERAGE_RATIO_THRESHOLD = 0.90
SHADOW_RING_INNER_RATIO = 0.0018
SHADOW_RING_OUTER_RATIO = 0.016
SHADOW_BACKGROUND_LEVEL = 252.0
MIN_REFERENCE_SHADOW_DENSITY = 0.25
SHADOW_ENERGY_RATIO_THRESHOLD = 0.70
SHADOW_DIRECTION_THRESHOLD = 0.95
PICTURE_CORNER_SCORE_THRESHOLD = 0.98
PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD = 0.95
PICTURE_RECTIFIED_EDGE_F1_THRESHOLD = 0.90
PICTURE_RECTIFIED_SIZE = 384
PICTURE_EDGE_TOLERANCE_RATIO = 0.008
PICTURE_CROP_MUTATION_RATIO = 0.12
BOTTOM_FRONT_CORNER_SCORE_THRESHOLD = 0.98
BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD = 1.0
BOTTOM_FRONT_SOURCE_FLAT_FILL = (68, 114, 196)
CUSTOM_RASTER_TOLERANCE_RATIO = 0.0025
CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD = 0.95
CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD = 0.98
CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD = 0.90
CUSTOM_COLOR_SCORE_THRESHOLD = 0.98
CUSTOM_VERTICAL_SQUASH_RATIO = 0.20
CUSTOM_GEOMETRY_EXTENTS = {
    (3840480, 3840480),
    (7315200, 2926080),
    (2926080, 4937760),
}


def _slide_number(path: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", path)
    return int(match.group(1)) if match else 0


def _zero_or_absent(value: str | None) -> bool:
    if value is None:
        return True
    try:
        numeric = float(value)
    except ValueError:
        return False
    return math.isfinite(numeric) and numeric == 0


def _rotation_matches(node, expected: tuple[float, float, float]) -> bool:
    if node is None:
        return False
    try:
        values = tuple(float(node.get(name, "0")) for name in ("lat", "lon", "rev"))
    except ValueError:
        return False
    return all(math.isfinite(value) and value == target for value, target in zip(values, expected))


def _supported_camera(camera) -> str | None:
    if camera.get("zoom") is not None:
        return None
    preset = camera.get("prst")
    rotation = camera.find("a:rot", NS)
    if preset == "orthographicFront" and camera.get("fov") is None:
        if rotation is None or _rotation_matches(rotation, (1200000, 1800000, 0)):
            return "orthographic"
        return None
    if (
        preset == "perspectiveRelaxedModerately"
        and camera.get("fov") == "7200000"
        and _rotation_matches(rotation, (18590633, 0, 0))
    ):
        return "perspective"
    return None


def _supported_camera_fill(
    shape,
    shape_properties,
    camera_kind: str,
    verified_theme_accent1: bool,
) -> bool:
    solid = shape_properties.find("a:solidFill", NS)
    if solid is not None:
        color = solid.find("a:srgbClr", NS)
        return (
            color is not None
            and color.get("val", "").upper() == "2F75B5"
            and len(color) == 0
        )
    if camera_kind != "perspective" or not verified_theme_accent1:
        return False
    return bool(
        shape.xpath(
            "boolean(p:style/a:fillRef[@idx='1']/a:schemeClr[@val='accent1'])",
            namespaces=NS,
        )
    )


def _is_supported_camera_shape(
    shape,
    verified_theme_accent1: bool,
    verified_theme_outer_shadow: bool,
) -> bool:
    shape_properties = shape.find("p:spPr", NS)
    if shape_properties is None:
        return False
    geometry = shape_properties.find("a:prstGeom", NS)
    scene = shape_properties.find("a:scene3d", NS)
    shape3d = shape_properties.find("a:sp3d", NS)
    if (
        geometry is None
        or geometry.get("prst") != "rect"
        or scene is None
        or scene.find("a:backdrop", NS) is not None
    ):
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    if camera is None or light is None:
        return False
    camera_kind = _supported_camera(camera)
    if (
        camera_kind is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
        or light.find("a:rot", NS) is not None
    ):
        return False
    transform = shape_properties.find("a:xfrm", NS)
    if transform is not None and any(
        not _zero_or_absent(transform.get(name)) for name in ("rot", "flipH", "flipV")
    ):
        return False
    if shape3d is None:
        # Native case 0014 proves absence is implicit zero depth only for the exact relaxed
        # perspective tuple. Orthographic absence remains outside this metric's contract.
        if camera_kind != "perspective":
            return False
    elif any(
        not _zero_or_absent(shape3d.get(name)) for name in ("z", "extrusionH", "contourW")
    ) or (
        shape3d.get("prstMaterial") is not None
        or shape3d.find("a:bevelT", NS) is not None
        or shape3d.find("a:bevelB", NS) is not None
        or shape3d.find("a:extrusionClr", NS) is not None
        or shape3d.find("a:contourClr", NS) is not None
    ):
        return False
    if (
        shape_properties.find("a:effectLst", NS) is not None
        or shape_properties.find("a:effectDag", NS) is not None
        or shape_properties.find("a:ln/a:noFill", NS) is None
    ):
        return False
    if shape.xpath("boolean(p:txBody//a:t[normalize-space(.) != ''])", namespaces=NS):
        return False
    effect_refs = shape.xpath("p:style/a:effectRef", namespaces=NS)
    if len(effect_refs) > 1:
        return False
    if effect_refs:
        effect_index = effect_refs[0].get("idx")
        if effect_index not in {"0", "2"}:
            return False
        if effect_index == "2" and not verified_theme_outer_shadow:
            return False
    return _supported_camera_fill(
        shape,
        shape_properties,
        camera_kind,
        verified_theme_accent1,
    )


def _is_bounded_multi_contour_cubic_geometry(custom_geometry) -> bool:
    for list_name in ("avLst", "gdLst", "ahLst", "cxnLst"):
        values = custom_geometry.find(f"a:{list_name}", NS)
        if values is None or len(values) != 0:
            return False
    text_rect = custom_geometry.find("a:rect", NS)
    if text_rect is None or dict(text_rect.attrib) != {"l": "l", "t": "t", "r": "r", "b": "b"}:
        return False
    paths = custom_geometry.findall("a:pathLst/a:path", NS)
    if len(paths) != 1 or dict(paths[0].attrib) != {"w": "1000", "h": "1000"}:
        return False

    contour_open = False
    move_count = 0
    close_count = 0
    cubic_count = 0
    for command in paths[0]:
        local_name = etree.QName(command).localname
        if local_name == "close":
            if not contour_open or len(command) != 0:
                return False
            contour_open = False
            close_count += 1
            continue
        if local_name not in {"moveTo", "lnTo", "cubicBezTo"}:
            return False
        if local_name == "moveTo":
            if contour_open:
                return False
            contour_open = True
            move_count += 1
        elif not contour_open:
            return False
        points = command.findall("a:pt", NS)
        expected_count = 3 if local_name == "cubicBezTo" else 1
        if len(points) != expected_count:
            return False
        for point in points:
            try:
                x = float(point.get("x", "nan"))
                y = float(point.get("y", "nan"))
            except ValueError:
                return False
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1000 and 0 <= y <= 1000):
                return False
        if local_name == "cubicBezTo":
            cubic_count += 1
    return (
        not contour_open
        and move_count >= 2
        and close_count == move_count
        and cubic_count >= 1
    )


def _is_supported_custom_geometry_camera_shape(shape) -> bool:
    if shape.xpath("boolean(ancestor::p:grpSp)", namespaces=NS):
        return False
    if shape.find("p:style", NS) is not None:
        return False
    shape_properties = shape.find("p:spPr", NS)
    if shape_properties is None:
        return False
    transform = shape_properties.find("a:xfrm", NS)
    extent = transform.find("a:ext", NS) if transform is not None else None
    if transform is None or extent is None:
        return False
    if any(not _zero_or_absent(transform.get(name)) for name in ("rot", "flipH", "flipV")):
        return False
    try:
        dimensions = (int(extent.get("cx", "")), int(extent.get("cy", "")))
    except ValueError:
        return False
    if dimensions not in CUSTOM_GEOMETRY_EXTENTS:
        return False

    custom_geometry = shape_properties.find("a:custGeom", NS)
    if custom_geometry is None or not _is_bounded_multi_contour_cubic_geometry(custom_geometry):
        return False
    scene = shape_properties.find("a:scene3d", NS)
    if scene is None or scene.find("a:backdrop", NS) is not None:
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    if (
        camera is None
        or camera.get("prst") != "perspectiveRelaxedModerately"
        or camera.get("fov") != "7200000"
        or camera.get("zoom") is not None
        or not _rotation_matches(camera.find("a:rot", NS), (18590633, 0, 0))
        or light is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
        or light.find("a:rot", NS) is not None
    ):
        return False
    if (
        shape_properties.find("a:sp3d", NS) is not None
        or shape_properties.find("a:effectLst", NS) is not None
        or shape_properties.find("a:effectDag", NS) is not None
        or shape_properties.find("a:ln/a:noFill", NS) is None
        or shape.xpath("boolean(p:txBody//a:t[normalize-space(.) != ''])", namespaces=NS)
    ):
        return False
    solid = shape_properties.find("a:solidFill", NS)
    color = solid.find("a:srgbClr", NS) if solid is not None else None
    return bool(
        color is not None
        and color.get("val", "").upper() in {"2F75B5", "FFFFFF"}
        and len(color) == 0
    )


def _has_verified_theme_accent1(archive: ZipFile) -> bool:
    values: set[str] = set()
    for name in archive.namelist():
        if not re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
            continue
        root = etree.fromstring(archive.read(name))
        values.update(
            value.upper()
            for value in root.xpath(".//a:clrScheme/a:accent1/a:srgbClr/@val", namespaces=NS)
        )
    return values == {"4F81BD"}


def _has_verified_theme_outer_shadow(archive: ZipFile) -> bool:
    themes = 0
    matches = 0
    for name in archive.namelist():
        if not re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
            continue
        themes += 1
        root = etree.fromstring(archive.read(name))
        styles = root.xpath(".//a:effectStyleLst/a:effectStyle", namespaces=NS)
        if len(styles) < 2:
            continue
        effect_list = styles[1].find("a:effectLst", NS)
        if effect_list is None or len(effect_list) != 1:
            continue
        shadow = effect_list.find("a:outerShdw", NS)
        if shadow is None or dict(shadow.attrib) != {
            "blurRad": "40000",
            "dist": "23000",
            "dir": "5400000",
            "rotWithShape": "0",
        }:
            continue
        color = shadow.find("a:srgbClr", NS)
        alpha = color.find("a:alpha", NS) if color is not None else None
        if (
            color is not None
            and color.get("val", "").upper() == "000000"
            and len(color) == 1
            and alpha is not None
            and alpha.get("val") == "35000"
            and len(alpha) == 0
        ):
            matches += 1
    return themes > 0 and matches == themes


def extract_camera_slide_indices(source_pptx: Path) -> set[int]:
    """Return slides containing the bounded zero-depth rectangular camera-plane tuple."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        verified_theme_accent1 = _has_verified_theme_accent1(archive)
        verified_theme_outer_shadow = _has_verified_theme_outer_shadow(archive)
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            for shape in slide.xpath(".//p:sp", namespaces=NS):
                if _is_supported_camera_shape(
                    shape,
                    verified_theme_accent1,
                    verified_theme_outer_shadow,
                ):
                    indices.add(slide_index)
                    break
    return indices


def extract_custom_geometry_camera_slide_indices(source_pptx: Path) -> set[int]:
    """Return slides containing the native-backed multi-contour cubic camera tuple."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            if any(
                _is_supported_custom_geometry_camera_shape(shape)
                for shape in slide.xpath(".//p:sp", namespaces=NS)
            ):
                indices.add(slide_index)
    return indices


def extract_camera_shadow_slide_indices(source_pptx: Path) -> set[int]:
    """Return supported solid camera slides whose exact theme effect requires an outer shadow."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        verified_theme_accent1 = _has_verified_theme_accent1(archive)
        verified_theme_outer_shadow = _has_verified_theme_outer_shadow(archive)
        if not verified_theme_outer_shadow:
            return indices
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            for shape in slide.xpath(".//p:sp", namespaces=NS):
                if not _is_supported_camera_shape(
                    shape,
                    verified_theme_accent1,
                    verified_theme_outer_shadow,
                ):
                    continue
                if shape.xpath("boolean(p:style/a:effectRef[@idx='2'])", namespaces=NS):
                    indices.add(slide_index)
                    break
    return indices


def _native_backed_bottom_aspect(shape_properties) -> bool:
    extent = shape_properties.find("a:xfrm/a:ext", NS)
    if extent is None:
        return False
    try:
        width = float(extent.get("cx", "nan"))
        height = float(extent.get("cy", "nan"))
    except ValueError:
        return False
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        return False
    aspect = width / height
    return any(
        abs(math.log(aspect / expected)) <= 1e-4
        for expected in (3.2 / 5.2, 1.0, 2.0)
    )


def _is_supported_bottom_bevel_front_shape(shape) -> bool:
    parent = shape.getparent()
    grandparent = parent.getparent() if parent is not None else None
    if (
        parent is None
        or grandparent is None
        or etree.QName(parent).localname != "spTree"
        or etree.QName(grandparent).localname != "cSld"
        or shape.find("p:nvSpPr/p:nvPr/p:ph", NS) is not None
    ):
        return False
    shape_properties = shape.find("p:spPr", NS)
    if shape_properties is None or not _native_backed_bottom_aspect(shape_properties):
        return False
    transform = shape_properties.find("a:xfrm", NS)
    if transform is not None and any(
        not _zero_or_absent(transform.get(name)) for name in ("rot", "flipH", "flipV")
    ):
        return False
    geometry = shape_properties.find("a:prstGeom", NS)
    color = shape_properties.find("a:solidFill/a:srgbClr", NS)
    if (
        geometry is None
        or geometry.get("prst") != "rect"
        or color is None
        or color.get("val", "").upper() != "4472C4"
        or len(color) != 0
        or shape_properties.find("a:ln/a:noFill", NS) is None
        or shape_properties.find("a:effectLst", NS) is not None
        or shape_properties.find("a:effectDag", NS) is not None
    ):
        return False
    scene = shape_properties.find("a:scene3d", NS)
    shape3d = shape_properties.find("a:sp3d", NS)
    if scene is None or shape3d is None or scene.find("a:backdrop", NS) is not None:
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    if (
        camera is None
        or camera.get("prst") != "orthographicFront"
        or camera.get("fov") is not None
        or camera.get("zoom") is not None
        or camera.find("a:rot", NS) is not None
        or light is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
    ):
        return False
    light_rotation = light.find("a:rot", NS)
    if light_rotation is not None and not _rotation_matches(
        light_rotation, (0, 0, 3000000)
    ):
        return False
    if any(
        not _zero_or_absent(shape3d.get(name)) for name in ("z", "extrusionH", "contourW")
    ) or (
        shape3d.get("prstMaterial") not in {None, "dkEdge"}
        or shape3d.find("a:bevelT", NS) is not None
        or shape3d.find("a:extrusionClr", NS) is not None
        or shape3d.find("a:contourClr", NS) is not None
    ):
        return False
    bevel = shape3d.find("a:bevelB", NS)
    if (
        bevel is None
        or bevel.get("prst", "circle") not in {"relaxedInset", "circle"}
        or bevel.get("w", "76200") != "76200"
        or bevel.get("h", "76200") != "76200"
        or len(bevel) != 0
    ):
        return False
    visible_text = shape.xpath("p:txBody//a:t[normalize-space(.) != '']", namespaces=NS)
    if visible_text:
        body = shape.find("p:txBody/a:bodyPr", NS)
        paragraphs = shape.xpath("p:txBody/a:p[a:t or a:r/a:t]", namespaces=NS)
        runs = shape.xpath("p:txBody/a:p/a:r[a:t]", namespaces=NS)
        if (
            body is None
            or body.get("anchor") != "ctr"
            or body.get("wrap") is not None
            or body.get("vert") is not None
            or len(body) != 0
            or len(paragraphs) != 1
            or paragraphs[0].find("a:pPr", NS) is None
            or paragraphs[0].find("a:pPr", NS).get("algn") != "ctr"
            or len(runs) != 1
        ):
            return False
        run_properties = runs[0].find("a:rPr", NS)
        run_color = runs[0].find("a:rPr/a:solidFill/a:srgbClr", NS)
        if (
            run_properties is None
            or run_properties.get("sz") != "2000"
            or run_properties.get("b") != "1"
            or run_color is None
            or run_color.get("val", "").upper() != "FFFFFF"
        ):
            return False
    return True


def extract_bottom_bevel_front_slide_indices(source_pptx: Path) -> set[int]:
    """Return standalone slides in the bounded edge-on bottom-bevel material matrix."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            if any(
                _is_supported_bottom_bevel_front_shape(shape)
                for shape in slide.xpath(".//p:sp", namespaces=NS)
            ):
                indices.add(slide_index)
    return indices


def _is_supported_text_camera_shape(shape) -> bool:
    shape_properties = shape.find("p:spPr", NS)
    if shape_properties is None:
        return False
    geometry = shape_properties.find("a:prstGeom", NS)
    scene = shape_properties.find("a:scene3d", NS)
    if (
        geometry is None
        or geometry.get("prst") != "rect"
        or scene is None
        or shape_properties.find("a:sp3d", NS) is not None
        or scene.find("a:backdrop", NS) is not None
    ):
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    camera_supported = False
    expected_anchor: str | None = None
    if camera is not None:
        if (
            camera.get("prst") == "perspectiveContrastingRightFacing"
            and camera.get("fov") == "5100000"
            and _rotation_matches(camera.find("a:rot", NS), (0, 19532225, 0))
        ):
            camera_supported = True
            expected_anchor = "ctr"
        elif (
            camera.get("prst") == "perspectiveLeft"
            and camera.get("fov") == "7200000"
            and camera.find("a:rot", NS) is None
        ):
            camera_supported = True
    if (
        camera is None
        or not camera_supported
        or camera.get("zoom") is not None
        or light is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
        or light.find("a:rot", NS) is not None
    ):
        return False
    transform = shape_properties.find("a:xfrm", NS)
    if transform is not None and any(
        not _zero_or_absent(transform.get(name)) for name in ("rot", "flipH", "flipV")
    ):
        return False
    body = shape.find("p:txBody/a:bodyPr", NS)
    if (
        shape_properties.find("a:noFill", NS) is None
        or shape_properties.find("a:ln", NS) is not None
        or shape_properties.find("a:effectLst", NS) is not None
        or shape_properties.find("a:effectDag", NS) is not None
        or shape.find("p:style", NS) is not None
        or body is None
        or body.get("wrap") != "none"
        or body.get("anchor") != expected_anchor
        or body.get("vert") is not None
        or body.find("a:spAutoFit", NS) is None
        or body.find("a:normAutofit", NS) is not None
        or body.find("a:noAutofit", NS) is not None
        or not shape.xpath("boolean(p:txBody//a:t[normalize-space(.) != ''])", namespaces=NS)
    ):
        return False
    return True


def extract_text_camera_slide_indices(source_pptx: Path) -> set[int]:
    """Return slides containing the exact scene-only editable-text camera tuple."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            if any(
                _is_supported_text_camera_shape(shape)
                for shape in slide.xpath(".//p:sp", namespaces=NS)
            ):
                indices.add(slide_index)
    return indices


def _supported_source_crop(source_crop) -> bool:
    if source_crop is None:
        return True
    values: dict[str, float] = {}
    for name in ("l", "t", "r", "b"):
        raw = source_crop.get(name, "0")
        try:
            value = float(raw)
        except ValueError:
            return False
        if not math.isfinite(value) or value < 0 or value > 100000:
            return False
        values[name] = value
    return values["l"] + values["r"] < 99900 and values["t"] + values["b"] < 99900


def _is_supported_picture_camera_shape(picture) -> bool:
    shape_properties = picture.find("p:spPr", NS)
    blip_fill = picture.find("p:blipFill", NS)
    if shape_properties is None or blip_fill is None or picture.find("p:style", NS) is not None:
        return False
    geometry = shape_properties.find("a:prstGeom", NS)
    scene = shape_properties.find("a:scene3d", NS)
    if (
        geometry is None
        or geometry.get("prst") != "rect"
        or scene is None
        or scene.find("a:backdrop", NS) is not None
        or shape_properties.find("a:sp3d", NS) is not None
        or shape_properties.find("a:effectLst", NS) is not None
        or shape_properties.find("a:effectDag", NS) is not None
        or shape_properties.find("a:ln", NS) is not None
        or any(
            shape_properties.find(f"a:{fill}", NS) is not None
            for fill in ("solidFill", "gradFill", "pattFill", "blipFill", "grpFill")
        )
    ):
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    if (
        camera is None
        or camera.get("prst") != "perspectiveRight"
        or camera.get("fov") != "5700000"
        or camera.get("zoom") is not None
        or camera.find("a:rot", NS) is not None
        or light is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
        or light.find("a:rot", NS) is not None
    ):
        return False
    transform = shape_properties.find("a:xfrm", NS)
    if transform is not None and any(
        not _zero_or_absent(transform.get(name)) for name in ("rot", "flipH", "flipV")
    ):
        return False
    blip = blip_fill.find("a:blip", NS)
    stretch = blip_fill.find("a:stretch", NS)
    if (
        blip is None
        or not blip.get(f"{{{R_NS}}}embed")
        or len(blip) != 0
        or stretch is None
        or len(stretch) != 0
        or blip_fill.find("a:tile", NS) is not None
        or not _supported_source_crop(blip_fill.find("a:srcRect", NS))
    ):
        return False
    return True


def extract_picture_camera_slide_indices(source_pptx: Path) -> set[int]:
    """Return slides containing the exact perspective-right live-picture plane tuple."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            if any(
                _is_supported_picture_camera_shape(picture)
                for picture in slide.xpath(".//p:pic", namespaces=NS)
            ):
                indices.add(slide_index)
    return indices


def _is_supported_group_picture_child(picture) -> bool:
    if picture.find("p:style", NS) is not None:
        return False
    if picture.xpath("boolean(p:nvPicPr/p:nvPr/p:videoFile | p:nvPicPr/p:nvPr/p:audioFile)", namespaces=NS):
        return False
    blip_fill = picture.find("p:blipFill", NS)
    shape_properties = picture.find("p:spPr", NS)
    if blip_fill is None or shape_properties is None:
        return False
    blip = blip_fill.find("a:blip", NS)
    stretch = blip_fill.find("a:stretch", NS)
    if (
        blip is None
        or not blip.get(f"{{{R_NS}}}embed")
        or len(blip) != 0
        or stretch is None
        or blip_fill.find("a:tile", NS) is not None
        or not _supported_source_crop(blip_fill.find("a:srcRect", NS))
    ):
        return False
    if len(stretch) > 1:
        return False
    if len(stretch) == 1 and (
        etree.QName(stretch[0]).localname != "fillRect"
        or len(stretch[0]) != 0
        or len(stretch[0].attrib) != 0
    ):
        return False

    transform = shape_properties.find("a:xfrm", NS)
    extent = transform.find("a:ext", NS) if transform is not None else None
    if transform is None or extent is None:
        return False
    try:
        width = float(extent.get("cx", "nan"))
        height = float(extent.get("cy", "nan"))
    except ValueError:
        return False
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
        or any(
            not _zero_or_absent(transform.get(name))
            for name in ("rot", "flipH", "flipV")
        )
    ):
        return False
    geometry = shape_properties.find("a:prstGeom", NS)
    if geometry is not None and (
        geometry.get("prst") != "rect"
        or len(geometry.findall("a:avLst/*", NS)) != 0
    ):
        return False
    if shape_properties.xpath(
        "boolean(a:custGeom | a:scene3d | a:sp3d | a:effectLst | a:effectDag | a:ln | "
        "a:solidFill | a:gradFill | a:pattFill | a:blipFill | a:grpFill)",
        namespaces=NS,
    ):
        return False
    return True


def _group_has_transform(group) -> bool:
    transform = group.find("p:grpSpPr/a:xfrm", NS)
    return bool(
        transform is not None
        and any(
            not _zero_or_absent(transform.get(name))
            for name in ("rot", "flipH", "flipV")
        )
    )


def _is_supported_group_picture_camera(
    group,
    *,
    ancestor_transformed: bool,
    ancestor_has_scene: bool,
    ancestor_has_effect: bool,
) -> bool:
    group_properties = group.find("p:grpSpPr", NS)
    transform = group_properties.find("a:xfrm", NS) if group_properties is not None else None
    if group_properties is None or transform is None:
        return False
    extent = transform.find("a:ext", NS)
    child_extent = transform.find("a:chExt", NS)
    if extent is None or child_extent is None:
        return False
    try:
        dimensions = tuple(
            float(value)
            for value in (
                extent.get("cx", "nan"),
                extent.get("cy", "nan"),
                child_extent.get("cx", "nan"),
                child_extent.get("cy", "nan"),
            )
        )
    except ValueError:
        return False
    if (
        ancestor_transformed
        or ancestor_has_scene
        or ancestor_has_effect
        or any(not math.isfinite(value) or value <= 0 for value in dimensions)
        or _group_has_transform(group)
        or group_properties.find("a:sp3d", NS) is not None
        or group_properties.find("a:effectLst", NS) is not None
        or group_properties.find("a:effectDag", NS) is not None
    ):
        return False
    scene = group_properties.find("a:scene3d", NS)
    if scene is None or scene.find("a:backdrop", NS) is not None:
        return False
    camera = scene.find("a:camera", NS)
    light = scene.find("a:lightRig", NS)
    if (
        camera is None
        or camera.get("prst") != "perspectiveLeft"
        or camera.get("fov") != "5700000"
        or camera.get("zoom") is not None
        or not _rotation_matches(camera.find("a:rot", NS), (0, 1500000, 0))
        or light is None
        or light.get("rig") != "threePt"
        or light.get("dir") != "t"
        or light.find("a:rot", NS) is not None
    ):
        return False
    children = [
        child
        for child in group
        if etree.QName(child).localname in {"sp", "pic", "grpSp", "graphicFrame", "cxnSp"}
    ]
    return len(children) == 2 and all(
        etree.QName(child).localname == "pic" and _is_supported_group_picture_child(child)
        for child in children
    )


def _find_group_picture_camera_slides(
    container,
    *,
    ancestor_transformed: bool,
    ancestor_has_scene: bool,
    ancestor_has_effect: bool,
) -> bool:
    for group in container.findall("p:grpSp", NS):
        if _is_supported_group_picture_camera(
            group,
            ancestor_transformed=ancestor_transformed,
            ancestor_has_scene=ancestor_has_scene,
            ancestor_has_effect=ancestor_has_effect,
        ):
            return True
        group_properties = group.find("p:grpSpPr", NS)
        if _find_group_picture_camera_slides(
            group,
            ancestor_transformed=ancestor_transformed or _group_has_transform(group),
            ancestor_has_scene=ancestor_has_scene
            or bool(group_properties is not None and group_properties.find("a:scene3d", NS) is not None),
            ancestor_has_effect=ancestor_has_effect
            or bool(
                group_properties is not None
                and group_properties.xpath("boolean(a:effectLst | a:effectDag)", namespaces=NS)
            ),
        ):
            return True
    return False


def extract_group_picture_camera_slide_indices(source_pptx: Path) -> set[int]:
    """Return slides containing the bounded perspective-left two-picture group tuple."""
    indices: set[int] = set()
    with ZipFile(source_pptx) as archive:
        slide_paths = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=_slide_number,
        )
        for slide_index, slide_path in enumerate(slide_paths):
            slide = etree.fromstring(archive.read(slide_path))
            shape_tree = slide.find("p:cSld/p:spTree", NS)
            if shape_tree is not None and _find_group_picture_camera_slides(
                shape_tree,
                ancestor_transformed=False,
                ancestor_has_scene=False,
                ancestor_has_effect=False,
            ):
                indices.add(slide_index)
    return indices


def _foreground_mask(image: np.ndarray) -> np.ndarray:
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("camera metric requires an RGB image")
    rgb = image[..., :3].astype(np.int16, copy=False)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    # The native camera matrix uses saturated blue probes on a white slide. Requiring chroma keeps
    # PDF antialiasing while excluding the white page and makes the local gate independent of the
    # renderer's own source bounds.
    return (spread > 18) & (rgb.min(axis=2) < 245)


def _ordered_normalized_corners(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = _foreground_mask(image)
    contours, _ = cv2.findContours(
        mask.astype(np.uint8) * 255,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        raise ValueError("camera plane foreground is empty")
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 64:
        raise ValueError("camera plane foreground is below the resolution floor")
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    polygon = None
    for epsilon in (0.003, 0.005, 0.008, 0.012, 0.02):
        candidate = cv2.approxPolyDP(hull, epsilon * perimeter, True).reshape(-1, 2)
        if len(candidate) == 4:
            polygon = candidate.astype(np.float64)
            break
    if polygon is None:
        raise ValueError("camera plane foreground is not a four-corner convex polygon")
    polygon[:, 0] /= image.shape[1]
    polygon[:, 1] /= image.shape[0]
    sums = polygon.sum(axis=1)
    differences = polygon[:, 0] - polygon[:, 1]
    ordered = np.asarray(
        [
            polygon[np.argmin(sums)],
            polygon[np.argmax(differences)],
            polygon[np.argmax(sums)],
            polygon[np.argmin(differences)],
        ]
    )
    if len({tuple(point) for point in ordered}) != 4:
        raise ValueError("camera plane corners are ambiguous")
    return ordered, mask


def _ordered_group_surface_corners(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Recover a group plane from all substantial picture fragments, not one contour.

    A legal source crop can enlarge a white feature until it separates the two colored halves of
    the target surface. Selecting only the largest contour then validates half a group. The group
    modality instead joins every substantial foreground contour before building the outer hull.
    """
    mask = _foreground_mask(image)
    contours, _ = cv2.findContours(
        mask.astype(np.uint8) * 255,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    substantial = [contour for contour in contours if cv2.contourArea(contour) >= 64]
    if not substantial:
        raise ValueError("group camera plane foreground is below the resolution floor")
    hull = cv2.convexHull(np.vstack(substantial))
    perimeter = cv2.arcLength(hull, True)
    polygon = None
    for epsilon in (0.003, 0.005, 0.008, 0.012, 0.02):
        candidate = cv2.approxPolyDP(hull, epsilon * perimeter, True).reshape(-1, 2)
        if len(candidate) == 4:
            polygon = candidate.astype(np.float64)
            break
    if polygon is None:
        raise ValueError("group camera plane foreground is not a four-corner convex polygon")
    polygon[:, 0] /= image.shape[1]
    polygon[:, 1] /= image.shape[0]
    sums = polygon.sum(axis=1)
    differences = polygon[:, 0] - polygon[:, 1]
    ordered = np.asarray(
        [
            polygon[np.argmin(sums)],
            polygon[np.argmax(differences)],
            polygon[np.argmax(sums)],
            polygon[np.argmin(differences)],
        ]
    )
    if len({tuple(point) for point in ordered}) != 4:
        raise ValueError("group camera plane corners are ambiguous")
    return ordered, mask


def _material_bands(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    foreground_rows = np.flatnonzero(mask.any(axis=1))
    if foreground_rows.size == 0:
        raise ValueError("camera plane foreground is empty")
    top = int(foreground_rows[0])
    bottom = int(foreground_rows[-1])
    height = max(bottom - top, 1)
    yy = np.arange(image.shape[0])[:, None]
    bands: list[np.ndarray] = []
    for fraction in (0.1, 0.5, 0.9):
        center = top + fraction * height
        radius = max(2.0, height * 0.015)
        selection = mask & (np.abs(yy - center) <= radius)
        values = image[..., :3][selection]
        if values.shape[0] < 16:
            raise ValueError("camera material band contains too few pixels")
        bands.append(np.median(values, axis=0))
    return np.asarray(bands, dtype=np.float64)


def _exterior_shadow_field(image: np.ndarray) -> tuple[float, np.ndarray, int, int, int]:
    mask = _foreground_mask(image)
    if int(mask.sum()) < 64:
        raise ValueError("camera plane foreground is below the resolution floor")
    raster_scale = max(image.shape[:2])
    inner_radius = max(1, round(raster_scale * SHADOW_RING_INNER_RATIO))
    outer_radius = max(inner_radius + 1, round(raster_scale * SHADOW_RING_OUTER_RATIO))
    inner_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (inner_radius * 2 + 1, inner_radius * 2 + 1),
    )
    outer_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (outer_radius * 2 + 1, outer_radius * 2 + 1),
    )
    inner = cv2.dilate(mask.astype(np.uint8), inner_kernel) > 0
    outer = cv2.dilate(mask.astype(np.uint8), outer_kernel) > 0
    ring = outer & ~inner
    if int(ring.sum()) < 64:
        raise ValueError("camera shadow ring is below the resolution floor")
    grayscale = image[..., :3].astype(np.float64).mean(axis=2)
    darkness = np.maximum(0.0, SHADOW_BACKGROUND_LEVEL - grayscale) * ring
    density = float(darkness[ring].mean())
    total = float(darkness.sum())
    mask_y, mask_x = np.nonzero(mask)
    center = np.asarray(
        [mask_x.mean() / image.shape[1], mask_y.mean() / image.shape[0]],
        dtype=np.float64,
    )
    if total <= 1e-9:
        direction = np.zeros(2, dtype=np.float64)
    else:
        yy, xx = np.indices(mask.shape)
        direction = np.asarray(
            [
                float((darkness * xx).sum() / total) / image.shape[1],
                float((darkness * yy).sum() / total) / image.shape[0],
            ],
            dtype=np.float64,
        ) - center
    return density, direction, int(ring.sum()), inner_radius, outer_radius


def _camera_shadow_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    required: bool,
) -> dict[str, Any]:
    if candidate.shape[:2] != reference.shape[:2]:
        candidate = cv2.resize(
            candidate,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    reference_density, reference_direction, reference_pixels, inner_px, outer_px = (
        _exterior_shadow_field(reference)
    )
    candidate_density, candidate_direction, candidate_pixels, _, _ = _exterior_shadow_field(
        candidate
    )
    maximum_density = max(reference_density, candidate_density)
    energy_ratio = (
        min(reference_density, candidate_density) / maximum_density
        if maximum_density > 1e-9
        else 1.0
    )
    direction_denominator = float(
        np.linalg.norm(reference_direction) * np.linalg.norm(candidate_direction)
    )
    if direction_denominator > 1e-12:
        direction_cosine = float(
            np.dot(reference_direction, candidate_direction) / direction_denominator
        )
    elif (
        np.linalg.norm(reference_direction) <= 1e-12
        and np.linalg.norm(candidate_direction) <= 1e-12
    ):
        direction_cosine = 1.0
    else:
        direction_cosine = 0.0
    measurable = required and reference_density >= MIN_REFERENCE_SHADOW_DENSITY
    passed = not measurable or (
        energy_ratio >= SHADOW_ENERGY_RATIO_THRESHOLD
        and direction_cosine >= SHADOW_DIRECTION_THRESHOLD
    )
    return {
        "shadowRequired": required,
        "shadowMeasurable": measurable,
        "referenceShadowDensity": reference_density,
        "candidateShadowDensity": candidate_density,
        "shadowEnergyRatio": float(energy_ratio),
        "shadowDirectionCosine": direction_cosine,
        "referenceShadowRingPixels": reference_pixels,
        "candidateShadowRingPixels": candidate_pixels,
        "shadowRingInnerPx": inner_px,
        "shadowRingOuterPx": outer_px,
        "shadowPassed": passed,
    }


def _erase_exterior_shadow(image: np.ndarray) -> np.ndarray:
    mask = _foreground_mask(image)
    radius = max(2, round(max(image.shape[:2]) * SHADOW_RING_OUTER_RATIO))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (radius * 2 + 1, radius * 2 + 1),
    )
    exterior = (cv2.dilate(mask.astype(np.uint8), kernel) > 0) & ~mask
    mutated = image.copy()
    mutated[exterior] = 255
    return mutated


def _shadow_sensitivity(
    reference: np.ndarray,
    candidate: np.ndarray,
    shadow: Mapping[str, Any],
) -> dict[str, Any]:
    if not shadow["shadowMeasurable"]:
        return {
            "mutation": "erase-exterior-shadow",
            "applicable": False,
            "detected": None,
        }
    normalized_candidate = candidate
    if candidate.shape[:2] != reference.shape[:2]:
        normalized_candidate = cv2.resize(
            candidate,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    mutated = _camera_shadow_metrics(
        reference,
        _erase_exterior_shadow(normalized_candidate),
        required=True,
    )
    detected = not mutated["shadowPassed"]
    return {
        "mutation": "erase-exterior-shadow",
        "applicable": True,
        "mutatedCandidateShadowDensity": mutated["candidateShadowDensity"],
        "mutatedShadowEnergyRatio": mutated["shadowEnergyRatio"],
        "mutatedShadowDirectionCosine": mutated["shadowDirectionCosine"],
        "mutatedShadowPassed": mutated["shadowPassed"],
        "detected": detected,
    }


def compute_camera_plane_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    corner_score_threshold: float = CORNER_SCORE_THRESHOLD,
    color_score_threshold: float = COLOR_SCORE_THRESHOLD,
    gradient_range_ratio_threshold: float = GRADIENT_RANGE_RATIO_THRESHOLD,
    gradient_direction_threshold: float = GRADIENT_DIRECTION_THRESHOLD,
    shadow_required: bool = False,
) -> dict[str, Any]:
    reference_corners, reference_mask = _ordered_normalized_corners(reference)
    candidate_corners, candidate_mask = _ordered_normalized_corners(candidate)
    left, top = reference_corners.min(axis=0)
    right, bottom = reference_corners.max(axis=0)
    diagonal = math.hypot(right - left, bottom - top)
    if diagonal <= 0:
        raise ValueError("camera plane reference bounds are degenerate")
    mean_corner_error = float(
        np.linalg.norm(reference_corners - candidate_corners, axis=1).mean() / diagonal
    )
    corner_score = max(0.0, 1.0 - mean_corner_error)

    reference_bands = _material_bands(reference, reference_mask)
    candidate_bands = _material_bands(candidate, candidate_mask)
    color_score = max(0.0, 1.0 - float(np.abs(reference_bands - candidate_bands).mean()) / 255)
    reference_vector = reference_bands[0] - reference_bands[-1]
    candidate_vector = candidate_bands[0] - candidate_bands[-1]
    reference_range = float(np.linalg.norm(reference_vector))
    candidate_range = float(np.linalg.norm(candidate_vector))
    gradient_required = reference_range >= MIN_REFERENCE_GRADIENT_RANGE
    if max(reference_range, candidate_range) > 0:
        gradient_range_ratio = min(reference_range, candidate_range) / max(
            reference_range, candidate_range
        )
    else:
        gradient_range_ratio = 1.0
    if reference_range > 1 and candidate_range > 1:
        gradient_direction = float(
            np.dot(reference_vector, candidate_vector) / (reference_range * candidate_range)
        )
    elif reference_range <= 1 and candidate_range <= 1:
        gradient_direction = 1.0
    else:
        gradient_direction = 0.0
    shadow = _camera_shadow_metrics(reference, candidate, required=shadow_required)
    shadow_sensitivity = _shadow_sensitivity(reference, candidate, shadow)
    passed = (
        corner_score >= corner_score_threshold
        and color_score >= color_score_threshold
        and (
            not gradient_required
            or (
                gradient_range_ratio >= gradient_range_ratio_threshold
                and gradient_direction >= gradient_direction_threshold
            )
        )
        and shadow["shadowPassed"]
        and (
            not shadow_sensitivity["applicable"]
            or shadow_sensitivity["detected"] is True
        )
    )
    return {
        "evaluable": True,
        "cornerScore": corner_score,
        "meanCornerErrorRatio": mean_corner_error,
        "colorScore": color_score,
        "gradientRequired": gradient_required,
        "referenceGradientRange": reference_range,
        "candidateGradientRange": candidate_range,
        "gradientRangeRatio": float(gradient_range_ratio),
        "gradientDirection": gradient_direction,
        "referenceBands": reference_bands.round(3).tolist(),
        "candidateBands": candidate_bands.round(3).tolist(),
        **shadow,
        "shadowSensitivity": shadow_sensitivity,
        "thresholds": {
            "cornerScore": corner_score_threshold,
            "colorScore": color_score_threshold,
            "gradientRangeRatio": gradient_range_ratio_threshold,
            "gradientDirection": gradient_direction_threshold,
            "minimumReferenceGradientRange": MIN_REFERENCE_GRADIENT_RANGE,
            "shadowRingInnerRatio": SHADOW_RING_INNER_RATIO,
            "shadowRingOuterRatio": SHADOW_RING_OUTER_RATIO,
            "shadowBackgroundLevel": SHADOW_BACKGROUND_LEVEL,
            "minimumReferenceShadowDensity": MIN_REFERENCE_SHADOW_DENSITY,
            "shadowEnergyRatio": SHADOW_ENERGY_RATIO_THRESHOLD,
            "shadowDirectionCosine": SHADOW_DIRECTION_THRESHOLD,
        },
        "passed": passed,
    }


def compute_bottom_bevel_front_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    corner_score_threshold: float = BOTTOM_FRONT_CORNER_SCORE_THRESHOLD,
    mean_band_color_error_threshold: float = BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD,
    source_flat_fill: tuple[int, int, int] = BOTTOM_FRONT_SOURCE_FLAT_FILL,
) -> dict[str, Any]:
    """Measure the uniform native front-face response without rewarding an invented bevel rim."""
    reference_corners, reference_mask = _ordered_normalized_corners(reference)
    candidate_corners, candidate_mask = _ordered_normalized_corners(candidate)
    left, top = reference_corners.min(axis=0)
    right, bottom = reference_corners.max(axis=0)
    diagonal = math.hypot(right - left, bottom - top)
    if diagonal <= 0:
        raise ValueError("bottom-bevel front reference bounds are degenerate")
    mean_corner_error = float(
        np.linalg.norm(reference_corners - candidate_corners, axis=1).mean() / diagonal
    )
    corner_score = max(0.0, 1.0 - mean_corner_error)
    reference_bands = _material_bands(reference, reference_mask)
    candidate_bands = _material_bands(candidate, candidate_mask)
    mean_band_color_error = float(np.abs(reference_bands - candidate_bands).mean())

    mutated_candidate = candidate.copy()
    mutated_candidate[candidate_mask] = np.asarray(source_flat_fill, dtype=np.uint8)
    mutated_bands = _material_bands(mutated_candidate, candidate_mask)
    mutated_mean_band_color_error = float(np.abs(reference_bands - mutated_bands).mean())
    mutated_passed = (
        corner_score >= corner_score_threshold
        and mutated_mean_band_color_error <= mean_band_color_error_threshold
    )
    detected = not mutated_passed
    passed = (
        corner_score >= corner_score_threshold
        and mean_band_color_error <= mean_band_color_error_threshold
        and detected
    )
    return {
        "evaluable": True,
        "cornerScore": corner_score,
        "meanCornerErrorRatio": mean_corner_error,
        "referenceBands": reference_bands.round(3).tolist(),
        "candidateBands": candidate_bands.round(3).tolist(),
        "meanBandColorError": mean_band_color_error,
        "flatFillSensitivity": {
            "mutation": "restore-source-flat-fill",
            "sourceFlatFill": list(source_flat_fill),
            "mutatedBands": mutated_bands.round(3).tolist(),
            "mutatedMeanBandColorError": mutated_mean_band_color_error,
            "mutatedPassed": mutated_passed,
            "detected": detected,
        },
        "thresholds": {
            "cornerScore": corner_score_threshold,
            "meanBandColorError": mean_band_color_error_threshold,
            "sourceFlatFill": list(source_flat_fill),
        },
        "passed": passed,
    }


def _rectify_camera_plane(
    image: np.ndarray,
    corners: np.ndarray,
    size: int = PICTURE_RECTIFIED_SIZE,
) -> np.ndarray:
    source = (corners * np.asarray([image.shape[1] - 1, image.shape[0] - 1])).astype(
        np.float32
    )
    destination = np.asarray(
        [[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(
        image[..., :3],
        transform,
        (size, size),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _edge_coverage(reference: np.ndarray, candidate: np.ndarray) -> tuple[float, float, float, int]:
    reference_gray = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
    candidate_gray = cv2.cvtColor(candidate, cv2.COLOR_RGB2GRAY)
    reference_edges = cv2.Canny(reference_gray, 40, 100) > 0
    candidate_edges = cv2.Canny(candidate_gray, 40, 100) > 0
    if int(reference_edges.sum()) < 32 or int(candidate_edges.sum()) < 32:
        raise ValueError("rectified picture edges are below the resolution floor")
    tolerance_px = max(1, round(reference.shape[0] * PICTURE_EDGE_TOLERANCE_RATIO))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (tolerance_px * 2 + 1, tolerance_px * 2 + 1),
    )
    reference_dilated = cv2.dilate(reference_edges.astype(np.uint8), kernel) > 0
    candidate_dilated = cv2.dilate(candidate_edges.astype(np.uint8), kernel) > 0
    reference_coverage = float(
        np.logical_and(reference_edges, candidate_dilated).sum()
        / max(int(reference_edges.sum()), 1)
    )
    candidate_coverage = float(
        np.logical_and(candidate_edges, reference_dilated).sum()
        / max(int(candidate_edges.sum()), 1)
    )
    edge_f1 = (
        2 * reference_coverage * candidate_coverage / (reference_coverage + candidate_coverage)
        if reference_coverage + candidate_coverage > 0
        else 0.0
    )
    return reference_coverage, candidate_coverage, float(edge_f1), tolerance_px


def _picture_crop_sensitivity(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    color_score_threshold: float,
    edge_f1_threshold: float,
) -> dict[str, Any]:
    crop_pixels = max(1, round(candidate.shape[1] * PICTURE_CROP_MUTATION_RATIO))
    mutated = cv2.resize(
        candidate[:, crop_pixels:],
        (candidate.shape[1], candidate.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    color_score = max(
        0.0,
        1.0
        - float(np.abs(reference.astype(np.float64) - mutated.astype(np.float64)).mean())
        / 255,
    )
    reference_coverage, candidate_coverage, edge_f1, _ = _edge_coverage(
        reference,
        mutated,
    )
    mutated_passed = color_score >= color_score_threshold and edge_f1 >= edge_f1_threshold
    return {
        "mutation": "left-crop-and-rescale",
        "cropRatio": PICTURE_CROP_MUTATION_RATIO,
        "mutatedRectifiedColorScore": color_score,
        "mutatedRectifiedEdgeF1": edge_f1,
        "mutatedReferenceEdgeCoverageAtTolerance": reference_coverage,
        "mutatedCandidateEdgeCoverageAtTolerance": candidate_coverage,
        "mutatedPassed": mutated_passed,
        "detected": not mutated_passed,
    }


def _compute_picture_camera_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    corner_score_threshold: float = PICTURE_CORNER_SCORE_THRESHOLD,
    rectified_color_score_threshold: float = PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
    rectified_edge_f1_threshold: float = PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
    corner_extractor=_ordered_normalized_corners,
) -> dict[str, Any]:
    reference_corners, _ = corner_extractor(reference)
    candidate_corners, _ = corner_extractor(candidate)
    left, top = reference_corners.min(axis=0)
    right, bottom = reference_corners.max(axis=0)
    diagonal = math.hypot(right - left, bottom - top)
    if diagonal <= 0:
        raise ValueError("picture camera reference bounds are degenerate")
    mean_corner_error = float(
        np.linalg.norm(reference_corners - candidate_corners, axis=1).mean() / diagonal
    )
    corner_score = max(0.0, 1.0 - mean_corner_error)
    reference_rectified = _rectify_camera_plane(reference, reference_corners)
    candidate_rectified = _rectify_camera_plane(candidate, candidate_corners)
    rectified_color_score = max(
        0.0,
        1.0
        - float(
            np.abs(
                reference_rectified.astype(np.float64)
                - candidate_rectified.astype(np.float64)
            ).mean()
        )
        / 255,
    )
    reference_coverage, candidate_coverage, edge_f1, tolerance_px = _edge_coverage(
        reference_rectified,
        candidate_rectified,
    )
    crop_sensitivity = _picture_crop_sensitivity(
        reference_rectified,
        candidate_rectified,
        color_score_threshold=rectified_color_score_threshold,
        edge_f1_threshold=rectified_edge_f1_threshold,
    )
    passed = (
        corner_score >= corner_score_threshold
        and rectified_color_score >= rectified_color_score_threshold
        and edge_f1 >= rectified_edge_f1_threshold
        and crop_sensitivity["detected"] is True
    )
    return {
        "evaluable": True,
        "cornerScore": corner_score,
        "meanCornerErrorRatio": mean_corner_error,
        "rectifiedColorScore": rectified_color_score,
        "rectifiedEdgeF1": edge_f1,
        "referenceEdgeCoverageAtTolerance": reference_coverage,
        "candidateEdgeCoverageAtTolerance": candidate_coverage,
        "edgeTolerancePx": tolerance_px,
        "rectifiedSize": PICTURE_RECTIFIED_SIZE,
        "cropSensitivity": crop_sensitivity,
        "thresholds": {
            "cornerScore": corner_score_threshold,
            "rectifiedColorScore": rectified_color_score_threshold,
            "rectifiedEdgeF1": rectified_edge_f1_threshold,
            "rectifiedSize": PICTURE_RECTIFIED_SIZE,
            "edgeToleranceRatio": PICTURE_EDGE_TOLERANCE_RATIO,
            "cropMutationRatio": PICTURE_CROP_MUTATION_RATIO,
        },
        "passed": passed,
    }


def compute_picture_camera_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    corner_score_threshold: float = PICTURE_CORNER_SCORE_THRESHOLD,
    rectified_color_score_threshold: float = PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
    rectified_edge_f1_threshold: float = PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
) -> dict[str, Any]:
    return _compute_picture_camera_metrics(
        reference,
        candidate,
        corner_score_threshold=corner_score_threshold,
        rectified_color_score_threshold=rectified_color_score_threshold,
        rectified_edge_f1_threshold=rectified_edge_f1_threshold,
    )


def compute_group_picture_camera_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    corner_score_threshold: float = PICTURE_CORNER_SCORE_THRESHOLD,
    rectified_color_score_threshold: float = PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
    rectified_edge_f1_threshold: float = PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
) -> dict[str, Any]:
    return _compute_picture_camera_metrics(
        reference,
        candidate,
        corner_score_threshold=corner_score_threshold,
        rectified_color_score_threshold=rectified_color_score_threshold,
        rectified_edge_f1_threshold=rectified_edge_f1_threshold,
        corner_extractor=_ordered_group_surface_corners,
    )


def compute_text_camera_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    raster_tolerance_ratio: float = TEXT_RASTER_TOLERANCE_RATIO,
    tolerant_foreground_f1_threshold: float = TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD,
    tolerant_bounds_score_threshold: float = TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD,
    ink_coverage_ratio_threshold: float = TEXT_INK_COVERAGE_RATIO_THRESHOLD,
) -> dict[str, Any]:
    if reference.ndim != 3 or candidate.ndim != 3:
        raise ValueError("text camera metric requires RGB images")
    reference_ink_density = float((255 - reference[..., :3].astype(np.float64)).mean())
    candidate_ink_density = float((255 - candidate[..., :3].astype(np.float64)).mean())
    if max(reference_ink_density, candidate_ink_density) <= 0:
        raise ValueError("text camera ink coverage is empty")
    ink_coverage_ratio = min(reference_ink_density, candidate_ink_density) / max(
        reference_ink_density, candidate_ink_density
    )
    if candidate.shape[:2] != reference.shape[:2]:
        candidate = cv2.resize(
            candidate,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    reference_mask = _foreground_mask(reference)
    candidate_mask = _foreground_mask(candidate)
    reference_y, reference_x = np.nonzero(reference_mask)
    candidate_y, candidate_x = np.nonzero(candidate_mask)
    if reference_x.size < 32 or candidate_x.size < 32:
        raise ValueError("text camera foreground is below the resolution floor")

    intersection = int(np.logical_and(reference_mask, candidate_mask).sum())
    union = int(np.logical_or(reference_mask, candidate_mask).sum())
    foreground_iou = intersection / max(union, 1)
    raster_tolerance_px = max(1, round(max(reference.shape[:2]) * raster_tolerance_ratio))
    tolerance_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (raster_tolerance_px * 2 + 1, raster_tolerance_px * 2 + 1),
    )
    reference_dilated = cv2.dilate(reference_mask.astype(np.uint8), tolerance_kernel) > 0
    candidate_dilated = cv2.dilate(candidate_mask.astype(np.uint8), tolerance_kernel) > 0
    reference_coverage = float(
        np.logical_and(reference_mask, candidate_dilated).sum()
        / max(int(reference_mask.sum()), 1)
    )
    candidate_coverage = float(
        np.logical_and(candidate_mask, reference_dilated).sum()
        / max(int(candidate_mask.sum()), 1)
    )
    tolerant_foreground_f1 = (
        2 * reference_coverage * candidate_coverage / (reference_coverage + candidate_coverage)
        if reference_coverage + candidate_coverage > 0
        else 0.0
    )
    reference_bounds = np.asarray(
        [reference_x.min(), reference_y.min(), reference_x.max(), reference_y.max()],
        dtype=np.float64,
    )
    candidate_bounds = np.asarray(
        [candidate_x.min(), candidate_y.min(), candidate_x.max(), candidate_y.max()],
        dtype=np.float64,
    )
    reference_diagonal = math.hypot(
        reference_bounds[2] - reference_bounds[0],
        reference_bounds[3] - reference_bounds[1],
    )
    if reference_diagonal <= 0:
        raise ValueError("text camera reference bounds are degenerate")
    top_left_error = np.linalg.norm(reference_bounds[:2] - candidate_bounds[:2])
    bottom_right_error = np.linalg.norm(reference_bounds[2:] - candidate_bounds[2:])
    mean_bounds_error_ratio = float(
        (top_left_error + bottom_right_error) / (2 * reference_diagonal)
    )
    bounds_score = max(0.0, 1.0 - mean_bounds_error_ratio)
    tolerant_bounds_delta = np.maximum(
        np.abs(reference_bounds - candidate_bounds) - raster_tolerance_px,
        0,
    )
    tolerant_mean_bounds_error_ratio = float(
        (
            np.linalg.norm(tolerant_bounds_delta[:2])
            + np.linalg.norm(tolerant_bounds_delta[2:])
        )
        / (2 * reference_diagonal)
    )
    tolerant_bounds_score = max(0.0, 1.0 - tolerant_mean_bounds_error_ratio)
    passed = (
        tolerant_foreground_f1 >= tolerant_foreground_f1_threshold
        and tolerant_bounds_score >= tolerant_bounds_score_threshold
        and ink_coverage_ratio >= ink_coverage_ratio_threshold
    )
    return {
        "evaluable": True,
        "foregroundIou": float(foreground_iou),
        "boundsScore": bounds_score,
        "meanBoundsErrorRatio": mean_bounds_error_ratio,
        "rasterTolerancePx": raster_tolerance_px,
        "referenceCoverageAtTolerance": reference_coverage,
        "candidateCoverageAtTolerance": candidate_coverage,
        "tolerantForegroundF1": float(tolerant_foreground_f1),
        "tolerantBoundsScore": tolerant_bounds_score,
        "tolerantMeanBoundsErrorRatio": tolerant_mean_bounds_error_ratio,
        "inkCoverageRatio": float(ink_coverage_ratio),
        "referenceInkDensity": reference_ink_density,
        "candidateInkDensity": candidate_ink_density,
        "referenceBounds": reference_bounds.astype(int).tolist(),
        "candidateBounds": candidate_bounds.astype(int).tolist(),
        "thresholds": {
            "rasterToleranceRatio": raster_tolerance_ratio,
            "tolerantForegroundF1": tolerant_foreground_f1_threshold,
            "tolerantBoundsScore": tolerant_bounds_score_threshold,
            "inkCoverageRatio": ink_coverage_ratio_threshold,
        },
        "passed": passed,
    }


def _custom_background_color(image: np.ndarray) -> np.ndarray:
    edge = max(2, round(min(image.shape[:2]) * 0.01))
    samples = np.concatenate(
        [
            image[:edge, :edge, :3].reshape(-1, 3),
            image[:edge, -edge:, :3].reshape(-1, 3),
            image[-edge:, :edge, :3].reshape(-1, 3),
            image[-edge:, -edge:, :3].reshape(-1, 3),
        ]
    )
    return np.median(samples.astype(np.float64), axis=0)


def _custom_foreground_mask(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("custom camera metric requires an RGB image")
    background = _custom_background_color(image)
    delta = np.max(np.abs(image[..., :3].astype(np.float64) - background), axis=2)
    raw = (delta > 10).astype(np.uint8)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(raw, 8)
    mask = np.zeros(raw.shape, dtype=bool)
    for component in range(1, component_count):
        x, y, width, height, area = stats[component]
        touches_frame = (
            x == 0
            or y == 0
            or x + width == raw.shape[1]
            or y + height == raw.shape[0]
        )
        if area >= 32 and not touches_frame:
            mask |= labels == component
    if int(mask.sum()) < 64:
        raise ValueError("custom camera foreground is below the resolution floor")
    return mask, background


def _custom_geometry_core_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    raster_tolerance_ratio: float,
    tolerant_foreground_f1_threshold: float,
    tolerant_bounds_score_threshold: float,
    foreground_area_ratio_threshold: float,
    color_score_threshold: float,
) -> dict[str, Any]:
    if candidate.shape[:2] != reference.shape[:2]:
        candidate = cv2.resize(
            candidate,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    reference_mask, _ = _custom_foreground_mask(reference)
    candidate_mask, _ = _custom_foreground_mask(candidate)
    reference_y, reference_x = np.nonzero(reference_mask)
    candidate_y, candidate_x = np.nonzero(candidate_mask)

    raster_tolerance_px = max(1, round(max(reference.shape[:2]) * raster_tolerance_ratio))
    tolerance_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (raster_tolerance_px * 2 + 1, raster_tolerance_px * 2 + 1),
    )
    reference_dilated = cv2.dilate(reference_mask.astype(np.uint8), tolerance_kernel) > 0
    candidate_dilated = cv2.dilate(candidate_mask.astype(np.uint8), tolerance_kernel) > 0
    reference_coverage = float(
        np.logical_and(reference_mask, candidate_dilated).sum()
        / max(int(reference_mask.sum()), 1)
    )
    candidate_coverage = float(
        np.logical_and(candidate_mask, reference_dilated).sum()
        / max(int(candidate_mask.sum()), 1)
    )
    tolerant_foreground_f1 = (
        2 * reference_coverage * candidate_coverage / (reference_coverage + candidate_coverage)
        if reference_coverage + candidate_coverage > 0
        else 0.0
    )

    reference_bounds = np.asarray(
        [reference_x.min(), reference_y.min(), reference_x.max(), reference_y.max()],
        dtype=np.float64,
    )
    candidate_bounds = np.asarray(
        [candidate_x.min(), candidate_y.min(), candidate_x.max(), candidate_y.max()],
        dtype=np.float64,
    )
    reference_diagonal = math.hypot(
        reference_bounds[2] - reference_bounds[0],
        reference_bounds[3] - reference_bounds[1],
    )
    if reference_diagonal <= 0:
        raise ValueError("custom camera reference bounds are degenerate")
    tolerant_bounds_delta = np.maximum(
        np.abs(reference_bounds - candidate_bounds) - raster_tolerance_px,
        0,
    )
    tolerant_mean_bounds_error_ratio = float(
        (
            np.linalg.norm(tolerant_bounds_delta[:2])
            + np.linalg.norm(tolerant_bounds_delta[2:])
        )
        / (2 * reference_diagonal)
    )
    tolerant_bounds_score = max(0.0, 1.0 - tolerant_mean_bounds_error_ratio)
    reference_centroid = np.asarray([reference_x.mean(), reference_y.mean()])
    candidate_centroid = np.asarray([candidate_x.mean(), candidate_y.mean()])
    centroid_error_ratio = float(
        np.linalg.norm(reference_centroid - candidate_centroid) / reference_diagonal
    )
    centroid_score = max(0.0, 1.0 - centroid_error_ratio)
    foreground_area_ratio = float(
        min(int(reference_mask.sum()), int(candidate_mask.sum()))
        / max(int(reference_mask.sum()), int(candidate_mask.sum()))
    )
    reference_color = np.median(reference[..., :3][reference_mask], axis=0).astype(np.float64)
    candidate_color = np.median(candidate[..., :3][candidate_mask], axis=0).astype(np.float64)
    mean_color_error = float(np.abs(reference_color - candidate_color).mean())
    color_score = max(0.0, 1.0 - mean_color_error / 255)
    passed = (
        tolerant_foreground_f1 >= tolerant_foreground_f1_threshold
        and tolerant_bounds_score >= tolerant_bounds_score_threshold
        and foreground_area_ratio >= foreground_area_ratio_threshold
        and centroid_score >= 0.99
        and color_score >= color_score_threshold
    )
    return {
        "evaluable": True,
        "rasterTolerancePx": raster_tolerance_px,
        "referenceCoverageAtTolerance": reference_coverage,
        "candidateCoverageAtTolerance": candidate_coverage,
        "tolerantForegroundF1": float(tolerant_foreground_f1),
        "tolerantBoundsScore": tolerant_bounds_score,
        "tolerantMeanBoundsErrorRatio": tolerant_mean_bounds_error_ratio,
        "foregroundAreaRatio": foreground_area_ratio,
        "referenceForegroundPixels": int(reference_mask.sum()),
        "candidateForegroundPixels": int(candidate_mask.sum()),
        "centroidScore": centroid_score,
        "centroidErrorRatio": centroid_error_ratio,
        "colorScore": color_score,
        "meanColorError": mean_color_error,
        "referenceColor": reference_color.round(3).tolist(),
        "candidateColor": candidate_color.round(3).tolist(),
        "referenceBounds": reference_bounds.astype(int).tolist(),
        "candidateBounds": candidate_bounds.astype(int).tolist(),
        "thresholds": {
            "rasterToleranceRatio": raster_tolerance_ratio,
            "tolerantForegroundF1": tolerant_foreground_f1_threshold,
            "tolerantBoundsScore": tolerant_bounds_score_threshold,
            "foregroundAreaRatio": foreground_area_ratio_threshold,
            "centroidScore": 0.99,
            "colorScore": color_score_threshold,
            "verticalSquashRatio": CUSTOM_VERTICAL_SQUASH_RATIO,
        },
        "passed": passed,
    }


def _vertical_squash_candidate(candidate: np.ndarray, ratio: float) -> np.ndarray:
    mask, background = _custom_foreground_mask(candidate)
    y, x = np.nonzero(mask)
    left, right = int(x.min()), int(x.max()) + 1
    top, bottom = int(y.min()), int(y.max()) + 1
    crop = candidate[top:bottom, left:right, :3]
    crop_mask = mask[top:bottom, left:right]
    squashed_height = max(1, round(crop.shape[0] * ratio))
    squashed = cv2.resize(
        crop,
        (crop.shape[1], squashed_height),
        interpolation=cv2.INTER_AREA,
    )
    squashed_mask = cv2.resize(
        crop_mask.astype(np.uint8),
        (crop.shape[1], squashed_height),
        interpolation=cv2.INTER_NEAREST,
    ) > 0
    output = np.empty_like(candidate[..., :3])
    output[:] = np.rint(background).astype(np.uint8)
    center_y = (top + bottom) // 2
    next_top = max(0, min(output.shape[0] - squashed_height, center_y - squashed_height // 2))
    target = output[next_top : next_top + squashed_height, left:right]
    target[squashed_mask] = squashed[squashed_mask]
    return output


def compute_custom_geometry_camera_metrics(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    raster_tolerance_ratio: float = CUSTOM_RASTER_TOLERANCE_RATIO,
    tolerant_foreground_f1_threshold: float = CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD,
    tolerant_bounds_score_threshold: float = CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD,
    foreground_area_ratio_threshold: float = CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD,
    color_score_threshold: float = CUSTOM_COLOR_SCORE_THRESHOLD,
) -> dict[str, Any]:
    if reference.ndim != 3 or candidate.ndim != 3:
        raise ValueError("custom camera metric requires RGB images")
    if candidate.shape[:2] != reference.shape[:2]:
        candidate = cv2.resize(
            candidate,
            (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    metrics = _custom_geometry_core_metrics(
        reference,
        candidate,
        raster_tolerance_ratio=raster_tolerance_ratio,
        tolerant_foreground_f1_threshold=tolerant_foreground_f1_threshold,
        tolerant_bounds_score_threshold=tolerant_bounds_score_threshold,
        foreground_area_ratio_threshold=foreground_area_ratio_threshold,
        color_score_threshold=color_score_threshold,
    )
    mutated = _vertical_squash_candidate(candidate, CUSTOM_VERTICAL_SQUASH_RATIO)
    mutated_metrics = _custom_geometry_core_metrics(
        reference,
        mutated,
        raster_tolerance_ratio=raster_tolerance_ratio,
        tolerant_foreground_f1_threshold=tolerant_foreground_f1_threshold,
        tolerant_bounds_score_threshold=tolerant_bounds_score_threshold,
        foreground_area_ratio_threshold=foreground_area_ratio_threshold,
        color_score_threshold=color_score_threshold,
    )
    sensitivity = {
        "mutation": "vertical-squash",
        "ratio": CUSTOM_VERTICAL_SQUASH_RATIO,
        "mutatedTolerantForegroundF1": mutated_metrics["tolerantForegroundF1"],
        "mutatedTolerantBoundsScore": mutated_metrics["tolerantBoundsScore"],
        "mutatedForegroundAreaRatio": mutated_metrics["foregroundAreaRatio"],
        "mutatedCentroidScore": mutated_metrics["centroidScore"],
        "mutatedColorScore": mutated_metrics["colorScore"],
        "mutatedPassed": mutated_metrics["passed"],
        "detected": not mutated_metrics["passed"],
    }
    metrics["squashSensitivity"] = sensitivity
    metrics["passed"] = metrics["passed"] and sensitivity["detected"]
    return metrics


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
        raise ValueError(f"camera artifact must be inside the repository: {path}") from error


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON report must be an object: {path}")
    return value


def build_camera_report(
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
        plane_slides = extract_camera_slide_indices(source_path)
        bottom_front_slides = extract_bottom_bevel_front_slide_indices(source_path)
        shadow_slides = extract_camera_shadow_slide_indices(source_path)
        text_slides = extract_text_camera_slide_indices(source_path)
        picture_slides = extract_picture_camera_slide_indices(source_path)
        group_picture_slides = extract_group_picture_camera_slide_indices(source_path)
        custom_slides = extract_custom_geometry_camera_slide_indices(source_path)
        applicable_slides = (
            plane_slides
            | bottom_front_slides
            | text_slides
            | picture_slides
            | group_picture_slides
            | custom_slides
        )
        slide_results: list[dict[str, Any]] = []
        for slide in case_report.get("perSlide", []):
            if not isinstance(slide, Mapping) or slide.get("hidden") is True:
                continue
            slide_index = slide.get("slideIdx")
            if not isinstance(slide_index, int) or slide_index not in applicable_slides:
                continue
            render_artifacts = slide.get("renderArtifacts")
            if not isinstance(render_artifacts, Mapping):
                raise ValueError(
                    f"native report is missing render artifacts: {case_id} slide {slide_index}"
                )
            artifact_values: dict[str, dict[str, Any]] = {}
            for kind, suffix in (("reference", "pdf"), ("candidate", "html")):
                path = reports_dir / f"{case_id}_slide{slide_index}_{suffix}.png"
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
            reference = np.asarray(
                Image.open(reports_dir / f"{case_id}_slide{slide_index}_pdf.png").convert("RGB")
            )
            candidate = np.asarray(
                Image.open(reports_dir / f"{case_id}_slide{slide_index}_html.png").convert("RGB")
            )
            if slide_index in custom_slides:
                modality = "custom-geometry"
                metrics = compute_custom_geometry_camera_metrics(reference, candidate)
            elif slide_index in bottom_front_slides:
                modality = "bottom-material"
                metrics = compute_bottom_bevel_front_metrics(reference, candidate)
            elif slide_index in plane_slides:
                modality = "plane"
                metrics = compute_camera_plane_metrics(
                    reference,
                    candidate,
                    shadow_required=slide_index in shadow_slides,
                )
            elif slide_index in text_slides:
                modality = "text"
                metrics = compute_text_camera_metrics(reference, candidate)
            elif slide_index in group_picture_slides:
                modality = "picture-group"
                metrics = compute_group_picture_camera_metrics(reference, candidate)
            else:
                modality = "picture"
                metrics = compute_picture_camera_metrics(reference, candidate)
            slide_results.append(
                {
                    "slideIdx": slide_index,
                    "modality": modality,
                    "referencePath": artifact_values["reference"]["path"],
                    "candidatePath": artifact_values["candidate"]["path"],
                    "referenceSha256": artifact_values["reference"]["sha256"],
                    "candidateSha256": artifact_values["candidate"]["sha256"],
                    "metrics": metrics,
                    "passed": metrics["passed"],
                }
            )
        applicable = bool(applicable_slides)
        passed = applicable and len(slide_results) == len(applicable_slides) and all(
            slide["passed"] for slide in slide_results
        )
        cases.append(
            {
                "caseId": case_id,
                "sourceSha256": source_info.get("sha256"),
                "groundTruthSha256": ground_truth_info.get("combinedSha256"),
                "applicable": applicable,
                "passed": passed,
                "slides": slide_results,
            }
        )
    applicable_count = sum(1 for case in cases if case["applicable"])
    return {
        "schemaVersion": 7,
        "renderer": dict(renderer or {}),
        "thresholds": {
            "plane": {
                "cornerScore": CORNER_SCORE_THRESHOLD,
                "colorScore": COLOR_SCORE_THRESHOLD,
                "gradientRangeRatio": GRADIENT_RANGE_RATIO_THRESHOLD,
                "gradientDirection": GRADIENT_DIRECTION_THRESHOLD,
                "minimumReferenceGradientRange": MIN_REFERENCE_GRADIENT_RANGE,
                "shadowRingInnerRatio": SHADOW_RING_INNER_RATIO,
                "shadowRingOuterRatio": SHADOW_RING_OUTER_RATIO,
                "shadowBackgroundLevel": SHADOW_BACKGROUND_LEVEL,
                "minimumReferenceShadowDensity": MIN_REFERENCE_SHADOW_DENSITY,
                "shadowEnergyRatio": SHADOW_ENERGY_RATIO_THRESHOLD,
                "shadowDirectionCosine": SHADOW_DIRECTION_THRESHOLD,
            },
            "text": {
                "rasterToleranceRatio": TEXT_RASTER_TOLERANCE_RATIO,
                "tolerantForegroundF1": TEXT_TOLERANT_FOREGROUND_F1_THRESHOLD,
                "tolerantBoundsScore": TEXT_TOLERANT_BOUNDS_SCORE_THRESHOLD,
                "inkCoverageRatio": TEXT_INK_COVERAGE_RATIO_THRESHOLD,
            },
            "picture": {
                "cornerScore": PICTURE_CORNER_SCORE_THRESHOLD,
                "rectifiedColorScore": PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
                "rectifiedEdgeF1": PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
                "rectifiedSize": PICTURE_RECTIFIED_SIZE,
                "edgeToleranceRatio": PICTURE_EDGE_TOLERANCE_RATIO,
                "cropMutationRatio": PICTURE_CROP_MUTATION_RATIO,
            },
            "picture-group": {
                "cornerScore": PICTURE_CORNER_SCORE_THRESHOLD,
                "rectifiedColorScore": PICTURE_RECTIFIED_COLOR_SCORE_THRESHOLD,
                "rectifiedEdgeF1": PICTURE_RECTIFIED_EDGE_F1_THRESHOLD,
                "rectifiedSize": PICTURE_RECTIFIED_SIZE,
                "edgeToleranceRatio": PICTURE_EDGE_TOLERANCE_RATIO,
                "cropMutationRatio": PICTURE_CROP_MUTATION_RATIO,
            },
            "bottom-material": {
                "cornerScore": BOTTOM_FRONT_CORNER_SCORE_THRESHOLD,
                "meanBandColorError": BOTTOM_FRONT_MEAN_BAND_COLOR_ERROR_THRESHOLD,
                "sourceFlatFill": list(BOTTOM_FRONT_SOURCE_FLAT_FILL),
            },
            "custom-geometry": {
                "rasterToleranceRatio": CUSTOM_RASTER_TOLERANCE_RATIO,
                "tolerantForegroundF1": CUSTOM_TOLERANT_FOREGROUND_F1_THRESHOLD,
                "tolerantBoundsScore": CUSTOM_TOLERANT_BOUNDS_SCORE_THRESHOLD,
                "foregroundAreaRatio": CUSTOM_FOREGROUND_AREA_RATIO_THRESHOLD,
                "centroidScore": 0.99,
                "colorScore": CUSTOM_COLOR_SCORE_THRESHOLD,
                "verticalSquashRatio": CUSTOM_VERTICAL_SQUASH_RATIO,
            },
        },
        "caseResults": sorted(cases, key=lambda case: case["caseId"]),
        "applicableCaseCount": applicable_count,
        "passed": applicable_count > 0 and all(case["passed"] for case in cases),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate local Shape 3D camera-plane fidelity")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--case-report", action="append", required=True)
    parser.add_argument("--reports-dir", default="test/e2e/reports")
    parser.add_argument(
        "--out",
        default="test/e2e/reports/capability-loop/shape3d-camera-local.json",
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
        report = build_camera_report(
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
        f"evaluated {report['applicableCaseCount']} applicable camera case(s); "
        f"passed={report['passed']} -> {output}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
