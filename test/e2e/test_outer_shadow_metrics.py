from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import numpy as np
from PIL import Image

from scripts.outer_shadow_metrics import (
    _erase_exterior_shadow,
    build_outer_shadow_report,
    compute_outer_shadow_metrics,
    extract_outer_shadow_slide_indices,
)


def _shadow_specimen(
    *,
    scale: float = 1.0,
    offset: tuple[int, int] = (10, 8),
    sigma: float = 6.0,
    opacity: float = 0.35,
    shadow: bool = True,
) -> np.ndarray:
    height, width = 300, 420
    shape_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(shape_mask, (110, 80), (310, 220), 255, -1)
    shadow_mask = np.zeros_like(shape_mask)
    if shadow:
        center_x, center_y = 210, 150
        transform = np.asarray(
            [
                [scale, 0, center_x * (1 - scale) + offset[0]],
                [0, scale, center_y * (1 - scale) + offset[1]],
            ],
            dtype=np.float32,
        )
        shadow_mask = cv2.warpAffine(
            shape_mask,
            transform,
            (width, height),
            flags=cv2.INTER_LINEAR,
        )
        shadow_mask = cv2.GaussianBlur(shadow_mask, (0, 0), sigmaX=sigma, sigmaY=sigma)

    image = np.full((height, width, 3), 255, dtype=np.float64)
    image -= shadow_mask[..., None].astype(np.float64) / 255 * (255 * opacity)
    image[shape_mask > 0] = np.asarray((58, 123, 213), dtype=np.float64)
    return np.rint(image).clip(0, 255).astype(np.uint8)


def test_outer_shadow_metrics_pass_identical_shadow_and_detect_erasure():
    reference = _shadow_specimen(scale=1.08, offset=(12, 9))

    result = compute_outer_shadow_metrics(reference, reference.copy(), required=True)
    erased = compute_outer_shadow_metrics(
        reference,
        _erase_exterior_shadow(reference.copy()),
        required=True,
    )

    assert result["shadowMeasurable"] is True
    assert result["passed"] is True
    assert erased["passed"] is False
    assert result["shadowSensitivity"]["detected"] is True


def test_outer_shadow_metrics_reject_wrong_scaled_silhouette():
    reference = _shadow_specimen(scale=1.14, offset=(8, 6))
    candidate = _shadow_specimen(scale=1.0, offset=(8, 6))

    result = compute_outer_shadow_metrics(reference, candidate, required=True)

    assert result["passed"] is False
    assert result["shadowFieldIou"] < result["thresholds"]["shadowFieldIou"]


def test_outer_shadow_metrics_caps_invented_shadow_when_native_is_not_measurable():
    reference = _shadow_specimen(shadow=False)
    candidate = _shadow_specimen(scale=0.92, offset=(18, 18), sigma=10, opacity=0.5)

    result = compute_outer_shadow_metrics(reference, candidate, required=True)

    assert result["shadowMeasurable"] is False
    assert result["candidateShadowDensity"] > result["thresholds"]["invisibleShadowDensity"]
    assert result["passed"] is False


def test_extract_outer_shadow_slide_indices_uses_exact_shape_path(tmp_path: Path):
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    source = tmp_path / "source.pptx"
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:cSld/></p:sld>',
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:sp><p:spPr>'
            '<a:effectLst><a:outerShdw/></a:effectLst>'
            "</p:spPr></p:sp></p:sld>",
        )
        archive.writestr(
            "ppt/slides/slide3.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:pic><p:spPr>'
            '<a:effectLst><a:outerShdw/></a:effectLst>'
            "</p:spPr></p:pic></p:sld>",
        )

    assert extract_outer_shadow_slide_indices(source) == {1}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_outer_shadow_report_binds_case_provenance_and_rasters(tmp_path: Path):
    repo = tmp_path / "repo"
    reports = repo / "test/e2e/reports"
    case_dir = repo / "test/e2e/testdata/cases/shadow-case"
    reports.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    source = case_dir / "source.pptx"
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:sp><p:spPr>'
            '<a:effectLst><a:outerShdw blurRad="127000"/></a:effectLst>'
            "</p:spPr></p:sp></p:sld>",
        )

    image = _shadow_specimen()
    reference = reports / "shadow-case_slide0_pdf.png"
    candidate = reports / "shadow-case_slide0_html.png"
    Image.fromarray(image).save(reference)
    Image.fromarray(image).save(candidate)
    report_path = repo / "case-report.json"
    report_path.write_text(
        json.dumps(
            {
                "testFile": "shadow-case",
                "provenance": {
                    "renderer": {"revision": "a" * 40, "dirty": False},
                    "inputs": {
                        "sourcePptx": {
                            "path": "test/e2e/testdata/cases/shadow-case/source.pptx",
                            "sha256": _sha256(source),
                        },
                        "groundTruth": {"combinedSha256": "b" * 64},
                    },
                },
                "perSlide": [
                    {
                        "slideIdx": 0,
                        "hidden": False,
                        "renderArtifacts": {
                            "reference": {
                                "path": "test/e2e/reports/shadow-case_slide0_pdf.png",
                                "sizeBytes": reference.stat().st_size,
                                "sha256": _sha256(reference),
                            },
                            "candidate": {
                                "path": "test/e2e/reports/shadow-case_slide0_html.png",
                                "sizeBytes": candidate.stat().st_size,
                                "sha256": _sha256(candidate),
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = build_outer_shadow_report([report_path], repo, reports)

    assert report["schemaVersion"] == 1
    assert report["renderer"] == {"revision": "a" * 40, "dirty": False}
    assert report["applicableCaseCount"] == 1
    assert report["caseResults"][0]["sourceSha256"] == _sha256(source)
    assert report["caseResults"][0]["slides"][0]["referenceSha256"] == _sha256(reference)
    assert report["passed"] is True
