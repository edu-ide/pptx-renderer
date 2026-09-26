import hashlib
import json
from dataclasses import replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from oracle.capability_contract import load_capability_registry
from oracle.capability_verification import (
    CapabilityVerificationError,
    normalize_native_evaluation_reports,
)


def capability_fixture(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/renderer.ts").write_text("renderer", encoding="utf-8")
    registry_path = repo / "capabilities.json"
    registry_path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "capabilities": [
                    {
                        "id": "drawingml.shape.geometry.adjustment.donut",
                        "component": "shape",
                        "renderMode": "native",
                        "impact": "fidelity",
                        "selectors": [],
                        "scope": {"presets": ["donut"]},
                        "fallback": "Use the handwritten geometry.",
                        "affectedPaths": ["src/renderer.ts"],
                        "requiredGates": [
                            "source",
                            "structural",
                            "unit",
                            "browser",
                            "native-powerpoint",
                            "manual-visual",
                            "regression",
                            "docs",
                        ],
                        "issueUrls": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    capability = load_capability_registry(registry_path).capabilities[0]
    return repo, capability


def native_report(
    case_id: str,
    *,
    revision: str = "a" * 40,
    dirty: bool = False,
    ssim: float = 0.99,
    needs_review: bool = False,
) -> dict:
    suffix = "1" if case_id.endswith("thin") else "2"
    return {
        "testFile": case_id,
        "evaluationErrorCount": 0,
        "evaluationErrors": [],
        "oracleMismatchCount": 0,
        "avgSsim": ssim,
        "supported": True,
        "quality": {"passed": True, "needsReview": needs_review},
        "provenance": {
            "inputs": {
                "sourcePptx": {"sha256": suffix * 64},
                "groundTruth": {"kind": "png", "combinedSha256": ("f" + suffix) * 32},
            },
            "renderer": {"revision": revision, "dirty": dirty},
            "runtime": {
                "platform": "macOS-test",
                "python": "3.11.11",
                "browser": {"name": "chrome", "version": "152"},
                "fontProfile": None,
            },
        },
    }


def bevel_report(case: dict, repo: Path, *, passed: bool = True) -> dict:
    case_id = case["testFile"]
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reports_dir / f"{case_id}_pdf.png"
    candidate_path = reports_dir / f"{case_id}_html.png"
    reference_path.write_bytes(b"native-reference")
    candidate_path.write_bytes(b"renderer-candidate")
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    native_artifacts = {
        "reference": {
            "path": reference_path.relative_to(repo).as_posix(),
            "sizeBytes": reference_path.stat().st_size,
            "sha256": reference_hash,
        },
        "candidate": {
            "path": candidate_path.relative_to(repo).as_posix(),
            "sizeBytes": candidate_path.stat().st_size,
            "sha256": candidate_hash,
        },
    }
    case["perSlide"] = [
        {"slideIdx": 0, "hidden": False, "renderArtifacts": native_artifacts}
    ]
    return {
        "schemaVersion": 8,
        "renderer": dict(case["provenance"]["renderer"]),
        "thresholds": {
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
        "applicableCaseCount": 1,
        "passed": passed,
        "caseResults": [
            {
                "caseId": case_id,
                "sourceSha256": case["provenance"]["inputs"]["sourcePptx"]["sha256"],
                "groundTruthSha256": case["provenance"]["inputs"]["groundTruth"][
                    "combinedSha256"
                ],
                "applicable": True,
                "passed": passed,
                "equivalencePairs": [],
                "slides": [
                    {
                        "slideIdx": 0,
                        "referencePath": native_artifacts["reference"]["path"],
                        "candidatePath": native_artifacts["candidate"]["path"],
                        "referenceSha256": reference_hash,
                        "candidateSha256": candidate_hash,
                        "passed": passed,
                        "regions": [
                            {
                                "region": {"preset": "roundRect", "surface": "shape"},
                                "metrics": {
                                    "evaluable": True,
                                    "score": 0.9 if passed else 0.4,
                                    "cornerScore": 0.9 if passed else 0.4,
                                    "cornerRequired": True,
                                    "rangeRatio": 1.0,
                                    "referenceDynamicRange": 80.0,
                                    "candidateDynamicRange": 80.0,
                                    "highlightAmplitudeRatio": 1.0,
                                    "referenceHighlightAmplitude": 35.0,
                                    "candidateHighlightAmplitude": 35.0,
                                    "shadowAmplitudeRatio": 1.0,
                                    "shadowOvershootRatio": 1.0,
                                    "referenceShadowAmplitude": 45.0,
                                    "candidateShadowAmplitude": 45.0,
                                    "referenceShadowEnergy": 12.0,
                                    "candidateShadowEnergy": 12.0,
                                    "shadowEnergyOvershootRatio": 1.0,
                                    "shadowLocalExcessRatio": 0.0,
                                    "shadowSectorOvershootRatio": 1.0,
                                    "shadowSectors": [],
                                    "thresholds": {
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
                                    "passed": passed,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def camera_report(
    case: dict,
    repo: Path,
    *,
    passed: bool = True,
    modality: str = "plane",
    schema_version: int = 7,
) -> dict:
    case_id = case["testFile"]
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reports_dir / f"{case_id}_pdf.png"
    candidate_path = reports_dir / f"{case_id}_html.png"
    reference_path.write_bytes(b"native-camera-reference")
    candidate_path.write_bytes(b"renderer-camera-candidate")
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    artifacts = {
        "reference": {
            "path": reference_path.relative_to(repo).as_posix(),
            "sizeBytes": reference_path.stat().st_size,
            "sha256": reference_hash,
        },
        "candidate": {
            "path": candidate_path.relative_to(repo).as_posix(),
            "sizeBytes": candidate_path.stat().st_size,
            "sha256": candidate_hash,
        },
    }
    case["perSlide"] = [{"slideIdx": 0, "hidden": False, "renderArtifacts": artifacts}]
    plane_thresholds = {
        "cornerScore": 0.98,
        "colorScore": 0.97,
        "gradientRangeRatio": 0.65,
        "gradientDirection": 0.95,
        "minimumReferenceGradientRange": 4.0,
        "shadowRingInnerRatio": 0.0018,
        "shadowRingOuterRatio": 0.016,
        "shadowBackgroundLevel": 252.0,
        "minimumReferenceShadowDensity": 0.25,
        "shadowEnergyRatio": 0.70,
        "shadowDirectionCosine": 0.95,
    }
    text_thresholds = {
        "rasterToleranceRatio": 0.0025,
        "tolerantForegroundF1": 0.90,
        "tolerantBoundsScore": 0.98,
        "inkCoverageRatio": 0.90,
    }
    picture_thresholds = {
        "cornerScore": 0.98,
        "rectifiedColorScore": 0.95,
        "rectifiedEdgeF1": 0.90,
        "rectifiedSize": 384,
        "edgeToleranceRatio": 0.008,
        "cropMutationRatio": 0.12,
    }
    bottom_material_thresholds = {
        "cornerScore": 0.98,
        "meanBandColorError": 1.0,
        "sourceFlatFill": [68, 114, 196],
    }
    custom_geometry_thresholds = {
        "rasterToleranceRatio": 0.0025,
        "tolerantForegroundF1": 0.95,
        "tolerantBoundsScore": 0.98,
        "foregroundAreaRatio": 0.90,
        "centroidScore": 0.99,
        "colorScore": 0.98,
        "verticalSquashRatio": 0.20,
    }
    thresholds = {
        "plane": plane_thresholds,
        "text": text_thresholds,
        "picture": picture_thresholds,
        "bottom-material": bottom_material_thresholds,
        "custom-geometry": custom_geometry_thresholds,
    }
    if schema_version >= 7:
        thresholds["picture-group"] = picture_thresholds
    if modality == "plane":
        metrics = {
            "evaluable": True,
            "cornerScore": 0.995 if passed else 0.9,
            "meanCornerErrorRatio": 0.005 if passed else 0.1,
            "colorScore": 0.99,
            "gradientRequired": True,
            "referenceGradientRange": 20.0,
            "candidateGradientRange": 18.0,
            "gradientRangeRatio": 0.9,
            "gradientDirection": 1.0,
            "referenceBands": [[1, 2, 3]] * 3,
            "candidateBands": [[1, 2, 3]] * 3,
            "shadowRequired": True,
            "shadowMeasurable": True,
            "referenceShadowDensity": 2.0,
            "candidateShadowDensity": 1.6 if passed else 0.1,
            "shadowEnergyRatio": 0.8 if passed else 0.05,
            "shadowDirectionCosine": 1.0,
            "referenceShadowRingPixels": 500,
            "candidateShadowRingPixels": 500,
            "shadowRingInnerPx": 3,
            "shadowRingOuterPx": 24,
            "shadowPassed": passed,
            "shadowSensitivity": {
                "mutation": "erase-exterior-shadow",
                "applicable": True,
                "mutatedCandidateShadowDensity": 0.0,
                "mutatedShadowEnergyRatio": 0.0,
                "mutatedShadowDirectionCosine": 0.0,
                "mutatedShadowPassed": False,
                "detected": True,
            },
            "thresholds": plane_thresholds,
            "passed": passed,
        }
    elif modality == "bottom-material":
        metrics = {
            "evaluable": True,
            "cornerScore": 0.995 if passed else 0.9,
            "meanCornerErrorRatio": 0.005 if passed else 0.1,
            "referenceBands": [[70, 118, 203]] * 3,
            "candidateBands": ([[70, 118, 203]] * 3 if passed else [[68, 114, 196]] * 3),
            "meanBandColorError": 0.0 if passed else 13 / 3,
            "flatFillSensitivity": {
                "mutation": "restore-source-flat-fill",
                "sourceFlatFill": [68, 114, 196],
                "mutatedBands": [[68, 114, 196]] * 3,
                "mutatedMeanBandColorError": 13 / 3,
                "mutatedPassed": False,
                "detected": True,
            },
            "thresholds": bottom_material_thresholds,
            "passed": passed,
        }
    elif modality == "text":
        metrics = {
            "evaluable": True,
            "foregroundIou": 0.4,
            "boundsScore": 0.995,
            "meanBoundsErrorRatio": 0.005,
            "rasterTolerancePx": 3,
            "referenceCoverageAtTolerance": 0.95 if passed else 0.4,
            "candidateCoverageAtTolerance": 0.95 if passed else 0.4,
            "tolerantForegroundF1": 0.95 if passed else 0.4,
            "tolerantBoundsScore": 0.998,
            "tolerantMeanBoundsErrorRatio": 0.002,
            "inkCoverageRatio": 0.96,
            "referenceInkDensity": 1.5,
            "candidateInkDensity": 1.45,
            "referenceBounds": [10, 20, 100, 80],
            "candidateBounds": [10, 20, 100, 80],
            "thresholds": text_thresholds,
            "passed": passed,
        }
    elif modality == "custom-geometry":
        reference_coverage = 0.98 if passed else 0.5
        candidate_coverage = 0.97 if passed else 0.5
        bounds_error = 0.005 if passed else 0.05
        reference_pixels = 1000
        candidate_pixels = 980 if passed else 500
        centroid_error = 0.005 if passed else 0.05
        metrics = {
            "evaluable": True,
            "rasterTolerancePx": 3,
            "referenceCoverageAtTolerance": reference_coverage,
            "candidateCoverageAtTolerance": candidate_coverage,
            "tolerantForegroundF1": (
                2
                * reference_coverage
                * candidate_coverage
                / (reference_coverage + candidate_coverage)
            ),
            "tolerantBoundsScore": 1 - bounds_error,
            "tolerantMeanBoundsErrorRatio": bounds_error,
            "foregroundAreaRatio": min(reference_pixels, candidate_pixels)
            / max(reference_pixels, candidate_pixels),
            "referenceForegroundPixels": reference_pixels,
            "candidateForegroundPixels": candidate_pixels,
            "centroidScore": 1 - centroid_error,
            "centroidErrorRatio": centroid_error,
            "colorScore": 1.0,
            "meanColorError": 0.0,
            "referenceColor": [71.0, 144.0, 210.0],
            "candidateColor": [71.0, 144.0, 210.0],
            "referenceBounds": [10, 20, 100, 80],
            "candidateBounds": [10, 20, 100, 80],
            "squashSensitivity": {
                "mutation": "vertical-squash",
                "ratio": 0.20,
                "mutatedTolerantForegroundF1": 0.4,
                "mutatedTolerantBoundsScore": 0.7,
                "mutatedForegroundAreaRatio": 0.2,
                "mutatedCentroidScore": 0.995,
                "mutatedColorScore": 1.0,
                "mutatedPassed": False,
                "detected": True,
            },
            "thresholds": custom_geometry_thresholds,
            "passed": passed,
        }
    else:
        reference_edge_coverage = 0.96
        candidate_edge_coverage = 0.94
        mutated_reference_edge_coverage = 0.52
        mutated_candidate_edge_coverage = 0.61
        metrics = {
            "evaluable": True,
            "cornerScore": 0.995 if passed else 0.9,
            "meanCornerErrorRatio": 0.005 if passed else 0.1,
            "rectifiedColorScore": 0.97,
            "rectifiedEdgeF1": (
                2
                * reference_edge_coverage
                * candidate_edge_coverage
                / (reference_edge_coverage + candidate_edge_coverage)
            ),
            "referenceEdgeCoverageAtTolerance": reference_edge_coverage,
            "candidateEdgeCoverageAtTolerance": candidate_edge_coverage,
            "edgeTolerancePx": 3,
            "rectifiedSize": 384,
            "cropSensitivity": {
                "mutation": "left-crop-and-rescale",
                "cropRatio": 0.12,
                "mutatedRectifiedColorScore": 0.91,
                "mutatedRectifiedEdgeF1": (
                    2
                    * mutated_reference_edge_coverage
                    * mutated_candidate_edge_coverage
                    / (
                        mutated_reference_edge_coverage
                        + mutated_candidate_edge_coverage
                    )
                ),
                "mutatedReferenceEdgeCoverageAtTolerance": (
                    mutated_reference_edge_coverage
                ),
                "mutatedCandidateEdgeCoverageAtTolerance": (
                    mutated_candidate_edge_coverage
                ),
                "mutatedPassed": False,
                "detected": True,
            },
            "thresholds": picture_thresholds,
            "passed": passed,
        }
    return {
        "schemaVersion": schema_version,
        "renderer": dict(case["provenance"]["renderer"]),
        "thresholds": thresholds,
        "applicableCaseCount": 1,
        "passed": passed,
        "caseResults": [
            {
                "caseId": case_id,
                "sourceSha256": case["provenance"]["inputs"]["sourcePptx"]["sha256"],
                "groundTruthSha256": case["provenance"]["inputs"]["groundTruth"][
                    "combinedSha256"
                ],
                "applicable": True,
                "passed": passed,
                "slides": [
                    {
                        "slideIdx": 0,
                        "modality": modality,
                        "referencePath": artifacts["reference"]["path"],
                        "candidatePath": artifacts["candidate"]["path"],
                        "referenceSha256": reference_hash,
                        "candidateSha256": candidate_hash,
                        "metrics": metrics,
                        "passed": passed,
                    }
                ],
            }
        ],
    }


def _bind_shadow_source(case: dict, peers: tuple[dict, ...], repo: Path) -> None:
    case_id = case["testFile"]
    source_path = repo / "test/e2e/testdata/cases" / case_id / "source.pptx"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(source_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:spPr><a:effectLst><a:outerShdw blurRad="127000" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="35000"/></a:srgbClr></a:outerShdw></a:effectLst></p:spPr></p:sp></p:spTree></p:cSld></p:sld>""",
        )
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = {
        "path": source_path.relative_to(repo).as_posix(),
        "sizeBytes": source_path.stat().st_size,
        "sha256": source_hash,
    }
    for report in (case, *peers):
        report["provenance"]["inputs"]["sourcePptx"] = dict(source)


def shadow_report(
    case: dict,
    repo: Path,
    *,
    baseline_reports: tuple[dict, ...] = (),
    passed: bool = True,
) -> dict:
    case_id = case["testFile"]
    _bind_shadow_source(case, baseline_reports, repo)
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reports_dir / f"{case_id}_pdf.png"
    candidate_path = reports_dir / f"{case_id}_html.png"
    reference_path.write_bytes(b"native-shadow-reference")
    candidate_path.write_bytes(b"renderer-shadow-candidate")
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    artifacts = {
        "reference": {
            "path": reference_path.relative_to(repo).as_posix(),
            "sizeBytes": reference_path.stat().st_size,
            "sha256": reference_hash,
        },
        "candidate": {
            "path": candidate_path.relative_to(repo).as_posix(),
            "sizeBytes": candidate_path.stat().st_size,
            "sha256": candidate_hash,
        },
    }
    case["perSlide"] = [
        {"slideIdx": 0, "hidden": False, "renderArtifacts": artifacts}
    ]
    thresholds = {
        "ringInnerRatio": 0.002,
        "ringOuterRatio": 0.05,
        "backgroundNoiseFloor": 2.0,
        "minimumReferenceShadowDensity": 0.25,
        "invisibleShadowDensity": 0.5,
        "shadowEnergyRatio": 0.75,
        "shadowOvershootRatio": 1.25,
        "shadowFieldCosine": 0.9,
        "shadowFieldIou": 0.55,
        "shadowFieldError": 0.35,
        "shadowCentroidErrorRatio": 0.03,
        "shadowFieldBinaryThreshold": 2.0,
    }
    metrics = {
        "shadowRequired": True,
        "shadowMeasurable": True,
        "referenceShadowDensity": 1.0,
        "candidateShadowDensity": 1.0 if passed else 0.5,
        "shadowEnergyRatio": 1.0 if passed else 0.5,
        "shadowOvershootRatio": 1.0 if passed else 0.5,
        "shadowFieldCosine": 1.0 if passed else 0.5,
        "shadowFieldIou": 1.0 if passed else 0.4,
        "shadowFieldError": 0.0 if passed else 0.5,
        "shadowCentroidErrorRatio": 0.0 if passed else 0.1,
        "shadowRingPixels": 100,
        "shadowRingInnerPx": 1,
        "shadowRingOuterPx": 5,
        "shadowSensitivity": {
            "mutation": "erase-exterior-shadow",
            "applicable": True,
            "mutatedCandidateShadowDensity": 0.0,
            "mutatedShadowEnergyRatio": 0.0,
            "mutatedShadowPassed": False,
            "detected": True,
        },
        "thresholds": thresholds,
        "passed": passed,
    }
    return {
        "schemaVersion": 1,
        "renderer": dict(case["provenance"]["renderer"]),
        "thresholds": thresholds,
        "applicableCaseCount": 1,
        "passed": passed,
        "caseResults": [
            {
                "caseId": case_id,
                "sourceSha256": case["provenance"]["inputs"]["sourcePptx"]["sha256"],
                "groundTruthSha256": case["provenance"]["inputs"]["groundTruth"][
                    "combinedSha256"
                ],
                "applicable": True,
                "passed": passed,
                "slides": [
                    {
                        "slideIdx": 0,
                        "shadowRequired": True,
                        "referencePath": artifacts["reference"]["path"],
                        "candidatePath": artifacts["candidate"]["path"],
                        "referenceSha256": reference_hash,
                        "candidateSha256": candidate_hash,
                        "metrics": metrics,
                        "passed": passed,
                    }
                ],
            }
        ],
    }


def _bind_reflection_source(case: dict, peers: tuple[dict, ...], repo: Path) -> None:
    case_id = case["testFile"]
    source_path = repo / "test/e2e/testdata/cases" / case_id / "source.pptx"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(source_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:spPr><a:effectLst><a:reflection blurRad="6350" stA="52000" endA="300" endPos="35000" dir="5400000" sy="-100000" algn="bl" rotWithShape="0"/></a:effectLst></p:spPr></p:sp></p:spTree></p:cSld></p:sld>""",
        )
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source = {
        "path": source_path.relative_to(repo).as_posix(),
        "sizeBytes": source_path.stat().st_size,
        "sha256": source_hash,
    }
    for report in (case, *peers):
        report["provenance"]["inputs"]["sourcePptx"] = dict(source)


def reflection_report(
    case: dict,
    repo: Path,
    *,
    baseline_reports: tuple[dict, ...] = (),
    passed: bool = True,
) -> dict:
    case_id = case["testFile"]
    _bind_reflection_source(case, baseline_reports, repo)
    reports_dir = repo / "test/e2e/reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    reference_path = reports_dir / f"{case_id}_reflection_pdf.png"
    candidate_path = reports_dir / f"{case_id}_reflection_html.png"
    reference_path.write_bytes(b"native-reflection-reference")
    candidate_path.write_bytes(b"renderer-reflection-candidate")
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    artifacts = {
        "reference": {
            "path": reference_path.relative_to(repo).as_posix(),
            "sizeBytes": reference_path.stat().st_size,
            "sha256": reference_hash,
        },
        "candidate": {
            "path": candidate_path.relative_to(repo).as_posix(),
            "sizeBytes": candidate_path.stat().st_size,
            "sha256": candidate_hash,
        },
    }
    case["perSlide"] = [
        {"slideIdx": 0, "hidden": False, "renderArtifacts": artifacts}
    ]
    thresholds = {
        "blurPadMultiplier": 2.0,
        "backgroundNoiseFloor": 2.0,
        "minimumReferenceReflectionDensity": 2.0,
        "invisibleReflectionDensity": 1.5,
        "reflectionEnergyRatio": 0.85,
        "reflectionOvershootRatio": 1.2,
        "reflectionFieldCosine": 0.95,
        "reflectionFieldIou": 0.75,
        "reflectionFieldError": 0.2,
        "reflectionCentroidErrorRatio": 0.03,
        "reflectionFieldBinaryThreshold": 2.0,
    }
    metrics = {
        "reflectionRequired": True,
        "reflectionMeasurable": True,
        "referenceReflectionDensity": 4.0,
        "candidateReflectionDensity": 4.0 if passed else 1.0,
        "reflectionEnergyRatio": 1.0 if passed else 0.25,
        "reflectionOvershootRatio": 1.0 if passed else 0.25,
        "reflectionFieldCosine": 1.0 if passed else 0.5,
        "reflectionFieldIou": 1.0 if passed else 0.4,
        "reflectionFieldError": 0.0 if passed else 0.5,
        "reflectionCentroidErrorRatio": 0.0 if passed else 0.1,
        "reflectionRegionPixels": 100,
        "reflectionSensitivity": {
            "mutation": "erase-reflection-region",
            "applicable": True,
            "mutatedCandidateReflectionDensity": 0.0,
            "mutatedReflectionEnergyRatio": 0.0,
            "mutatedReflectionPassed": False,
            "detected": True,
        },
        "thresholds": thresholds,
        "passed": passed,
    }
    return {
        "schemaVersion": 1,
        "renderer": dict(case["provenance"]["renderer"]),
        "thresholds": thresholds,
        "applicableCaseCount": 1,
        "passed": passed,
        "caseResults": [
            {
                "caseId": case_id,
                "sourceSha256": case["provenance"]["inputs"]["sourcePptx"]["sha256"],
                "groundTruthSha256": case["provenance"]["inputs"]["groundTruth"][
                    "combinedSha256"
                ],
                "applicable": True,
                "passed": passed,
                "slides": [
                    {
                        "slideIdx": 0,
                        "reflectionRequired": True,
                        "referencePath": artifacts["reference"]["path"],
                        "candidatePath": artifacts["candidate"]["path"],
                        "referenceSha256": reference_hash,
                        "candidateSha256": candidate_hash,
                        "regions": [{"x0": 0.2, "y0": 0.5, "x1": 0.8, "y1": 0.9}],
                        "metrics": metrics,
                        "passed": passed,
                    }
                ],
            }
        ],
    }


def test_normalizes_native_reports_for_the_current_revision(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)
    current = [native_report("donut-thin"), native_report("donut-thick")]
    baseline = [
        native_report("donut-thin", revision="b" * 40, ssim=0.98),
        native_report("donut-thick", revision="b" * 40, ssim=0.985),
    ]

    verification = normalize_native_evaluation_reports(
        capability,
        current,
        repo,
        oracle="powerpoint-macos",
        baseline_reports=baseline,
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )

    assert verification["renderer"] == {
        "revision": "a" * 40,
        "dirty": False,
    }
    assert verification["schemaVersion"] == 2
    assert "definitionFingerprint" not in verification
    assert set(verification["gates"].values()) == {"passed"}
    assert [case["caseId"] for case in verification["caseResults"]] == [
        "donut-thick",
        "donut-thin",
    ]
    assert all(case["manualVerdict"] == "not-required" for case in verification["caseResults"])
    assert verification["environment"]["oracle"] == "powerpoint-macos"
    assert verification["environment"]["browser"] == {"name": "chrome", "version": "152"}


def test_verification_receipt_preserves_capture_environment(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)
    current = native_report("donut-thin")
    baseline = native_report("donut-thin", revision="b" * 40)
    capture = {
        "browserCssDpi": 96,
        "pdfRasterDpi": 150,
        "pdfDeviceScaleFactor": 1.5625,
        "pngDeviceScaleFactor": 1.0,
    }
    current["provenance"]["runtime"]["capture"] = capture
    baseline["provenance"]["runtime"]["capture"] = capture

    verification = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )

    assert verification["environment"]["capture"] == current["provenance"]["runtime"][
        "capture"
    ]


def test_rejects_regression_beyond_the_ssim_budget(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)

    with pytest.raises(CapabilityVerificationError, match="SSIM regression"):
        normalize_native_evaluation_reports(
            capability,
            [native_report("donut-thin", ssim=0.95)],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[native_report("donut-thin", revision="b" * 40, ssim=0.98)],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )


def test_rejects_incomparable_regression_baselines(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)
    current = [native_report("donut-thin")]
    same_revision = [native_report("donut-thin")]

    with pytest.raises(CapabilityVerificationError, match="earlier renderer revision"):
        normalize_native_evaluation_reports(
            capability,
            current,
            repo,
            oracle="powerpoint-macos",
            baseline_reports=same_revision,
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )

    changed_input = native_report("donut-thin", revision="b" * 40)
    changed_input["provenance"]["inputs"]["sourcePptx"]["sha256"] = "e" * 64
    with pytest.raises(CapabilityVerificationError, match="input hashes must match"):
        normalize_native_evaluation_reports(
            capability,
            current,
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[changed_input],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )


def test_regression_environment_compares_resolved_fonts_not_manifest_formatting(
    tmp_path: Path,
):
    repo, capability = capability_fixture(tmp_path)
    current = native_report("donut-thin")
    baseline = native_report("donut-thin", revision="b" * 40)
    resolved_profile = {
        "id": "office-core",
        "manifest": {
            "path": "font-profiles/office-core.json",
            "sizeBytes": 120,
            "sha256": "1" * 64,
        },
        "faces": [
            {
                "family": "Calibri",
                "descriptors": {"weight": "400", "style": "normal"},
                "file": {
                    "path": "font-profiles/fonts/calibri.ttf",
                    "sizeBytes": 1000,
                    "sha256": "2" * 64,
                },
            }
        ],
    }
    current["provenance"]["runtime"]["fontProfile"] = resolved_profile
    baseline["provenance"]["runtime"]["fontProfile"] = {
        **resolved_profile,
        "manifest": {
            "path": "font-profiles/office-core.json",
            "sizeBytes": 180,
            "sha256": "3" * 64,
        },
    }

    verification = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )

    assert verification["gates"]["regression"] == "passed"

    changed_font = json.loads(json.dumps(baseline))
    changed_font["provenance"]["runtime"]["fontProfile"]["faces"][0]["file"][
        "sha256"
    ] = "4" * 64
    with pytest.raises(CapabilityVerificationError, match="runtime environment"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[changed_font],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )

def test_requires_explicit_verdict_for_a_review_row(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)
    current = [native_report("donut-thin", needs_review=True)]
    baseline = [native_report("donut-thin", revision="b" * 40)]

    with pytest.raises(CapabilityVerificationError, match="manual verdict"):
        normalize_native_evaluation_reports(
            capability,
            current,
            repo,
            oracle="powerpoint-macos",
            baseline_reports=baseline,
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )

    verification = normalize_native_evaluation_reports(
        capability,
        current,
        repo,
        oracle="powerpoint-macos",
        baseline_reports=baseline,
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        manual_verdicts={"donut-thin": "passed"},
    )
    assert verification["caseResults"][0]["manualVerdict"] == "passed"


def test_rejects_dirty_or_mixed_revision_reports(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)
    baseline = [native_report("donut-thin", revision="b" * 40)]

    with pytest.raises(CapabilityVerificationError, match="clean renderer revision"):
        normalize_native_evaluation_reports(
            capability,
            [native_report("donut-thin", dirty=True)],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=baseline,
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )

    with pytest.raises(CapabilityVerificationError, match="one renderer revision"):
        normalize_native_evaluation_reports(
            capability,
            [
                native_report("donut-thin"),
                native_report("donut-thick", revision="c" * 40),
            ],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[
                native_report("donut-thin", revision="b" * 40),
                native_report("donut-thick", revision="b" * 40),
            ],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
        )


def test_does_not_allow_callers_to_self_attest_derived_gates(tmp_path: Path):
    repo, capability = capability_fixture(tmp_path)

    with pytest.raises(CapabilityVerificationError, match="derived gate"):
        normalize_native_evaluation_reports(
            capability,
            [native_report("donut-thin")],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[native_report("donut-thin", revision="b" * 40)],
            passed_gates=("native-powerpoint",),
        )


def test_derives_bevel_local_gate_from_matching_artifact_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "bevel-local"),
    )
    current = native_report("donut-thin")
    baseline = native_report("donut-thin", revision="b" * 40)

    missing = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )
    assert missing["gates"]["bevel-local"] == "missing"

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        bevel_report=bevel_report(current, repo),
    )
    assert verified["gates"]["bevel-local"] == "passed"


def test_derives_camera_local_gate_from_matching_artifact_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-plane")
    baseline = native_report("camera-plane", revision="b" * 40)

    missing = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )
    assert missing["gates"]["camera-local"] == "missing"

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo),
    )
    assert verified["gates"]["camera-local"] == "passed"


def test_derives_shadow_local_gate_from_matching_artifact_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "shadow-local"),
    )
    current = native_report("outer-shadow-matrix")
    baseline = native_report("outer-shadow-matrix", revision="b" * 40)

    missing = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )
    assert missing["gates"]["shadow-local"] == "missing"

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        shadow_report=shadow_report(current, repo, baseline_reports=(baseline,)),
    )
    assert verified["gates"]["shadow-local"] == "passed"


def test_derives_reflection_local_gate_from_matching_artifact_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "reflection-local"),
    )
    current = native_report("reflection-matrix")
    baseline = native_report("reflection-matrix", revision="b" * 40)

    missing = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
    )
    assert missing["gates"]["reflection-local"] == "missing"

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        reflection_report=reflection_report(
            current, repo, baseline_reports=(baseline,)
        ),
    )
    assert verified["gates"]["reflection-local"] == "passed"


def test_rejects_reflection_local_erasure_sensitivity_tampering(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "reflection-local"),
    )
    current = native_report("reflection-matrix")
    baseline = native_report("reflection-matrix", revision="b" * 40)
    report = reflection_report(current, repo, baseline_reports=(baseline,))
    report["caseResults"][0]["slides"][0]["metrics"]["reflectionSensitivity"][
        "detected"
    ] = False

    with pytest.raises(CapabilityVerificationError, match="sensitivity"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            reflection_report=report,
        )


def test_rejects_invalid_reflection_local_region(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "reflection-local"),
    )
    current = native_report("reflection-matrix")
    baseline = native_report("reflection-matrix", revision="b" * 40)
    report = reflection_report(current, repo, baseline_reports=(baseline,))
    report["caseResults"][0]["slides"][0]["regions"][0]["x1"] = 1.2

    with pytest.raises(CapabilityVerificationError, match="region"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            reflection_report=report,
        )


def test_rejects_shadow_local_erasure_sensitivity_tampering(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "shadow-local"),
    )
    current = native_report("outer-shadow-matrix")
    baseline = native_report("outer-shadow-matrix", revision="b" * 40)
    report = shadow_report(current, repo, baseline_reports=(baseline,))
    report["caseResults"][0]["slides"][0]["metrics"]["shadowSensitivity"][
        "detected"
    ] = False

    with pytest.raises(CapabilityVerificationError, match="sensitivity"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            shadow_report=report,
        )


def test_rejects_failed_shadow_local_report(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "shadow-local"),
    )
    current = native_report("outer-shadow-matrix")
    baseline = native_report("outer-shadow-matrix", revision="b" * 40)

    with pytest.raises(CapabilityVerificationError, match="shadow-local report failed"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            shadow_report=shadow_report(
                current, repo, baseline_reports=(baseline,), passed=False
            ),
        )


def test_rejects_shadow_requirement_that_disagrees_with_source_ooxml(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "shadow-local"),
    )
    current = native_report("outer-shadow-matrix")
    baseline = native_report("outer-shadow-matrix", revision="b" * 40)
    report = shadow_report(current, repo, baseline_reports=(baseline,))
    report["caseResults"][0]["slides"][0]["shadowRequired"] = False
    report["caseResults"][0]["slides"][0]["metrics"]["shadowRequired"] = False

    with pytest.raises(
        CapabilityVerificationError,
        match="shadow requirement does not match source OOXML",
    ):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            shadow_report=report,
        )


def test_rejects_shadow_sensitivity_that_does_not_cross_failure_threshold(
    tmp_path: Path,
):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "shadow-local"),
    )
    current = native_report("outer-shadow-matrix")
    baseline = native_report("outer-shadow-matrix", revision="b" * 40)
    report = shadow_report(current, repo, baseline_reports=(baseline,))
    sensitivity = report["caseResults"][0]["slides"][0]["metrics"][
        "shadowSensitivity"
    ]
    sensitivity["mutatedCandidateShadowDensity"] = 1.0
    sensitivity["mutatedShadowEnergyRatio"] = 1.0

    with pytest.raises(CapabilityVerificationError, match="sensitivity is invalid"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            shadow_report=report,
        )


def test_derives_camera_local_gate_from_live_text_projection_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-text")
    baseline = native_report("camera-text", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, modality="text"),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_derives_camera_local_gate_from_live_picture_projection_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-picture")
    baseline = native_report("camera-picture", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, modality="picture"),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_derives_camera_local_gate_from_live_picture_group_projection_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-picture-group")
    baseline = native_report("camera-picture-group", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, modality="picture-group"),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_camera_local_keeps_schema_v6_reports_backward_compatible(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-plane-v6")
    baseline = native_report("camera-plane-v6", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, schema_version=6),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_derives_camera_local_gate_from_custom_geometry_silhouette_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-custom-geometry")
    baseline = native_report("camera-custom-geometry", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, modality="custom-geometry"),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_rejects_inconsistent_custom_geometry_metrics_or_undetected_squash(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-custom-geometry")
    baseline = native_report("camera-custom-geometry", revision="b" * 40)

    inconsistent = camera_report(current, repo, modality="custom-geometry")
    inconsistent["caseResults"][0]["slides"][0]["metrics"]["foregroundAreaRatio"] = 0.5
    with pytest.raises(CapabilityVerificationError, match="custom metrics are inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=inconsistent,
        )

    undetected = camera_report(current, repo, modality="custom-geometry")
    undetected["caseResults"][0]["slides"][0]["metrics"]["squashSensitivity"][
        "detected"
    ] = False
    with pytest.raises(CapabilityVerificationError, match="squash sensitivity"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=undetected,
        )


def test_derives_camera_local_gate_from_bottom_bevel_front_material_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("bottom-material")
    baseline = native_report("bottom-material", revision="b" * 40)

    verified = normalize_native_evaluation_reports(
        capability,
        [current],
        repo,
        oracle="powerpoint-macos",
        baseline_reports=[baseline],
        passed_gates=("source", "structural", "unit", "browser", "docs"),
        camera_report=camera_report(current, repo, modality="bottom-material"),
    )

    assert verified["gates"]["camera-local"] == "passed"


def test_rejects_undetected_bottom_bevel_flat_fill_mutation(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("bottom-material")
    baseline = native_report("bottom-material", revision="b" * 40)
    report = camera_report(current, repo, modality="bottom-material")
    report["caseResults"][0]["slides"][0]["metrics"]["flatFillSensitivity"][
        "detected"
    ] = False

    with pytest.raises(CapabilityVerificationError, match="flat fill sensitivity"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=report,
        )


def test_rejects_failed_or_tampered_camera_local_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-plane")
    baseline = native_report("camera-plane", revision="b" * 40)
    failed = camera_report(current, repo, passed=False)
    with pytest.raises(CapabilityVerificationError, match="camera-local report failed"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=failed,
        )

    tampered = camera_report(current, repo)
    (repo / tampered["caseResults"][0]["slides"][0]["candidatePath"]).write_bytes(b"changed")
    with pytest.raises(CapabilityVerificationError, match="artifact hash"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=tampered,
        )


def test_rejects_inconsistent_camera_shadow_or_picture_metrics(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )

    plane = native_report("camera-plane")
    plane_baseline = native_report("camera-plane", revision="b" * 40)
    inconsistent_shadow = camera_report(plane, repo)
    inconsistent_shadow["caseResults"][0]["slides"][0]["metrics"][
        "shadowEnergyRatio"
    ] = 0.05
    with pytest.raises(CapabilityVerificationError, match="shadow metrics are inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [plane],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[plane_baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=inconsistent_shadow,
        )

    materially_weak_shadow = camera_report(plane, repo)
    materially_weak_shadow["caseResults"][0]["slides"][0]["metrics"].update(
        shadowEnergyRatio=0.5,
        shadowPassed=True,
        passed=True,
    )
    with pytest.raises(CapabilityVerificationError, match="shadow metrics are inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [plane],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[plane_baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=materially_weak_shadow,
        )

    undetected_shadow = camera_report(plane, repo)
    undetected_shadow["caseResults"][0]["slides"][0]["metrics"][
        "shadowSensitivity"
    ]["detected"] = False
    with pytest.raises(CapabilityVerificationError, match="shadow sensitivity is inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [plane],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[plane_baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=undetected_shadow,
        )

    picture = native_report("camera-picture")
    picture_baseline = native_report("camera-picture", revision="b" * 40)
    inconsistent_picture = camera_report(picture, repo, modality="picture")
    inconsistent_picture["caseResults"][0]["slides"][0]["metrics"][
        "rectifiedEdgeF1"
    ] = 0.5
    with pytest.raises(CapabilityVerificationError, match="picture edge metrics are inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [picture],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[picture_baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=inconsistent_picture,
        )

    undetected_crop = camera_report(picture, repo, modality="picture")
    undetected_crop["caseResults"][0]["slides"][0]["metrics"]["cropSensitivity"][
        "detected"
    ] = False
    with pytest.raises(CapabilityVerificationError, match="crop sensitivity is inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [picture],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[picture_baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=undetected_crop,
        )


def test_rejects_inconsistent_camera_text_metrics_or_thresholds(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "camera-local"),
    )
    current = native_report("camera-text")
    baseline = native_report("camera-text", revision="b" * 40)

    inconsistent = camera_report(current, repo, modality="text")
    inconsistent["caseResults"][0]["slides"][0]["metrics"][
        "tolerantForegroundF1"
    ] = 1.0
    with pytest.raises(CapabilityVerificationError, match="tolerant metrics are inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=inconsistent,
        )

    threshold_drift = camera_report(current, repo, modality="text")
    threshold_drift["thresholds"]["text"]["tolerantForegroundF1"] = 0.1
    with pytest.raises(CapabilityVerificationError, match="unexpected thresholds"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            camera_report=threshold_drift,
        )


def test_rejects_bevel_local_evidence_for_different_inputs_or_failed_metrics(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "bevel-local"),
    )
    current = native_report("donut-thin")
    baseline = native_report("donut-thin", revision="b" * 40)
    wrong_input = bevel_report(current, repo)
    wrong_input["caseResults"][0]["sourceSha256"] = "e" * 64

    with pytest.raises(CapabilityVerificationError, match="input hashes"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=wrong_input,
        )

    with pytest.raises(CapabilityVerificationError, match="bevel-local report failed"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=bevel_report(current, repo, passed=False),
        )


def test_rejects_tampered_bevel_artifacts_and_inconsistent_metric_results(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "bevel-local"),
    )
    current = native_report("donut-thin")
    baseline = native_report("donut-thin", revision="b" * 40)
    tampered = bevel_report(current, repo)
    candidate_path = repo / tampered["caseResults"][0]["slides"][0]["candidatePath"]
    candidate_path.write_bytes(b"changed-after-metric")

    with pytest.raises(CapabilityVerificationError, match="artifact hash"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=tampered,
        )

    mismatched_native = bevel_report(current, repo)
    current["perSlide"][0]["renderArtifacts"]["candidate"]["sha256"] = "0" * 64
    with pytest.raises(CapabilityVerificationError, match="native report artifacts"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=mismatched_native,
        )

    inconsistent = bevel_report(current, repo)
    metrics = inconsistent["caseResults"][0]["slides"][0]["regions"][0]["metrics"]
    metrics["score"] = 0.2

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=inconsistent,
        )

    overdark = bevel_report(current, repo)
    metrics = overdark["caseResults"][0]["slides"][0]["regions"][0]["metrics"]
    metrics.update(
        candidateDynamicRange=120.0,
        rangeRatio=80.0 / 120.0,
        candidateShadowAmplitude=90.0,
        shadowAmplitudeRatio=0.5,
        shadowOvershootRatio=2.0,
    )

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=overdark,
        )

    visible_overshoot = bevel_report(current, repo)
    metrics = visible_overshoot["caseResults"][0]["slides"][0]["regions"][0]["metrics"]
    metrics.update(
        candidateDynamicRange=82.7,
        rangeRatio=80.0 / 82.7,
        candidateShadowAmplitude=47.7,
        shadowAmplitudeRatio=45.0 / 47.7,
        shadowOvershootRatio=47.7 / 45.0,
    )

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=visible_overshoot,
        )

    donut_overshoot = bevel_report(current, repo)
    region = donut_overshoot["caseResults"][0]["slides"][0]["regions"][0]
    region["region"]["preset"] = "donut"
    metrics = region["metrics"]
    metrics.update(
        candidateDynamicRange=80.675,
        rangeRatio=80.0 / 80.675,
        candidateShadowAmplitude=45.675,
        shadowAmplitudeRatio=45.0 / 45.675,
        shadowOvershootRatio=45.675 / 45.0,
    )

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=donut_overshoot,
        )

    donut_energy_overshoot = bevel_report(current, repo)
    region = donut_energy_overshoot["caseResults"][0]["slides"][0]["regions"][0]
    region["region"]["preset"] = "donut"
    metrics = region["metrics"]
    metrics.update(
        referenceShadowEnergy=12.0,
        candidateShadowEnergy=12.72,
        shadowEnergyOvershootRatio=1.06,
    )

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=donut_energy_overshoot,
        )

    donut_local_excess = bevel_report(current, repo)
    region = donut_local_excess["caseResults"][0]["slides"][0]["regions"][0]
    region["region"]["preset"] = "donut"
    region["metrics"]["shadowLocalExcessRatio"] = 0.31

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=donut_local_excess,
        )

    donut_sector_overshoot = bevel_report(current, repo)
    region = donut_sector_overshoot["caseResults"][0]["slides"][0]["regions"][0]
    region["region"]["preset"] = "donut"
    region["metrics"].update(
        shadowSectorOvershootRatio=1.61,
        shadowSectors=[
            {
                "contour": "outer",
                "startAngle": 60,
                "endAngle": 90,
                "pixelCount": 120,
                "referenceShadowEnergy": 10.0,
                "candidateShadowEnergy": 16.1,
                "overshootRatio": 1.61,
            }
        ],
    )

    with pytest.raises(CapabilityVerificationError, match="metric pass status"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=donut_sector_overshoot,
        )


def test_rejects_inconsistent_bevel_equivalence_evidence(tmp_path: Path):
    repo, base_capability = capability_fixture(tmp_path)
    capability = replace(
        base_capability,
        required_gates=(*base_capability.required_gates, "bevel-local"),
    )
    current = native_report("default-bevel-pair")
    baseline = native_report("default-bevel-pair", revision="b" * 40)
    evidence = bevel_report(current, repo)
    native_slide = current["perSlide"][0]
    second_native_slide = {
        **native_slide,
        "slideIdx": 1,
    }
    current["perSlide"].append(second_native_slide)
    evidence_slide = evidence["caseResults"][0]["slides"][0]
    evidence["caseResults"][0]["slides"].append(
        {
            **evidence_slide,
            "slideIdx": 1,
        }
    )
    evidence["caseResults"][0]["equivalencePairs"] = [
        {
            "leftSlideIdx": 0,
            "rightSlideIdx": 1,
            "referenceEqual": False,
            "candidateEqual": True,
            "passed": False,
        }
    ]
    evidence["caseResults"][0]["passed"] = False
    evidence["passed"] = False

    with pytest.raises(CapabilityVerificationError, match="equivalence evidence is inconsistent"):
        normalize_native_evaluation_reports(
            capability,
            [current],
            repo,
            oracle="powerpoint-macos",
            baseline_reports=[baseline],
            passed_gates=("source", "structural", "unit", "browser", "docs"),
            bevel_report=evidence,
        )
