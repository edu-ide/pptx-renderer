import json
from pathlib import Path

import pytest

from oracle.capability_contract import (
    load_acceptance_history,
    load_capability_registry,
    validate_acceptance_history,
)


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def capability(
    capability_id: str = "drawingml.shape.geometry.rect",
    *,
    render_mode: str = "fallback",
    fallback: str = "Render the existing flat 2D shape.",
    required_gates: list[str] | None = None,
) -> dict:
    return {
        "id": capability_id,
        "component": "shape",
        "renderMode": render_mode,
        "impact": "fidelity",
        "selectors": [
            {
                "partGlob": "ppt/slides/slide*.xml",
                "namespace": "http://schemas.openxmlformats.org/drawingml/2006/main",
                "localName": "prstGeom",
            }
        ],
        "scope": {"presets": ["rect"]},
        "fallback": fallback,
        "affectedPaths": ["src/shapes/presets.ts"],
        "requiredGates": required_gates or ["source", "unit", "browser", "docs"],
        "issueUrls": [],
    }


def test_registry_rejects_duplicate_capability_ids(tmp_path: Path):
    entry = capability("drawingml.shape.geometry.duplicate")
    path = write_json(
        tmp_path / "capabilities.json",
        {"schemaVersion": 2, "capabilities": [entry, entry]},
    )

    with pytest.raises(ValueError, match="duplicate capability id"):
        load_capability_registry(path)


def test_native_mode_requires_native_powerpoint_gate(tmp_path: Path):
    entry = capability(
        render_mode="native",
        fallback="none",
        required_gates=["source", "unit", "browser", "docs"],
    )
    path = write_json(
        tmp_path / "capabilities.json",
        {"schemaVersion": 2, "capabilities": [entry]},
    )

    with pytest.raises(ValueError, match="native-powerpoint"):
        load_capability_registry(path)


def test_non_native_mode_requires_a_fallback_description(tmp_path: Path):
    entry = capability(render_mode="approximate", fallback="")
    path = write_json(
        tmp_path / "capabilities.json",
        {"schemaVersion": 2, "capabilities": [entry]},
    )

    with pytest.raises(ValueError, match="fallback"):
        load_capability_registry(path)


def test_capability_accepts_explicit_verification_cases(tmp_path: Path):
    entry = capability(required_gates=["source", "unit", "native-powerpoint"])
    entry["verificationCases"] = ["oracle-chart-0001", "oracle-chart-0002"]
    registry = load_capability_registry(
        write_json(
            tmp_path / "capabilities.json",
            {"schemaVersion": 2, "capabilities": [entry]},
        )
    )

    assert registry.capabilities[0].verification_cases == (
        "oracle-chart-0001",
        "oracle-chart-0002",
    )


def test_capability_planning_mode_defaults_to_ranked_and_accepts_observation_only(
    tmp_path: Path,
):
    ranked_entry = capability("drawingml.shape.geometry.ranked")
    observation_entry = capability("drawingml.shape.geometry.scene-residual")
    observation_entry["planningMode"] = "observation-only"
    registry = load_capability_registry(
        write_json(
            tmp_path / "capabilities.json",
            {
                "schemaVersion": 2,
                "capabilities": [ranked_entry, observation_entry],
            },
        )
    )

    by_id = registry.by_id()
    assert by_id[ranked_entry["id"]].planning_mode == "ranked"
    assert by_id[observation_entry["id"]].planning_mode == "observation-only"


def test_capability_rejects_unknown_planning_mode(tmp_path: Path):
    entry = capability()
    entry["planningMode"] = "background-maybe"
    path = write_json(
        tmp_path / "capabilities.json",
        {"schemaVersion": 2, "capabilities": [entry]},
    )

    with pytest.raises(ValueError, match="planningMode"):
        load_capability_registry(path)


def test_registry_rejects_unknown_keys_and_unsafe_paths(tmp_path: Path):
    entry = capability()
    entry["renderMdoe"] = entry["renderMode"]
    typo_path = write_json(
        tmp_path / "typo.json",
        {"schemaVersion": 2, "capabilities": [entry]},
    )
    with pytest.raises(ValueError, match="unknown keys.*renderMdoe"):
        load_capability_registry(typo_path)

    unsafe_entry = capability()
    unsafe_entry["affectedPaths"] = ["../outside.ts"]
    unsafe_path = write_json(
        tmp_path / "unsafe.json",
        {"schemaVersion": 2, "capabilities": [unsafe_entry]},
    )
    with pytest.raises(ValueError, match="repository-relative"):
        load_capability_registry(unsafe_path)


def test_selector_parent_scope_is_parsed_immutably(tmp_path: Path):
    unscoped_entry = capability()
    scoped_entry = capability()
    scoped_entry["selectors"][0]["parent"] = {
        "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
        "localNames": ["spPr", "grpSpPr"],
    }
    unscoped_registry = load_capability_registry(
        write_json(
            tmp_path / "unscoped.json",
            {"schemaVersion": 2, "capabilities": [unscoped_entry]},
        )
    )
    scoped_registry = load_capability_registry(
        write_json(
            tmp_path / "scoped.json",
            {"schemaVersion": 2, "capabilities": [scoped_entry]},
        )
    )

    selector = scoped_registry.capabilities[0].selectors[0]
    assert selector.parent_namespace == scoped_entry["selectors"][0]["parent"]["namespace"]
    assert selector.parent_local_names == ("spPr", "grpSpPr")
    assert unscoped_registry.capabilities[0].selectors[0].parent_namespace is None


def test_selector_parent_scope_rejects_incomplete_or_duplicate_names(tmp_path: Path):
    incomplete = capability()
    incomplete["selectors"][0]["parent"] = {"localNames": ["spPr"]}
    with pytest.raises(ValueError, match="parent missing keys: namespace"):
        load_capability_registry(
            write_json(
                tmp_path / "incomplete-parent.json",
                {"schemaVersion": 2, "capabilities": [incomplete]},
            )
        )

    duplicate = capability()
    duplicate["selectors"][0]["parent"] = {
        "namespace": "urn:test",
        "localNames": ["spPr", "spPr"],
    }
    with pytest.raises(ValueError, match="localNames contains duplicates"):
        load_capability_registry(
            write_json(
                tmp_path / "duplicate-parent.json",
                {"schemaVersion": 2, "capabilities": [duplicate]},
            )
        )


def test_selector_ancestor_path_is_parsed_immutably(
    tmp_path: Path,
):
    unscoped_entry = capability()
    scoped_entry = capability()
    scoped_entry["selectors"][0]["ancestorPath"] = [
        {
            "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "localNames": ["sp"],
        },
        {
            "namespace": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "localNames": ["spPr"],
        },
    ]
    unscoped_registry = load_capability_registry(
        write_json(
            tmp_path / "unscoped.json",
            {"schemaVersion": 2, "capabilities": [unscoped_entry]},
        )
    )
    scoped_registry = load_capability_registry(
        write_json(
            tmp_path / "scoped.json",
            {"schemaVersion": 2, "capabilities": [scoped_entry]},
        )
    )

    selector = scoped_registry.capabilities[0].selectors[0]
    assert tuple(
        (step.namespace, step.local_names) for step in selector.ancestor_path
    ) == (
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("sp",),
        ),
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("spPr",),
        ),
    )
    assert unscoped_registry.capabilities[0].selectors[0].ancestor_path == ()


def test_selector_ancestor_path_rejects_empty_steps_and_parent_combination(tmp_path: Path):
    empty = capability()
    empty["selectors"][0]["ancestorPath"] = []
    with pytest.raises(ValueError, match="ancestorPath must be a non-empty list"):
        load_capability_registry(
            write_json(
                tmp_path / "empty-ancestor-path.json",
                {"schemaVersion": 2, "capabilities": [empty]},
            )
        )

    combined = capability()
    combined["selectors"][0]["parent"] = {
        "namespace": "urn:test",
        "localNames": ["spPr"],
    }
    combined["selectors"][0]["ancestorPath"] = [
        {"namespace": "urn:test", "localNames": ["sp", "spPr"]}
    ]
    with pytest.raises(ValueError, match="cannot combine parent and ancestorPath"):
        load_capability_registry(
            write_json(
                tmp_path / "combined-ancestor-parent.json",
                {"schemaVersion": 2, "capabilities": [combined]},
            )
        )


def test_acceptance_history_requires_known_capabilities_and_sha256(tmp_path: Path):
    registry_path = write_json(
        tmp_path / "capabilities.json",
        {"schemaVersion": 2, "capabilities": [capability()]},
    )
    registry = load_capability_registry(registry_path)

    unknown_path = write_json(
        tmp_path / "unknown-acceptance.json",
        {
            "schemaVersion": 2,
            "receipts": [
                {
                    "capabilityId": "drawingml.shape.geometry.unknown",
                    "acceptedRevision": "a" * 40,
                    "caseIds": ["oracle-shape-0001"],
                    "caseInputSha256": ["c" * 64],
                    "groundTruthSha256": ["d" * 64],
                    "gates": ["source", "unit"],
                    "environment": {"oracle": "powerpoint-macos"},
                    "acceptedAt": "2026-09-09T00:00:00Z",
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="unknown capability"):
        validate_acceptance_history(registry, load_acceptance_history(unknown_path))

    invalid_hash_path = write_json(
        tmp_path / "invalid-hash-acceptance.json",
        {
            "schemaVersion": 2,
            "receipts": [
                {
                    "capabilityId": "drawingml.shape.geometry.rect",
                    "acceptedRevision": "a" * 40,
                    "caseIds": ["oracle-shape-0001"],
                    "caseInputSha256": ["not-a-sha256"],
                    "groundTruthSha256": ["d" * 64],
                    "gates": ["source", "unit"],
                    "environment": {"oracle": "powerpoint-macos"},
                    "acceptedAt": "2026-09-09T00:00:00Z",
                }
            ],
        },
    )
    with pytest.raises(ValueError, match="caseInputSha256"):
        load_acceptance_history(invalid_hash_path)


def test_acceptance_history_keeps_only_one_receipt_per_capability(tmp_path: Path):
    receipt = {
        "capabilityId": "drawingml.shape.geometry.rect",
        "acceptedRevision": "b" * 40,
        "caseIds": ["oracle-shape-0001"],
        "caseInputSha256": ["d" * 64],
        "groundTruthSha256": ["e" * 64],
        "gates": ["source", "unit"],
        "environment": {"oracle": "powerpoint-macos"},
        "acceptedAt": "2026-09-09T00:00:00Z",
    }
    duplicate_capability_path = write_json(
        tmp_path / "duplicate-capability-acceptance.json",
        {
            "schemaVersion": 2,
            "receipts": [
                receipt,
                {
                    **receipt,
                    "acceptedRevision": "f" * 40,
                    "acceptedAt": "2026-09-09T01:00:00Z",
                },
            ],
        },
    )

    with pytest.raises(ValueError, match="one receipt per capability"):
        load_acceptance_history(duplicate_capability_path)


def test_valid_registry_and_acceptance_history_are_immutable(tmp_path: Path):
    entry = capability(
        render_mode="native",
        fallback="none",
        required_gates=["source", "unit", "browser", "native-powerpoint", "docs"],
    )
    registry = load_capability_registry(
        write_json(
            tmp_path / "capabilities.json",
            {"schemaVersion": 2, "capabilities": [entry]},
        )
    )
    history = load_acceptance_history(
        write_json(
            tmp_path / "capability-acceptance.json",
            {
                "schemaVersion": 2,
                "receipts": [
                    {
                        "capabilityId": entry["id"],
                        "acceptedRevision": "a" * 40,
                        "caseIds": ["oracle-shape-0001"],
                        "caseInputSha256": ["c" * 64],
                        "groundTruthSha256": ["d" * 64],
                        "gates": entry["requiredGates"],
                        "environment": {"oracle": "powerpoint-macos"},
                        "acceptedAt": "2026-09-09T00:00:00Z",
                    }
                ],
            },
        )
    )

    validate_acceptance_history(registry, history)
    assert registry.capabilities[0].scope == {"presets": ("rect",)}
    assert history.receipts[0].case_ids == ("oracle-shape-0001",)
    with pytest.raises(AttributeError):
        registry.capabilities[0].id = "changed"  # type: ignore[misc]


def test_tracked_capability_contract_is_valid():
    registry_path = Path("oracle/capabilities.json")
    history_path = Path("oracle/capability-acceptance.json")
    registry = load_capability_registry(registry_path)
    history = load_acceptance_history(history_path)

    validate_acceptance_history(registry, history)
    raw_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    raw_history = json.loads(history_path.read_text(encoding="utf-8"))
    assert all("affectedPaths" in item for item in raw_registry["capabilities"])
    legacy_fields = {
        "definitionFingerprint",
        "implementationFingerprint",
        "verificationFingerprint",
    }
    assert all(legacy_fields.isdisjoint(record) for record in raw_history["receipts"])
    assert len(registry.capabilities) == 21
    chart_2d = registry.by_id()["drawingml.chart.2d.common"]
    common_table = registry.by_id()["drawingml.table.common"]
    formula = registry.by_id()["drawingml.text.math.omml"]
    assert chart_2d.render_mode == "approximate"
    assert chart_2d.verification_cases == (
        "oracle-pypptx-chart-0002-column-negative-values",
        "oracle-pypptx-chart-0022-bar-negative-literal-zero-crossing",
    )
    assert chart_2d.scope["cartesianLocalEvidence"] == (
        "single-chart",
        "bar-column-line-area",
        "one-to-eight-series",
        "direct-chart-frame-roi",
        "chromatic-series",
        "neutral-axis-grid",
        "neutral-or-solid-plot-background",
    )
    assert "test/e2e/oracle/chart_metrics.py" in chart_2d.affected_paths
    assert common_table.render_mode == "native"
    assert formula.render_mode == "approximate"
    assert formula.scope["output"] == ("Presentation MathML",)
    assert "m:f" in formula.scope["directOmmlRendering"]
    camera_plane = registry.by_id()["drawingml.shape.3d.camera-projected-plane"]
    scene_residual = registry.by_id()["drawingml.shape.3d.scene"]
    text_scene_residual = registry.by_id()["drawingml.text.3d.scene"]
    entrance_fade = registry.by_id()["presentation.animation.entrance.fade"]
    timing_residual = registry.by_id()["presentation.animation.timing"]
    assert scene_residual.planning_mode == "observation-only"
    assert text_scene_residual.planning_mode == "observation-only"
    assert entrance_fade.planning_mode == "ranked"
    assert entrance_fade.selectors[0].attributes == {
        "presetClass": ("entr",),
        "presetID": ("10",),
        "presetSubtype": ("0",),
    }
    assert timing_residual.planning_mode == "observation-only"
    scene_selector = next(
        selector for selector in camera_plane.selectors if selector.local_name == "scene3d"
    )
    assert scene_selector.parent_local_names == ("spPr", "grpSpPr")
    assert camera_plane.scope["nodeKinds"] == ("shape", "picture", "group")
    assert "camera-local" in camera_plane.required_gates
    outer_shadow = registry.by_id()["drawingml.shape.effect.outer-shadow"]
    assert outer_shadow.render_mode == "native"
    assert "shadow-local" in outer_shadow.required_gates
    assert tuple(
        (step.namespace, step.local_names)
        for step in outer_shadow.selectors[0].ancestor_path
    ) == (
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("sp",),
        ),
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("spPr",),
        ),
        (
            "http://schemas.openxmlformats.org/drawingml/2006/main",
            ("effectLst",),
        ),
    )
    assert outer_shadow.scope["source"] == ("direct-shape-effect-list",)
    assert outer_shadow.scope["alignment"] == ("absent-default-b", "b", "ctr", "tr")
    assert outer_shadow.scope["scaleOoxmlPercent"] == (
        "absent-default-100000",
        92000,
        100000,
        102000,
    )
    assert outer_shadow.scope["groupScale"] == (
        "absent-standalone",
        "single-level-uniform-1.25",
    )
    assert outer_shadow.scope["rotWithShape"] == ("explicit-zero",)
    assert outer_shadow.scope["combinationPolicy"] == (
        "only-listed-verified-matrix-rows",
    )
    assert len(outer_shadow.scope["verifiedMatrixRows"]) == 7
    reflection = registry.by_id()["drawingml.shape.effect.reflection"]
    assert reflection.render_mode == "native"
    assert "reflection-local" in reflection.required_gates
    assert tuple(
        (step.namespace, step.local_names)
        for step in reflection.selectors[0].ancestor_path
    ) == (
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("sp",),
        ),
        (
            "http://schemas.openxmlformats.org/presentationml/2006/main",
            ("spPr",),
        ),
        (
            "http://schemas.openxmlformats.org/drawingml/2006/main",
            ("effectLst",),
        ),
    )
    assert reflection.scope["source"] == ("direct-shape-effect-list",)
    assert reflection.scope["alignment"] == ("bl",)
    assert reflection.scope["scaleYOoxmlPercent"] == (-100000,)
    assert reflection.scope["groupScale"] == (
        "absent-standalone",
        "single-level-uniform-1.25",
    )
    assert reflection.scope["rotWithShape"] == ("explicit-zero",)
    assert reflection.scope["combinationPolicy"] == (
        "only-listed-verified-matrix-rows",
    )
    assert len(reflection.scope["verifiedMatrixRows"]) == 6
    top_bevel = registry.by_id()["drawingml.shape.3d.top-bevel-contour"]
    assert top_bevel.scope["bevelPresetEncoding"] == (
        "explicit-circle",
        "implicit-circle-default",
    )
    assert top_bevel.scope["bevelDimensionEncoding"] == (
        "explicit-positive",
        "implicit-76200-emu-per-axis",
    )


def test_historical_receipt_needs_revision_and_testcase_evidence_only(tmp_path: Path):
    registry = load_capability_registry(
        write_json(
            tmp_path / "capabilities.json",
            {
                "schemaVersion": 2,
                "capabilities": [
                    capability(
                        render_mode="native",
                        fallback="none",
                        required_gates=["unit", "native-powerpoint"],
                    )
                ],
            },
        )
    )
    history = load_acceptance_history(
        write_json(
            tmp_path / "acceptance.json",
            {
                "schemaVersion": 2,
                "receipts": [
                    {
                        "capabilityId": registry.capabilities[0].id,
                        "acceptedRevision": "a" * 40,
                        "caseIds": ["oracle-shape-0001"],
                        "caseInputSha256": ["c" * 64],
                        "groundTruthSha256": ["d" * 64],
                        "gates": ["unit", "native-powerpoint"],
                        "environment": {"oracle": "powerpoint-macos"},
                        "acceptedAt": "2026-09-09T00:00:00Z",
                    }
                ],
            },
        )
    )

    validate_acceptance_history(registry, history)
    assert history.receipts[0].accepted_revision == "a" * 40
