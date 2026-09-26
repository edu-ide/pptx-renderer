from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import cv2
import numpy as np
from PIL import Image

from scripts.reflection_metrics import (
    ReflectionRegion,
    _erase_reflection_regions,
    build_reflection_report,
    compute_reflection_metrics,
    extract_shape_reflection_specs,
)


def _reflection_specimen(
    *,
    reflection: bool = True,
    offset_x: int = 0,
) -> tuple[np.ndarray, tuple[ReflectionRegion, ...]]:
    height, width = 300, 420
    source = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(source, (110, 55), (310, 135), 255, -1)
    region = ReflectionRegion(90 / width, 138 / height, 330 / width, 235 / height)
    image = np.full((height, width, 3), 255, dtype=np.float64)
    if reflection:
        reflected = cv2.flip(source, 0)
        transform = np.asarray([[1, 0, offset_x], [0, 1, -25]], dtype=np.float32)
        reflected = cv2.warpAffine(reflected, transform, (width, height))
        reflected = cv2.GaussianBlur(reflected, (0, 0), sigmaX=2.0, sigmaY=2.0)
        alpha = np.linspace(0.42, 0.0, height, dtype=np.float64)[:, None]
        paint = np.asarray((58, 123, 213), dtype=np.float64)
        coverage = reflected.astype(np.float64) / 255 * alpha
        image = image * (1 - coverage[..., None]) + paint * coverage[..., None]
    image[source > 0] = np.asarray((58, 123, 213), dtype=np.float64)
    return np.rint(image).clip(0, 255).astype(np.uint8), (region,)


def test_reflection_metrics_pass_identical_reflection_and_detect_erasure():
    reference, regions = _reflection_specimen()

    result = compute_reflection_metrics(reference, reference.copy(), required=True, regions=regions)
    erased = compute_reflection_metrics(
        reference,
        _erase_reflection_regions(reference.copy(), regions),
        required=True,
        regions=regions,
    )

    assert result["reflectionMeasurable"] is True
    assert result["passed"] is True
    assert erased["passed"] is False
    assert result["reflectionSensitivity"]["detected"] is True


def test_reflection_metrics_reject_displaced_reflection_field():
    reference, regions = _reflection_specimen()
    candidate, _ = _reflection_specimen(offset_x=42)

    result = compute_reflection_metrics(reference, candidate, required=True, regions=regions)

    assert result["passed"] is False
    assert result["reflectionFieldIou"] < result["thresholds"]["reflectionFieldIou"]


def test_reflection_metrics_reject_invented_control_reflection():
    reference, regions = _reflection_specimen(reflection=False)
    candidate, _ = _reflection_specimen(reflection=True)

    result = compute_reflection_metrics(reference, candidate, required=False, regions=regions)

    assert result["reflectionMeasurable"] is False
    assert result["candidateReflectionDensity"] > result["thresholds"]["invisibleReflectionDensity"]
    assert result["passed"] is False


def _write_reflection_source(path: Path, *, include_extra_slides: bool = True) -> None:
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            f'<p:presentation xmlns:p="{p_ns}"><p:sldSz cx="1000" cy="500"/></p:presentation>',
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:cSld><p:spTree>'
            '<p:sp><p:spPr><a:xfrm><a:off x="100" y="50"/><a:ext cx="300" cy="100"/>'
            '</a:xfrm><a:effectLst><a:reflection dir="5400000" sy="-100000" algn="bl"/>'
            '</a:effectLst></p:spPr></p:sp>'
            '</p:spTree></p:cSld></p:sld>',
        )
        if include_extra_slides:
            archive.writestr(
                "ppt/slides/slide2.xml",
                f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:cSld><p:spTree>'
                '<p:pic><p:spPr><a:xfrm><a:off x="100" y="50"/><a:ext cx="300" cy="100"/>'
                '</a:xfrm><a:effectLst><a:reflection/></a:effectLst></p:spPr></p:pic>'
                '</p:spTree></p:cSld></p:sld>',
            )
            archive.writestr(
                "ppt/slides/slide3.xml",
                f'<p:sld xmlns:p="{p_ns}" xmlns:a="{a_ns}"><p:cSld><p:spTree><p:grpSp>'
                '<p:grpSpPr><a:xfrm><a:off x="200" y="50"/><a:ext cx="400" cy="200"/>'
                '<a:chOff x="0" y="0"/><a:chExt cx="200" cy="100"/></a:xfrm></p:grpSpPr>'
                '<p:sp><p:spPr><a:xfrm><a:off x="20" y="10"/><a:ext cx="80" cy="30"/>'
                '</a:xfrm><a:effectLst><a:reflection dir="5400000" sy="-100000" algn="bl"/>'
                '</a:effectLst></p:spPr></p:sp>'
                '</p:grpSp></p:spTree></p:cSld></p:sld>',
            )


def test_extract_shape_reflection_specs_uses_exact_path_and_group_transform(tmp_path: Path):
    source = tmp_path / "source.pptx"
    _write_reflection_source(source)

    specs = extract_shape_reflection_specs(source)

    assert set(specs) == {0, 2}
    direct = specs[0].reflection_regions[0]
    assert direct == ReflectionRegion(0.1, 0.3, 0.4, 0.5)
    grouped = specs[2].reflection_regions[0]
    assert grouped == ReflectionRegion(0.24, 0.26, 0.4, 0.38)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_reflection_report_binds_case_provenance_and_rasters(tmp_path: Path):
    repo = tmp_path / "repo"
    reports = repo / "test/e2e/reports"
    case_dir = repo / "test/e2e/testdata/cases/reflection-case"
    reports.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    source = case_dir / "source.pptx"
    _write_reflection_source(source, include_extra_slides=False)

    image, _ = _reflection_specimen()
    reference = reports / "reflection-case_slide0_pdf.png"
    candidate = reports / "reflection-case_slide0_html.png"
    Image.fromarray(image).save(reference)
    Image.fromarray(image).save(candidate)
    report_path = repo / "case-report.json"
    report_path.write_text(
        json.dumps(
            {
                "testFile": "reflection-case",
                "provenance": {
                    "renderer": {"revision": "a" * 40, "dirty": False},
                    "inputs": {
                        "sourcePptx": {
                            "path": "test/e2e/testdata/cases/reflection-case/source.pptx",
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
                                "path": "test/e2e/reports/reflection-case_slide0_pdf.png",
                                "sizeBytes": reference.stat().st_size,
                                "sha256": _sha256(reference),
                            },
                            "candidate": {
                                "path": "test/e2e/reports/reflection-case_slide0_html.png",
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

    report = build_reflection_report([report_path], repo, reports)

    assert report["schemaVersion"] == 1
    assert report["renderer"] == {"revision": "a" * 40, "dirty": False}
    assert report["applicableCaseCount"] == 1
    assert report["caseResults"][0]["sourceSha256"] == _sha256(source)
    assert report["caseResults"][0]["slides"][0]["referenceSha256"] == _sha256(reference)
    assert report["passed"] is True
