from __future__ import annotations

import hashlib
import json
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import numpy as np
import pytest
from PIL import Image

from scripts.shape3d_bevel_metrics import (
    BevelRegion,
    _donut_shadow_sector_metrics,
    _field_score,
    build_bevel_report,
    compute_bevel_ring_metrics,
    extract_shape3d_regions,
)


def test_shadow_local_excess_detects_spatial_overdarkening_hidden_by_equal_total_energy():
    reference = np.linspace(-50, 50, 100, dtype=np.float32)
    candidate = reference.copy()
    first = candidate[:20].copy()
    candidate[:20] = candidate[20:40]
    candidate[20:40] = first

    result = _field_score(
        reference.reshape(10, 10),
        candidate.reshape(10, 10),
        np.ones((10, 10), dtype=bool),
    )

    assert result["score"] > 0.9
    assert result["shadowOvershootRatio"] == pytest.approx(1.0)
    assert result["shadowEnergyOvershootRatio"] == pytest.approx(1.0)
    assert result["shadowLocalExcessRatio"] > 0.30


def test_donut_shadow_sectors_detect_a_local_lobe_hidden_by_equal_total_energy():
    size = 120
    region = BevelRegion(
        slide_index=0,
        x=0,
        y=0,
        width=1,
        height=1,
        bevel_width_x=0.1,
        bevel_width_y=0.1,
        preset="donut",
        geometry_adjustment=0.25,
        geometry_inset_x_ratio=0.25,
        geometry_inset_y_ratio=0.25,
    )
    yy, xx = np.mgrid[:size, :size]
    center = (size - 1) / 2
    outer = ((xx - center) / (size / 2)) ** 2 + ((yy - center) / (size / 2)) ** 2 <= 1
    inner = ((xx - center) / (size / 4)) ** 2 + ((yy - center) / (size / 4)) ** 2 < 1
    ring = outer & ~inner
    angle = (np.degrees(np.arctan2(yy - center, xx - center)) + 360) % 360
    lobe = ring & (angle >= 60) & (angle < 90)
    reference_delta = np.zeros((size, size), dtype=np.float32)
    candidate_delta = np.zeros((size, size), dtype=np.float32)
    reference_delta[ring] = -10
    other_count = np.count_nonzero(ring & ~lobe)
    balanced_shadow = (
        10 * np.count_nonzero(ring) - 18 * np.count_nonzero(lobe)
    ) / other_count
    candidate_delta[ring] = -balanced_shadow
    candidate_delta[lobe] = -18

    result = _donut_shadow_sector_metrics(reference_delta, candidate_delta, ring, region)

    assert np.mean(np.maximum(-candidate_delta[ring], 0)) == pytest.approx(10)
    assert result["shadowSectorOvershootRatio"] == pytest.approx(1.8)
    assert any(
        sector["contour"] == "outer"
        and sector["startAngle"] == 60
        and sector["overshootRatio"] == pytest.approx(1.8)
        for sector in result["shadowSectors"]
    )


def _rounded_mask(height: int, width: int, bounds: tuple[int, int, int, int], radius: int):
    x, y, w, h = bounds
    yy, xx = np.mgrid[:height, :width]
    nearest_x = np.clip(xx, x + radius, x + w - radius - 1)
    nearest_y = np.clip(yy, y + radius, y + h - radius - 1)
    return (
        (xx >= x)
        & (xx < x + w)
        & (yy >= y)
        & (yy < y + h)
        & ((xx - nearest_x) ** 2 + (yy - nearest_y) ** 2 <= radius**2)
    )


def _bevel_specimen(height: int = 200, width: int = 300):
    bounds = (60, 50, 180, 100)
    radius = 20
    mask = _rounded_mask(height, width, bounds, radius)
    padded = np.pad(mask.astype(np.uint8), 1)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
    grad_x = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3)
    grad_length = np.maximum(np.hypot(grad_x, grad_y), 1e-6)
    outward_x = -grad_x / grad_length
    outward_y = -grad_y / grad_length
    profile = np.clip(1 - distance / 12, 0, 1)
    delta = (outward_x * -0.12 + outward_y * -0.82 - 0.16) * profile * 72

    reference = np.full((height, width, 3), 255, dtype=np.uint8)
    flat = reference.copy()
    base = np.array([50, 126, 196], dtype=np.float32)
    reference[mask] = np.clip(base + delta[mask, None], 0, 255).astype(np.uint8)
    flat[mask] = base.astype(np.uint8)
    region = BevelRegion(
        slide_index=0,
        x=bounds[0] / width,
        y=bounds[1] / height,
        width=bounds[2] / width,
        height=bounds[3] / height,
        bevel_width_x=12 / width,
        bevel_width_y=12 / height,
        preset="roundRect",
        corner_radius_ratio=radius / min(bounds[2], bounds[3]),
    )
    return reference, flat, region


def _elliptical_bevel_specimen(preset: str):
    height, width = 200, 300
    bounds = (60, 40, 180, 120)
    x, y, shape_width, shape_height = bounds
    yy, xx = np.mgrid[:height, :width]
    center_x = x + (shape_width - 1) / 2
    center_y = y + (shape_height - 1) / 2
    outer = (
        ((xx - center_x) / (shape_width / 2)) ** 2
        + ((yy - center_y) / (shape_height / 2)) ** 2
        <= 1
    )
    adjustment = 0.32 if preset == "donut" else 0.0
    mask = outer
    if preset == "donut":
        inset = min(shape_width, shape_height) * adjustment
        inner = (
            ((xx - center_x) / (shape_width / 2 - inset)) ** 2
            + ((yy - center_y) / (shape_height / 2 - inset)) ** 2
            < 1
        )
        mask = outer & ~inner

    padded = np.pad(mask.astype(np.uint8), 1)
    distance = cv2.distanceTransform(padded, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)[1:-1, 1:-1]
    grad_x = cv2.Sobel(distance, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(distance, cv2.CV_32F, 0, 1, ksize=3)
    grad_length = np.maximum(np.hypot(grad_x, grad_y), 1e-6)
    outward_x = -grad_x / grad_length
    outward_y = -grad_y / grad_length
    profile = np.clip(1 - distance / 12, 0, 1)
    delta = (outward_x * -0.12 + outward_y * -0.82 - 0.16) * profile * 72

    reference = np.full((height, width, 3), 255, dtype=np.uint8)
    flat = reference.copy()
    base = np.array([50, 126, 196], dtype=np.float32)
    reference[mask] = np.clip(base + delta[mask, None], 0, 255).astype(np.uint8)
    flat[mask] = base.astype(np.uint8)
    region = BevelRegion(
        slide_index=0,
        x=x / width,
        y=y / height,
        width=shape_width / width,
        height=shape_height / height,
        bevel_width_x=12 / width,
        bevel_width_y=12 / height,
        preset=preset,
        geometry_adjustment=adjustment,
        geometry_inset_x_ratio=(min(shape_width, shape_height) * adjustment / shape_width),
        geometry_inset_y_ratio=(min(shape_width, shape_height) * adjustment / shape_height),
    )
    return reference, flat, region


def test_bevel_ring_metrics_reject_a_flat_edge_and_accept_directional_curvature():
    reference, flat, region = _bevel_specimen()

    matched = compute_bevel_ring_metrics(reference, reference, region)
    flattened = compute_bevel_ring_metrics(reference, flat, region)

    assert matched["passed"] is True
    assert matched["score"] > 0.99
    assert matched["cornerScore"] > 0.99
    assert flattened["passed"] is False
    assert flattened["score"] < 0.55
    assert flattened["cornerScore"] < 0.55
    assert flattened["ringPixelCount"] < reference.shape[0] * reference.shape[1] * 0.2


def test_bevel_ring_metrics_reject_an_overdark_shadow_even_with_a_high_composite_score():
    reference, _flat, region = _bevel_specimen()
    base = np.array([50, 126, 196], dtype=np.float32)
    candidate = reference.astype(np.float32)
    delta = candidate - base
    shadow = np.mean(delta, axis=2) < 0
    candidate[shadow] = base + delta[shadow] * 1.8

    result = compute_bevel_ring_metrics(
        reference,
        np.clip(candidate, 0, 255).astype(np.uint8),
        region,
    )

    assert result["score"] > 0.85
    assert result["highlightAmplitudeRatio"] > 0.95
    assert result["shadowAmplitudeRatio"] < 0.85
    assert result["passed"] is False


def test_bevel_ring_metrics_rejects_a_visible_shadow_overshoot_inside_the_symmetric_budget():
    reference, _flat, region = _bevel_specimen()
    base = np.array([50, 126, 196], dtype=np.float32)
    candidate = reference.astype(np.float32)
    delta = candidate - base
    shadow = np.mean(delta, axis=2) < 0
    candidate[shadow] = base + delta[shadow] * 1.10

    result = compute_bevel_ring_metrics(
        reference,
        np.clip(candidate, 0, 255).astype(np.uint8),
        region,
    )

    assert result["score"] > 0.85
    assert result["shadowAmplitudeRatio"] > 0.85
    assert result["shadowOvershootRatio"] > 1.05
    assert result["passed"] is False


def test_bevel_ring_metrics_apply_the_tighter_native_donut_shadow_budget():
    reference, _flat, region = _elliptical_bevel_specimen("donut")
    base = np.array([50, 126, 196], dtype=np.float32)
    candidate = reference.astype(np.float32)
    delta = candidate - base
    shadow = np.mean(delta, axis=2) < 0
    candidate[shadow] = base + delta[shadow] * 1.03

    result = compute_bevel_ring_metrics(
        reference,
        np.clip(candidate, 0, 255).astype(np.uint8),
        region,
    )

    assert 1.01 < result["shadowOvershootRatio"] < 1.05
    assert result["thresholds"]["solidDonutShadowOvershootRatio"] == 1.01
    assert result["passed"] is False


def test_bevel_ring_metrics_reject_a_donut_shadow_band_that_is_too_heavy_in_aggregate():
    reference, _flat, region = _elliptical_bevel_specimen("donut")
    base = np.array([50, 126, 196], dtype=np.float32)
    candidate = reference.astype(np.float32)
    delta = np.mean(candidate - base, axis=2)
    moderately_dark = (delta > -20) & (delta < -3)
    candidate[moderately_dark] -= 4

    result = compute_bevel_ring_metrics(
        reference,
        np.clip(candidate, 0, 255).astype(np.uint8),
        region,
    )

    assert result["shadowOvershootRatio"] < 1.01
    assert result["shadowEnergyOvershootRatio"] > 1.05
    assert result["thresholds"]["solidDonutShadowEnergyOvershootRatio"] == 1.05
    assert result["passed"] is False


def test_bevel_ring_metrics_keep_picture_highlight_threshold_separate_from_solid_materials():
    reference, _flat, shape_region = _bevel_specimen()
    base = np.array([50, 126, 196], dtype=np.float32)
    candidate = reference.astype(np.float32)
    delta = candidate - base
    highlight = np.mean(delta, axis=2) > 0
    candidate[highlight] = base + delta[highlight] * 0.75
    candidate = np.clip(candidate, 0, 255).astype(np.uint8)
    picture_region = BevelRegion(**{**shape_region.__dict__, "surface": "picture"})

    shape_result = compute_bevel_ring_metrics(reference, candidate, shape_region)
    picture_result = compute_bevel_ring_metrics(reference, candidate, picture_region)

    assert 0.70 < shape_result["highlightAmplitudeRatio"] < 0.80
    assert shape_result["passed"] is False
    assert picture_result["passed"] is True


@pytest.mark.parametrize("preset", ["ellipse", "donut"])
def test_bevel_ring_metrics_follow_elliptical_silhouettes_and_holes(preset: str):
    reference, flat, region = _elliptical_bevel_specimen(preset)

    matched = compute_bevel_ring_metrics(reference, reference, region)
    flattened = compute_bevel_ring_metrics(reference, flat, region)

    assert matched["passed"] is True
    assert matched["score"] > 0.99
    assert flattened["passed"] is False
    assert flattened["score"] < 0.55


def test_bevel_ring_score_is_not_diluted_by_more_background_pixels():
    reference, flat, region = _bevel_specimen()
    small = compute_bevel_ring_metrics(reference, flat, region)

    large_reference = np.full((600, 900, 3), 255, dtype=np.uint8)
    large_flat = large_reference.copy()
    large_reference[200:400, 300:600] = reference
    large_flat[200:400, 300:600] = flat
    large_region = BevelRegion(
        slide_index=0,
        x=(300 + region.x * 300) / 900,
        y=(200 + region.y * 200) / 600,
        width=region.width * 300 / 900,
        height=region.height * 200 / 600,
        bevel_width_x=region.bevel_width_x * 300 / 900,
        bevel_width_y=region.bevel_width_y * 200 / 600,
        preset=region.preset,
        corner_radius_ratio=region.corner_radius_ratio,
    )
    large = compute_bevel_ring_metrics(large_reference, large_flat, large_region)

    assert large["passed"] is False
    assert abs(large["score"] - small["score"]) < 0.01
    assert large["ringPixelCount"] == small["ringPixelCount"]


def test_bevel_ring_reports_resolution_limited_instead_of_guessing():
    reference, _flat, region = _bevel_specimen()
    narrow = BevelRegion(
        **{
            **region.__dict__,
            "bevel_width_x": 2 / reference.shape[1],
            "bevel_width_y": 2 / reference.shape[0],
        }
    )

    result = compute_bevel_ring_metrics(reference, reference, narrow)

    assert result == {
        "evaluable": False,
        "passed": True,
        "reason": "bevel-band-below-resolution-floor",
        "boundsPx": [60, 50, 180, 100],
        "bandWidthPx": 2.0,
        "thresholds": {
            "minimumBandWidthPx": 4.0,
            "score": 0.6,
            "cornerScore": 0.78,
            "rangeRatio": 0.85,
            "highlightAmplitudeRatio": 0.8,
            "pictureHighlightAmplitudeRatio": 0.7,
            "shadowAmplitudeRatio": 0.85,
                "shadowOvershootRatio": 1.05,
                "solidDonutShadowOvershootRatio": 1.01,
                "solidDonutShadowEnergyOvershootRatio": 1.05,
                "solidDonutShadowLocalExcessRatio": 0.30,
                "solidDonutShadowSectorOvershootRatio": 1.60,
        },
    }


@pytest.mark.parametrize(
    ("preset", "rotation", "reason"),
    [
        ("star5", 0.0, "geometry-mask-unsupported"),
        ("roundRect", 30.0, "shape-rotation-unsupported"),
    ],
)
def test_bevel_ring_refuses_geometry_it_cannot_mask_independently(
    preset: str,
    rotation: float,
    reason: str,
):
    reference, _flat, base_region = _bevel_specimen()
    region = BevelRegion(
        **{
            **base_region.__dict__,
            "preset": preset,
            "rotation_degrees": rotation,
        }
    )

    result = compute_bevel_ring_metrics(reference, reference, region)

    assert result["evaluable"] is False
    assert result["passed"] is False
    assert result["reason"] == reason


def test_corner_gate_applies_only_to_roundrect_geometry():
    reference, _flat, roundrect = _bevel_specimen()
    rectangle = BevelRegion(
        **{
            **roundrect.__dict__,
            "preset": "rect",
            "corner_radius_ratio": 0.0,
        }
    )

    rectangle_result = compute_bevel_ring_metrics(
        reference,
        reference,
        rectangle,
        corner_score_threshold=1.01,
    )
    roundrect_result = compute_bevel_ring_metrics(
        reference,
        reference,
        roundrect,
        corner_score_threshold=1.01,
    )

    assert rectangle_result["score"] > 0.99
    assert rectangle_result["cornerRequired"] is False
    assert rectangle_result["passed"] is True
    assert roundrect_result["cornerRequired"] is True
    assert roundrect_result["passed"] is False


def test_extracts_group_scaled_bounds_and_bevel_width_from_ooxml(tmp_path):
    source = tmp_path / "grouped.pptx"
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="1000" cy="500"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:grpSp>
          <p:grpSpPr><a:xfrm>
            <a:off x="100" y="50"/><a:ext cx="400" cy="200"/>
            <a:chOff x="10" y="20"/><a:chExt cx="200" cy="100"/>
          </a:xfrm></p:grpSpPr>
          <p:sp><p:spPr>
            <a:xfrm><a:off x="10" y="20"/><a:ext cx="100" cy="50"/></a:xfrm>
            <a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 25000"/></a:avLst></a:prstGeom>
            <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
            <a:sp3d><a:bevelT w="10" h="8" prst="circle"/></a:sp3d>
          </p:spPr></p:sp>
        </p:grpSp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)

    regions = extract_shape3d_regions(source)

    assert regions == [
        BevelRegion(
            slide_index=0,
            x=0.1,
            y=0.1,
            width=0.2,
            height=0.2,
            bevel_width_x=0.02,
            bevel_width_y=0.04,
            preset="roundRect",
            corner_radius_ratio=0.25,
        )
    ]


def test_extracts_the_drawingml_default_bevel_width_when_w_is_omitted(tmp_path):
    source = tmp_path / "default-bevel.pptx"
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="1000000" cy="500000"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:sp><p:spPr>
          <a:xfrm><a:off x="100000" y="50000"/><a:ext cx="400000" cy="300000"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT/></a:sp3d>
        </p:spPr></p:sp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)

    [region] = extract_shape3d_regions(source)

    assert region.bevel_width_x == pytest.approx(76200 / 1000000)
    assert region.bevel_width_y == pytest.approx(76200 / 500000)


def test_extracts_donut_adjustment_for_the_bevel_mask(tmp_path):
    source = tmp_path / "donut.pptx"
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="1000" cy="500"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:sp><p:spPr>
          <a:xfrm><a:off x="100" y="50"/><a:ext cx="400" cy="300"/></a:xfrm>
          <a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val 32000"/></a:avLst></a:prstGeom>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT w="10" h="8" prst="circle"/></a:sp3d>
        </p:spPr></p:sp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)

    [region] = extract_shape3d_regions(source)

    assert region.preset == "donut"
    assert region.geometry_adjustment == pytest.approx(0.32)
    assert region.geometry_inset_x_ratio == pytest.approx(0.24)
    assert region.geometry_inset_y_ratio == pytest.approx(0.32)


def test_skips_a_zero_thickness_donut_from_local_bevel_scoring(tmp_path):
    source = tmp_path / "zero-donut.pptx"
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="1000" cy="500"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:sp><p:spPr>
          <a:xfrm><a:off x="100" y="50"/><a:ext cx="400" cy="300"/></a:xfrm>
          <a:prstGeom prst="donut"><a:avLst><a:gd name="adj" fmla="val 0"/></a:avLst></a:prstGeom>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT w="10" h="8" prst="circle"/></a:sp3d>
        </p:spPr></p:sp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)

    assert extract_shape3d_regions(source) == []


def test_bevel_report_binds_the_exact_native_report_rasters(tmp_path):
    repo = tmp_path / "repo"
    case_id = "shape3d-case"
    source = repo / f"test/e2e/testdata/cases/{case_id}/source.pptx"
    source.parent.mkdir(parents=True)
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="300" cy="200"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:sp><p:spPr>
          <a:xfrm><a:off x="60" y="50"/><a:ext cx="180" cy="100"/></a:xfrm>
          <a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 20000"/></a:avLst></a:prstGeom>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT w="12" h="8" prst="circle"/></a:sp3d>
        </p:spPr></p:sp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)

    reference, _flat, _region = _bevel_specimen()
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True)
    reference_path = reports_dir / f"{case_id}_slide0_pdf.png"
    candidate_path = reports_dir / f"{case_id}_slide0_html.png"
    Image.fromarray(reference).save(reference_path)
    Image.fromarray(reference).save(candidate_path)
    artifact = lambda path: {
        "path": path.relative_to(repo).as_posix(),
        "sizeBytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    native = {
        "testFile": case_id,
        "provenance": {
            "renderer": {"revision": "a" * 40, "dirty": False},
            "inputs": {
                "sourcePptx": {
                    "path": source.relative_to(repo).as_posix(),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "groundTruth": {"combinedSha256": "f" * 64},
            },
        },
        "perSlide": [
            {
                "slideIdx": 0,
                "hidden": False,
                "renderArtifacts": {
                    "reference": artifact(reference_path),
                    "candidate": artifact(candidate_path),
                },
            }
        ],
    }
    native_path = repo / "native.json"
    native_path.write_text(json.dumps(native), encoding="utf-8")

    report = build_bevel_report([native_path], repo, reports_dir)

    slide_result = report["caseResults"][0]["slides"][0]
    assert slide_result["referencePath"] == native["perSlide"][0]["renderArtifacts"][
        "reference"
    ]["path"]
    assert slide_result["candidateSha256"] == native["perSlide"][0]["renderArtifacts"][
        "candidate"
    ]["sha256"]

    native["perSlide"][0]["renderArtifacts"]["candidate"]["sha256"] = "0" * 64
    native_path.write_text(json.dumps(native), encoding="utf-8")
    with pytest.raises(ValueError, match="native report artifact"):
        build_bevel_report([native_path], repo, reports_dir)


def test_bevel_report_enforces_declared_implicit_explicit_slide_equivalence(tmp_path):
    repo = tmp_path / "repo"
    case_id = "shape3d-default-bevel-pair"
    source = repo / f"test/e2e/testdata/cases/{case_id}/source.pptx"
    source.parent.mkdir(parents=True)
    presentation = """
      <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
        <p:sldSz cx="300" cy="200"/>
      </p:presentation>"""
    slide = """
      <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <p:cSld><p:spTree><p:sp><p:spPr>
          <a:xfrm><a:off x="60" y="50"/><a:ext cx="180" cy="100"/></a:xfrm>
          <a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 20000"/></a:avLst></a:prstGeom>
          <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          <a:sp3d><a:bevelT w="12" h="8" prst="circle"/></a:sp3d>
        </p:spPr></p:sp></p:spTree></p:cSld>
      </p:sld>"""
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/slide2.xml", slide)

    case_path = repo / f"test/e2e/oracle/cases-pypptx/{case_id}.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(
        json.dumps({"assertions": {"equivalentSlidePairs": [[0, 1]]}}),
        encoding="utf-8",
    )

    reference, flat, _region = _bevel_specimen()
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True)

    def write_artifacts(second_candidate: np.ndarray):
        for slide_index, candidate in ((0, reference), (1, second_candidate)):
            Image.fromarray(reference).save(reports_dir / f"{case_id}_slide{slide_index}_pdf.png")
            Image.fromarray(candidate).save(
                reports_dir / f"{case_id}_slide{slide_index}_html.png"
            )

    def artifact(path):
        return {
            "path": path.relative_to(repo).as_posix(),
            "sizeBytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def native_report():
        return {
            "testFile": case_id,
            "provenance": {
                "renderer": {"revision": "a" * 40, "dirty": False},
                "inputs": {
                    "sourcePptx": {
                        "path": source.relative_to(repo).as_posix(),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                    "groundTruth": {"combinedSha256": "f" * 64},
                },
            },
            "perSlide": [
                {
                    "slideIdx": slide_index,
                    "hidden": False,
                    "renderArtifacts": {
                        "reference": artifact(
                            reports_dir / f"{case_id}_slide{slide_index}_pdf.png"
                        ),
                        "candidate": artifact(
                            reports_dir / f"{case_id}_slide{slide_index}_html.png"
                        ),
                    },
                }
                for slide_index in range(2)
            ],
        }

    native_path = repo / "native.json"
    write_artifacts(reference)
    native_path.write_text(json.dumps(native_report()), encoding="utf-8")

    matched = build_bevel_report([native_path], repo, reports_dir)

    assert matched["schemaVersion"] == 8
    assert matched["caseResults"][0]["equivalencePairs"] == [
        {
            "leftSlideIdx": 0,
            "rightSlideIdx": 1,
            "referenceEqual": True,
            "candidateEqual": True,
            "passed": True,
        }
    ]
    assert matched["passed"] is True

    write_artifacts(flat)
    native_path.write_text(json.dumps(native_report()), encoding="utf-8")
    mismatched = build_bevel_report([native_path], repo, reports_dir)

    assert mismatched["caseResults"][0]["equivalencePairs"][0] == {
        "leftSlideIdx": 0,
        "rightSlideIdx": 1,
        "referenceEqual": True,
        "candidateEqual": False,
        "passed": False,
    }
    assert mismatched["passed"] is False
