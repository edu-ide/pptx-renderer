from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import numpy as np

from oracle.chart_metrics import (
    PLOT_BOUND_MAX_ERROR_RATIO,
    _region_metrics,
    compute_cartesian_chart_metrics,
    detect_cartesian_plot_bounds,
    extract_cartesian_chart_profiles,
)


def _vertical_chart(*, offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    image = np.full((360, 640, 3), 255, dtype=np.uint8)
    dx, dy = offset
    left, top, right, bottom = 120 + dx, 50 + dy, 520 + dx, 290 + dy
    for y in (top, 110 + dy, 170 + dy, 230 + dy, bottom):
        cv2.line(image, (left, y), (right, y), (180, 180, 180), 1)
    cv2.line(image, (left, top), (left, bottom), (80, 80, 80), 1)
    for index, height in enumerate((110, 175, 80)):
        x0 = 180 + dx + index * 90
        cv2.rectangle(
            image,
            (x0, bottom - height),
            (x0 + 48, bottom - 1),
            ((79, 129, 189), (192, 80, 77), (155, 187, 89))[index],
            -1,
        )
    return image


def _horizontal_chart(*, offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    image = np.full((360, 640, 3), 255, dtype=np.uint8)
    dx, dy = offset
    left, top, right, bottom = 120 + dx, 50 + dy, 520 + dx, 290 + dy
    for x in (left, 220 + dx, 320 + dx, 420 + dx, right):
        cv2.line(image, (x, top), (x, bottom), (180, 180, 180), 1)
    cv2.line(image, (left, bottom), (right, bottom), (80, 80, 80), 1)
    # Draw after the grid so the first bar deliberately obscures part of the y-axis.
    for index, width in enumerate((250, 175, 310)):
        y0 = 70 + dy + index * 65
        cv2.rectangle(
            image,
            (left, y0),
            (left + width, y0 + 34),
            ((79, 129, 189), (192, 80, 77), (155, 187, 89))[index],
            -1,
        )
    return image


def _profile(
    family: str,
    *,
    series_count: int = 3,
    chart_frame: dict[str, float] | None = None,
) -> dict[str, object]:
    profile: dict[str, object] = {
        "evaluable": True,
        "family": family,
        "orientation": "horizontal" if family == "bar" else "vertical",
        "grouping": "clustered",
        "legendPosition": "right",
        "seriesCount": series_count,
        "chartFrame": chart_frame
        or {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0},
    }
    return profile


def test_detects_vertical_plot_bounds_with_colored_series_over_grid():
    image = _vertical_chart()

    bounds = detect_cartesian_plot_bounds(image, "vertical")

    assert bounds is not None
    assert (bounds["left"], bounds["right"], bounds["bottom"]) == (120, 520, 290)
    assert abs(bounds["top"] - 50) <= 1


def test_detects_horizontal_plot_bounds_when_a_bar_obscures_the_y_axis():
    image = _horizontal_chart()

    bounds = detect_cartesian_plot_bounds(image, "horizontal")

    assert bounds is not None
    assert (bounds["left"], bounds["right"], bounds["bottom"]) == (120, 520, 290)
    assert abs(bounds["top"] - 50) <= 1


def test_axis_pair_without_parallel_grid_evidence_is_not_treated_as_a_plot():
    image = np.full((360, 640, 3), 255, dtype=np.uint8)
    cv2.line(image, (120, 50), (120, 290), (80, 80, 80), 1)
    cv2.line(image, (120, 290), (520, 290), (80, 80, 80), 1)
    cv2.rectangle(image, (180, 140), (230, 289), (79, 129, 189), -1)

    assert detect_cartesian_plot_bounds(image, "vertical") is None


def test_identical_chart_passes_and_proves_series_erasure_sensitivity():
    reference = _vertical_chart()

    result = compute_cartesian_chart_metrics(
        reference,
        reference.copy(),
        _profile("column"),
    )

    assert result["evaluable"] is True
    assert result["passed"] is True
    assert result["plotBounds"]["passed"] is True
    assert result["seriesInk"]["passed"] is True
    assert result["seriesSensitivity"]["detected"] is True


def test_shifted_plot_exceeding_the_tolerance_fails_the_local_gate():
    reference = _vertical_chart()
    candidate = _vertical_chart(offset=(8, 0))

    result = compute_cartesian_chart_metrics(reference, candidate, _profile("column"))

    assert result["plotBounds"]["maxErrorRatio"] > PLOT_BOUND_MAX_ERROR_RATIO
    assert result["plotBounds"]["passed"] is False
    assert result["passed"] is False


def test_erased_series_cannot_pass_on_plot_background_similarity():
    reference = _vertical_chart()
    candidate = reference.copy()
    hsv = cv2.cvtColor(candidate, cv2.COLOR_RGB2HSV)
    candidate[hsv[..., 1] >= 45] = 255

    result = compute_cartesian_chart_metrics(reference, candidate, _profile("column"))

    assert result["seriesInk"]["candidatePixelCount"] == 0
    assert result["seriesInk"]["passed"] is False
    assert result["seriesSensitivity"]["detected"] is False
    assert result["passed"] is False


def test_erased_series_cannot_hide_inside_a_solid_colored_plot_background():
    background = (146, 205, 231)
    reference = _vertical_chart()
    reference[50:291, 120:521] = background
    for y in (50, 110, 170, 230, 290):
        cv2.line(reference, (120, y), (520, y), (180, 180, 180), 1)
    cv2.line(reference, (120, 50), (120, 290), (80, 80, 80), 1)
    for index, height in enumerate((110, 175, 80)):
        x0 = 180 + index * 90
        cv2.rectangle(
            reference,
            (x0, 290 - height),
            (x0 + 48, 289),
            ((192, 80, 77), (155, 187, 89), (128, 100, 162))[index],
            -1,
        )
    candidate = reference.copy()
    for index, height in enumerate((110, 175, 80)):
        x0 = 180 + index * 90
        cv2.rectangle(candidate, (x0, 290 - height), (x0 + 48, 289), background, -1)

    result = compute_cartesian_chart_metrics(reference, candidate, _profile("column"))

    assert not (result["evaluable"] and result["passed"])


def test_many_series_are_out_of_scope_for_the_aggregate_ink_gate():
    reference = _vertical_chart()

    result = compute_cartesian_chart_metrics(
        reference,
        reference.copy(),
        _profile("column", series_count=24),
    )

    assert result == {
        "evaluable": False,
        "reason": "series-count-out-of-scope",
        "seriesCount": 24,
    }


def test_missing_chart_frame_is_not_allowed_to_fall_back_to_the_whole_slide():
    reference = _vertical_chart()
    profile = _profile("column")
    profile.pop("chartFrame")

    result = compute_cartesian_chart_metrics(reference, reference.copy(), profile)

    assert result == {
        "evaluable": False,
        "reason": "chart-frame-unresolved",
    }


def test_chart_frame_excludes_an_unrelated_decorative_grid():
    reference = np.full((360, 640, 3), 255, dtype=np.uint8)
    candidate = reference.copy()
    for image, decorative_left in ((reference, 20), (candidate, 32)):
        for x in range(decorative_left, decorative_left + 221, 55):
            cv2.line(image, (x, 50), (x, 310), (150, 150, 150), 1)
        for y in range(50, 311, 52):
            cv2.line(
                image,
                (decorative_left, y),
                (decorative_left + 220, y),
                (150, 150, 150),
                1,
            )
        for y in (60, 120, 180, 240, 300):
            cv2.line(image, (280, y), (580, y), (180, 180, 180), 1)
        cv2.line(image, (280, 60), (280, 300), (80, 80, 80), 1)
        for index, height in enumerate((100, 160, 70)):
            x0 = 335 + index * 70
            cv2.rectangle(
                image,
                (x0, 300 - height),
                (x0 + 38, 299),
                ((79, 129, 189), (192, 80, 77), (155, 187, 89))[index],
                -1,
            )
    chart_frame = {
        "left": 260 / 640,
        "top": 30 / 360,
        "right": 610 / 640,
        "bottom": 330 / 360,
    }

    result = compute_cartesian_chart_metrics(
        reference,
        candidate,
        _profile("column", chart_frame=chart_frame),
    )

    assert result["passed"] is True
    assert result["plotBounds"]["reference"]["left"] == 280
    assert result["plotBounds"]["candidate"]["left"] == 280


def test_horizontal_chart_regions_assign_left_to_category_axis():
    reference = _horizontal_chart()
    candidate = reference.copy()
    candidate[100:180, 70:105] = 0
    plot = {"left": 120, "top": 50, "right": 520, "bottom": 290}

    regions = _region_metrics(reference, candidate, plot, "right", "horizontal")

    assert regions["categoryAxisSsim"] < 1.0
    assert regions["valueAxisSsim"] == 1.0


def test_recoloring_distinct_series_to_one_color_fails_the_local_gate():
    reference = _vertical_chart()
    candidate = reference.copy()
    hsv = cv2.cvtColor(candidate, cv2.COLOR_RGB2HSV)
    candidate[hsv[..., 1] >= 45] = (79, 129, 189)

    result = compute_cartesian_chart_metrics(reference, candidate, _profile("column"))

    assert result["plotBounds"]["passed"] is True
    assert result["seriesInk"]["fgIouTolerant"] == 1.0
    assert (
        result["seriesInk"]["meanLabDistance"]
        > result["seriesInk"]["thresholds"]["meanLabDistance"]
    )
    assert result["seriesInk"]["passed"] is False
    assert result["passed"] is False


def test_neutral_only_series_is_explicitly_not_evaluable():
    reference = _vertical_chart()
    hsv = cv2.cvtColor(reference, cv2.COLOR_RGB2HSV)
    reference[hsv[..., 1] >= 45] = (120, 120, 120)

    result = compute_cartesian_chart_metrics(
        reference,
        reference.copy(),
        _profile("column"),
    )

    assert result["evaluable"] is False
    assert result["reason"] == "reference-series-ink-not-detectable"


def _write_chart_package(
    path: Path,
    chart_xml: str,
    *,
    grouped: bool = False,
    include_frame: bool = True,
) -> None:
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    transform = (
        '<p:xfrm><a:off x="914400" y="514350"/>'
        '<a:ext cx="7315200" cy="4114800"/></p:xfrm>'
        if include_frame
        else ""
    )
    graphic_frame = (
        f'<p:graphicFrame>{transform}<a:graphic><a:graphicData>'
        '<c:chart r:id="rId2"/></a:graphicData></a:graphic></p:graphicFrame>'
    )
    if grouped:
        graphic_frame = (
            '<p:grpSp><p:grpSpPr><a:xfrm><a:off x="457200" y="257175"/>'
            '<a:ext cx="8229600" cy="4629150"/><a:chOff x="0" y="0"/>'
            '<a:chExt cx="9144000" cy="5143500"/></a:xfrm></p:grpSpPr>'
            f'{graphic_frame}</p:grpSp>'
        )
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{p_ns}" xmlns:a="{a_ns}" xmlns:r="{r_ns}">'
            '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            '<p:sldSz cx="9144000" cy="5143500"/></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId1" '
            f'Type="{r_ns}/slide" Target="slides/slide1.xml"/></Relationships>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}" xmlns:c="{c_ns}" '
            f'xmlns:r="{r_ns}"><p:cSld><p:spTree>{graphic_frame}'
            "</p:spTree></p:cSld></p:sld>",
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId2" '
            f'Type="{r_ns}/chart" Target="../charts/chart1.xml"/></Relationships>',
        )
        archive.writestr("ppt/charts/chart1.xml", chart_xml)


def test_extracts_single_horizontal_bar_profile_through_slide_relationship(tmp_path: Path):
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    source = tmp_path / "source.pptx"
    _write_chart_package(
        source,
        f'<c:chartSpace xmlns:c="{c_ns}"><c:chart><c:plotArea><c:barChart>'
        '<c:barDir val="bar"/><c:grouping val="stacked"/>'
        '<c:ser/><c:ser/></c:barChart></c:plotArea>'
        '<c:legend><c:legendPos val="r"/></c:legend></c:chart></c:chartSpace>',
    )

    assert extract_cartesian_chart_profiles(source) == {
        0: {
            "evaluable": True,
            "family": "bar",
            "orientation": "horizontal",
            "grouping": "stacked",
            "legendPosition": "right",
            "seriesCount": 2,
            "chartPath": "ppt/charts/chart1.xml",
            "chartFrame": {
                "left": 0.1,
                "top": 0.1,
                "right": 0.9,
                "bottom": 0.9,
            },
        }
    }


def test_combo_chart_is_explicitly_not_evaluable(tmp_path: Path):
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    source = tmp_path / "source.pptx"
    _write_chart_package(
        source,
        f'<c:chartSpace xmlns:c="{c_ns}"><c:chart><c:plotArea>'
        '<c:barChart><c:barDir val="col"/><c:ser/></c:barChart>'
        '<c:lineChart><c:ser/></c:lineChart></c:plotArea></c:chart></c:chartSpace>',
    )

    assert extract_cartesian_chart_profiles(source)[0] == {
        "evaluable": False,
        "reason": "combo-chart",
        "chartPath": "ppt/charts/chart1.xml",
    }


def test_grouped_chart_frame_is_explicitly_out_of_scope(tmp_path: Path):
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    source = tmp_path / "source.pptx"
    _write_chart_package(
        source,
        f'<c:chartSpace xmlns:c="{c_ns}"><c:chart><c:plotArea><c:lineChart>'
        '<c:ser/></c:lineChart></c:plotArea></c:chart></c:chartSpace>',
        grouped=True,
    )

    profile = extract_cartesian_chart_profiles(source)[0]

    assert profile["evaluable"] is False
    assert profile["reason"] == "grouped-chart-frame-unsupported"
    assert profile["chartPath"] == "ppt/charts/chart1.xml"


def test_missing_graphic_frame_transform_is_explicitly_not_evaluable(tmp_path: Path):
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    source = tmp_path / "source.pptx"
    _write_chart_package(
        source,
        f'<c:chartSpace xmlns:c="{c_ns}"><c:chart><c:plotArea><c:lineChart>'
        '<c:ser/></c:lineChart></c:plotArea></c:chart></c:chartSpace>',
        include_frame=False,
    )

    profile = extract_cartesian_chart_profiles(source)[0]

    assert profile["evaluable"] is False
    assert profile["reason"] == "chart-frame-unresolved"


def test_slide_profiles_follow_presentation_order_instead_of_part_number(tmp_path: Path):
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    source = tmp_path / "source.pptx"
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{p_ns}" xmlns:a="{a_ns}" xmlns:r="{r_ns}">'
            '<p:sldIdLst><p:sldId id="512" r:id="rId2"/>'
            '<p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            '<p:sldSz cx="9144000" cy="5143500"/></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{rel_ns}">'
            f'<Relationship Id="rId1" Type="{r_ns}/slide" Target="slides/slide1.xml"/>'
            f'<Relationship Id="rId2" Type="{r_ns}/slide" Target="slides/slide2.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p_ns}"/>',
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}" xmlns:c="{c_ns}" '
            f'xmlns:r="{r_ns}"><p:cSld><p:spTree><p:graphicFrame><p:xfrm>'
            '<a:off x="914400" y="514350"/><a:ext cx="7315200" cy="4114800"/>'
            '</p:xfrm><a:graphic><a:graphicData><c:chart r:id="rId2"/>'
            "</a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>",
        )
        archive.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId2" '
            f'Type="{r_ns}/chart" Target="../charts/chart1.xml"/></Relationships>',
        )
        archive.writestr(
            "ppt/charts/chart1.xml",
            f'<c:chartSpace xmlns:c="{c_ns}"><c:chart><c:plotArea><c:lineChart>'
            '<c:ser/></c:lineChart></c:plotArea></c:chart></c:chartSpace>',
        )

    profiles = extract_cartesian_chart_profiles(source)

    assert set(profiles) == {0}
    assert profiles[0]["chartPath"] == "ppt/charts/chart1.xml"
    assert profiles[0]["chartFrame"] == {
        "left": 0.1,
        "top": 0.1,
        "right": 0.9,
        "bottom": 0.9,
    }
