import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from oracle.capability_contract import load_capability_registry
from oracle.capability_inventory import (
    CapabilityInventoryError,
    ScanLimits,
    inventory_to_dict,
    scan_corpus,
    scan_pptx,
    write_inventory,
)


A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


SLIDE_WITH_SCENE_AND_SP3D = f"""
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="{A_NS}">
  <p:cSld><p:spTree><p:sp><p:spPr>
    <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
    <a:scene3d>
      <a:camera prst="orthographicFront"/>
      <a:lightRig rig="threePt" dir="t"/>
    </a:scene3d>
    <a:sp3d extrusionH="0" contourW="12700">
      <a:bevelT w="127000" h="127000" prst="circle"/>
      <a:contourClr><a:srgbClr val="FFFFFF"/></a:contourClr>
    </a:sp3d>
  </p:spPr></p:sp></p:spTree></p:cSld>
</p:sld>
"""

CHART_WITH_VIEW3D = f"""
<c:chartSpace xmlns:c="{C_NS}">
  <c:chart><c:view3D><c:rotX val="20"/></c:view3D></c:chart>
</c:chartSpace>
"""


def write_test_pptx(
    path: Path,
    *,
    slide_xml: str = SLIDE_WITH_SCENE_AND_SP3D,
    chart_xml: str | None = CHART_WITH_VIEW3D,
    extra_entries: dict[str, bytes] | None = None,
) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("ppt/slides/slide1.xml", slide_xml)
        if chart_xml is not None:
            archive.writestr("ppt/charts/chart1.xml", chart_xml)
        for name, data in (extra_entries or {}).items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def registry():
    return load_capability_registry(Path("oracle/capabilities.json"))


def test_scan_pptx_detects_shape_and_chart_3d_by_namespace(tmp_path: Path, registry):
    package = write_test_pptx(tmp_path / "sample.pptx")

    observation = scan_pptx(package, registry)

    assert observation.capability_ids == (
        "drawingml.chart.3d.view",
        "drawingml.shape.3d.camera-projected-plane",
        "drawingml.shape.3d.scene",
        "drawingml.shape.3d.top-bevel-contour",
    )
    assert observation.sha256 == observation.package_id.removeprefix("sha256:")
    assert observation.matching_parts["drawingml.chart.3d.view"] == (
        "ppt/charts/chart1.xml",
    )
    assert observation.matching_parts["drawingml.shape.3d.top-bevel-contour"] == (
        "ppt/slides/slide1.xml",
    )


def test_scan_pptx_matches_attribute_scopes_and_ignores_unrelated_namespaces(
    tmp_path: Path,
    registry,
):
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}" xmlns:x="urn:not-drawingml">
      <p:cSld><p:spTree><p:sp><p:spPr>
        <a:prstGeom prst="donut"><a:avLst/></a:prstGeom>
        <x:scene3d/><x:sp3d><x:bevelT/></x:sp3d>
      </p:spPr></p:sp></p:spTree></p:cSld>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "donut.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert observation.capability_ids == ("drawingml.shape.geometry.adjustment.donut",)


def test_scan_pptx_detects_zero_depth_shape_camera_candidate_and_keeps_residual_scene(
    tmp_path: Path,
    registry,
):
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}">
      <p:cSld><p:spTree><p:sp><p:spPr>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        <a:scene3d>
          <a:camera prst="perspectiveRelaxedModerately" fov="7200000">
            <a:rot lat="18590633" lon="0" rev="0"/>
          </a:camera>
          <a:lightRig rig="threePt" dir="t"/>
        </a:scene3d>
        <a:sp3d extrusionH="0"/>
      </p:spPr></p:sp></p:spTree></p:cSld>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "camera-plane.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert observation.capability_ids == (
        "drawingml.shape.3d.camera-projected-plane",
        "drawingml.shape.3d.scene",
    )


def test_scan_pptx_detects_scene_only_camera_candidate_and_keeps_residual_scene(
    tmp_path: Path,
    registry,
):
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}">
      <p:cSld><p:spTree><p:sp><p:spPr>
        <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
        <a:scene3d>
          <a:camera prst="perspectiveContrastingRightFacing" fov="5100000">
            <a:rot lat="0" lon="19532225" rev="0"/>
          </a:camera>
          <a:lightRig rig="threePt" dir="t"/>
        </a:scene3d>
      </p:spPr></p:sp></p:spTree></p:cSld>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "scene-only-camera.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert observation.capability_ids == (
        "drawingml.shape.3d.camera-projected-plane",
        "drawingml.shape.3d.scene",
    )


def test_scan_pptx_separates_shape_group_and_text_body_scene3d_by_parent(tmp_path: Path, registry):
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}">
      <p:cSld><p:spTree>
        <p:grpSp>
          <p:nvGrpSpPr><p:cNvPr id="1" name="Group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
          <p:grpSpPr>
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/><a:chOff x="0" y="0"/><a:chExt cx="1" cy="1"/></a:xfrm>
            <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
          </p:grpSpPr>
          <p:sp>
            <p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
            <p:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
            <p:txBody>
              <a:bodyPr>
                <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
                <a:sp3d><a:contourClr><a:srgbClr val="FFFFFF"/></a:contourClr></a:sp3d>
              </a:bodyPr>
              <a:lstStyle/><a:p/>
            </p:txBody>
          </p:sp>
        </p:grpSp>
      </p:spTree></p:cSld>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "parent-scoped-scenes.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert "drawingml.shape.3d.scene" in observation.capability_ids
    assert "drawingml.text.3d.scene" in observation.capability_ids
    assert "drawingml.shape.3d.camera-projected-plane" in observation.capability_ids
    assert observation.matching_parts["drawingml.shape.3d.camera-projected-plane"] == (
        "ppt/slides/slide1.xml",
    )
    assert observation.matching_parts["drawingml.shape.3d.scene"] == ("ppt/slides/slide1.xml",)
    assert observation.matching_parts["drawingml.text.3d.scene"] == ("ppt/slides/slide1.xml",)


def test_text_body_scene3d_does_not_count_as_shape_scene3d(tmp_path: Path, registry):
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}">
      <p:cSld><p:spTree><p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
        <p:txBody>
          <a:bodyPr>
            <a:scene3d><a:camera prst="orthographicFront"/><a:lightRig rig="threePt" dir="t"/></a:scene3d>
            <a:sp3d extrusionH="0"/>
          </a:bodyPr>
          <a:lstStyle/><a:p/>
        </p:txBody>
      </p:sp></p:spTree></p:cSld>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "text-scene.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert "drawingml.text.3d.scene" in observation.capability_ids
    assert "drawingml.shape.3d.scene" not in observation.capability_ids
    assert "drawingml.shape.3d.camera-projected-plane" not in observation.capability_ids


def test_scan_pptx_detects_bounded_entrance_fade_and_residual_timing(
    tmp_path: Path,
    registry,
):
    slide = f"""
    <p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
      <p:cSld><p:spTree/></p:cSld>
      <p:timing><p:tnLst><p:par>
        <p:cTn presetID="10" presetClass="entr" presetSubtype="0"
               fill="hold" nodeType="clickEffect">
          <p:childTnLst>
            <p:animEffect transition="in" filter="fade"/>
          </p:childTnLst>
        </p:cTn>
      </p:par></p:tnLst></p:timing>
    </p:sld>
    """

    observation = scan_pptx(
        write_test_pptx(tmp_path / "entrance-fade.pptx", slide_xml=slide, chart_xml=None),
        registry,
    )

    assert "presentation.animation.entrance.fade" in observation.capability_ids
    assert "presentation.animation.timing" in observation.capability_ids


def test_scan_pptx_matches_exact_ancestor_path_without_counting_other_outer_shadows(
    tmp_path: Path,
):
    entry = {
        "id": "drawingml.shape.effect.outer-shadow",
        "component": "shape",
        "renderMode": "approximate",
        "impact": "fidelity",
        "selectors": [
            {
                "partGlob": "ppt/**/*.xml",
                "namespace": A_NS,
                "localName": "outerShdw",
                "ancestorPath": [
                    {"namespace": P_NS, "localNames": ["sp"]},
                    {"namespace": P_NS, "localNames": ["spPr"]},
                    {"namespace": A_NS, "localNames": ["effectLst"]},
                ],
            }
        ],
        "scope": {"source": "direct-shape-effect-list"},
        "fallback": "Render a bounded shadow approximation.",
        "affectedPaths": ["src/renderer/ShapeRenderer.ts"],
        "requiredGates": ["source", "unit"],
        "issueUrls": [],
    }
    registry = load_capability_registry(
        write_json(
            tmp_path / "outer-shadow-capabilities.json",
            {"schemaVersion": 2, "capabilities": [entry]},
        )
    )
    target = f"""
    <p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
      <p:cSld><p:spTree><p:grpSp>
        <p:sp><p:spPr><a:effectLst><a:outerShdw/></a:effectLst></p:spPr></p:sp>
      </p:grpSp></p:spTree></p:cSld>
    </p:sld>
    """
    unrelated = f"""
    <p:sld xmlns:p="{P_NS}" xmlns:a="{A_NS}">
      <p:cSld><p:spTree>
        <p:pic><p:spPr><a:effectLst><a:outerShdw/></a:effectLst></p:spPr></p:pic>
        <p:grpSp><p:grpSpPr><a:effectLst><a:outerShdw/></a:effectLst></p:grpSpPr></p:grpSp>
        <p:sp>
          <p:spPr/>
          <p:txBody><a:p><a:r><a:rPr><a:effectLst><a:outerShdw/></a:effectLst></a:rPr></a:r></a:p></p:txBody>
        </p:sp>
        <a:effectStyle><a:effectLst><a:outerShdw/></a:effectLst></a:effectStyle>
      </p:spTree></p:cSld>
    </p:sld>
    """

    target_observation = scan_pptx(
        write_test_pptx(tmp_path / "target.pptx", slide_xml=target, chart_xml=None),
        registry,
    )
    unrelated_observation = scan_pptx(
        write_test_pptx(tmp_path / "unrelated.pptx", slide_xml=unrelated, chart_xml=None),
        registry,
    )

    assert target_observation.capability_ids == ("drawingml.shape.effect.outer-shadow",)
    assert unrelated_observation.capability_ids == ()


@pytest.mark.parametrize(
    ("entries", "limits", "message"),
    [
        ({"../escape.xml": b"x"}, ScanLimits(), "unsafe ZIP member"),
        ({"extra.bin": b"x"}, ScanLimits(max_entries=1), "entry count"),
        ({"large.bin": b"12345"}, ScanLimits(max_entry_uncompressed_bytes=4), "entry size"),
        ({"a.bin": b"123", "b.bin": b"456"}, ScanLimits(max_total_uncompressed_bytes=5), "total"),
    ],
)
def test_scan_pptx_rejects_unsafe_or_oversized_archives(
    tmp_path: Path,
    registry,
    entries: dict[str, bytes],
    limits: ScanLimits,
    message: str,
):
    package = write_test_pptx(
        tmp_path / "unsafe.pptx",
        slide_xml="<s/>",
        chart_xml=None,
        extra_entries=entries,
    )

    with pytest.raises(CapabilityInventoryError, match=message):
        scan_pptx(package, registry, limits)


def test_scan_pptx_rejects_xml_document_type_declarations(tmp_path: Path, registry):
    slide = """<!DOCTYPE p:sld [<!ENTITY x "expanded">]>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <a:scene3d>&x;</a:scene3d>
    </p:sld>"""
    package = write_test_pptx(tmp_path / "doctype.pptx", slide_xml=slide, chart_xml=None)

    with pytest.raises(CapabilityInventoryError, match="DTD or entity declaration"):
        scan_pptx(package, registry)


def test_scan_corpus_deduplicates_identical_packages_and_serializes_deterministically(
    tmp_path: Path,
    registry,
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    first = write_test_pptx(corpus / "first.pptx")
    (corpus / "copy.pptx").write_bytes(first.read_bytes())

    report = scan_corpus([corpus], registry)
    first_output = write_inventory(report, tmp_path / "first.json").read_bytes()
    second_output = write_inventory(report, tmp_path / "second.json").read_bytes()

    assert report.raw_package_count == 2
    assert report.unique_package_count == 1
    assert report.packages[0].aliases == (
        "corpus-0/copy.pptx",
        "corpus-0/first.pptx",
    )
    assert first_output == second_output
    payload = json.loads(first_output)
    assert payload["packages"][0]["sha256"] == report.packages[0].sha256
    explicitly_classified = inventory_to_dict(
        report,
        representative_alias_globs=("corpus-0/first.pptx",),
    )
    assert explicitly_classified["packages"][0]["corpusRole"] == "representative"
    assert explicitly_classified["corpusClassification"]["representativeUniquePackageCount"] == 1


def test_inventory_classifies_explicit_representative_aliases_without_rescanning(
    tmp_path: Path,
    registry,
):
    corpus = tmp_path / "corpus"
    representative = corpus / "representative"
    validation = corpus / "validation"
    representative.mkdir(parents=True)
    validation.mkdir(parents=True)
    write_test_pptx(representative / "source.pptx")
    write_test_pptx(
        validation / "source.pptx",
        slide_xml=SLIDE_WITH_SCENE_AND_SP3D.replace('contourW="12700"', 'contourW="25400"'),
    )

    payload = inventory_to_dict(
        scan_corpus([corpus], registry),
        representative_alias_globs=("corpus-0/representative/*",),
    )

    roles_by_alias = {
        package["aliases"][0]: package["corpusRole"] for package in payload["packages"]
    }
    assert roles_by_alias == {
        "corpus-0/representative/source.pptx": "representative",
        "corpus-0/validation/source.pptx": "validation",
    }
    assert payload["corpusClassification"] == {
        "mode": "explicit-representative-alias-globs",
        "representativeAliasGlobs": ["corpus-0/representative/*"],
        "validationAliasGlobs": [],
        "representativeUniquePackageCount": 1,
        "validationUniquePackageCount": 1,
    }

    with pytest.raises(ValueError, match="matched no packages"):
        inventory_to_dict(
            scan_corpus([corpus], registry),
            representative_alias_globs=("corpus-0/misspelled/*",),
        )


def test_inventory_classifies_explicit_validation_aliases_as_the_complement(
    tmp_path: Path,
    registry,
):
    corpus = tmp_path / "corpus"
    representative = corpus / "customer-deck"
    validation = corpus / "oracle-pypptx-shape3d-0001"
    representative.mkdir(parents=True)
    validation.mkdir(parents=True)
    write_test_pptx(representative / "source.pptx")
    write_test_pptx(
        validation / "source.pptx",
        slide_xml=SLIDE_WITH_SCENE_AND_SP3D.replace('contourW="12700"', 'contourW="25400"'),
    )

    payload = inventory_to_dict(
        scan_corpus([corpus], registry),
        validation_alias_globs=("corpus-0/oracle-*",),
    )

    roles_by_alias = {
        package["aliases"][0]: package["corpusRole"] for package in payload["packages"]
    }
    assert roles_by_alias == {
        "corpus-0/customer-deck/source.pptx": "representative",
        "corpus-0/oracle-pypptx-shape3d-0001/source.pptx": "validation",
    }
    assert payload["corpusClassification"] == {
        "mode": "explicit-validation-alias-globs",
        "representativeAliasGlobs": [],
        "validationAliasGlobs": ["corpus-0/oracle-*"],
        "representativeUniquePackageCount": 1,
        "validationUniquePackageCount": 1,
    }

    with pytest.raises(ValueError, match="mutually exclusive"):
        inventory_to_dict(
            scan_corpus([corpus], registry),
            representative_alias_globs=("corpus-0/customer-*",),
            validation_alias_globs=("corpus-0/oracle-*",),
        )


def test_validation_aliases_do_not_demote_an_identical_representative_package(
    tmp_path: Path,
    registry,
):
    corpus = tmp_path / "corpus"
    representative = corpus / "customer-deck"
    validation = corpus / "oracle-generated-case"
    representative.mkdir(parents=True)
    validation.mkdir(parents=True)
    source = write_test_pptx(representative / "source.pptx")
    (validation / "source.pptx").write_bytes(source.read_bytes())

    payload = inventory_to_dict(
        scan_corpus([corpus], registry),
        validation_alias_globs=("corpus-0/oracle-*",),
    )

    assert len(payload["packages"]) == 1
    assert payload["packages"][0]["aliases"] == [
        "corpus-0/customer-deck/source.pptx",
        "corpus-0/oracle-generated-case/source.pptx",
    ]
    assert payload["packages"][0]["corpusRole"] == "representative"
    assert payload["corpusClassification"]["representativeUniquePackageCount"] == 1
    assert payload["corpusClassification"]["validationUniquePackageCount"] == 0


def test_scan_corpus_records_one_rejected_package_and_continues(tmp_path: Path, registry):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_test_pptx(corpus / "valid.pptx")
    write_test_pptx(
        corpus / "oversized.pptx",
        slide_xml="<s/>",
        chart_xml=None,
        extra_entries={"ppt/media/large.bin": b"x" * 2049},
    )

    report = scan_corpus(
        [corpus],
        registry,
        ScanLimits(max_entry_uncompressed_bytes=2048),
    )

    assert report.raw_package_count == 2
    assert report.unique_package_count == 1
    assert report.rejected_package_count == 1
    assert report.rejected_packages[0].aliases == ("corpus-0/oversized.pptx",)
    assert report.rejected_packages[0].reason_code == "zip-entry-size"
