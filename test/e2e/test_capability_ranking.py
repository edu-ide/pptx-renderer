from pathlib import Path

import pytest

from oracle.capability_contract import load_capability_registry
from oracle.capability_ranking import (
    LedgerRow,
    build_ledger_rows,
    build_work_packet,
    ledger_row_from_dict,
    ledger_row_to_dict,
    rank_capabilities,
    select_ranked_capability,
)


def row(
    capability_id: str,
    *,
    impact: str = "fidelity",
    current_issues: int = 0,
    observed: int = 0,
    representative: int | None = None,
    failure: str = "none",
    oracle_ready: bool = False,
    dependency_depth: int = 0,
    evidence_state: str = "observed",
    render_mode: str = "fallback",
    blockers: tuple[str, ...] = (),
    planning_mode: str | None = None,
) -> LedgerRow:
    values = dict(
        capability_id=capability_id,
        impact=impact,
        current_issue_count=current_issues,
        observed_unique_packages=observed,
        observed_representative_packages=(
            observed if representative is None else representative
        ),
        failure_kind=failure,
        oracle_ready=oracle_ready,
        dependency_depth=dependency_depth,
        evidence_state=evidence_state,
        render_mode=render_mode,
        blockers=blockers,
        issue_urls=(),
    )
    if planning_mode is not None:
        values["planning_mode"] = planning_mode
    return LedgerRow(**values)


def test_missing_real_deck_feature_precedes_unobserved_enhancement():
    ranked = rank_capabilities(
        [
            row("presentation.animation.timing", impact="enhancement", observed=0),
            row("drawingml.shape.3d.top-bevel-contour", impact="missing", observed=3),
        ]
    )

    assert [item.capability_id for item in ranked] == [
        "drawingml.shape.3d.top-bevel-contour",
        "presentation.animation.timing",
    ]


def test_ranking_uses_documented_lexicographic_order():
    ranked = rank_capabilities(
        [
            row("cap.f", current_issues=1, observed=2, failure="review-warning"),
            row("cap.e", current_issues=1, observed=2, failure="native-failure"),
            row("cap.d", current_issues=1, observed=2, failure="runtime-error"),
            row("cap.c", current_issues=1, observed=3, failure="none"),
            row("cap.b", current_issues=2, observed=0, failure="none"),
            row("cap.a", impact="semantics", current_issues=0, observed=0),
        ]
    )

    assert [item.capability_id for item in ranked] == [
        "cap.a",
        "cap.b",
        "cap.c",
        "cap.d",
        "cap.e",
        "cap.f",
    ]
    assert ranked[1].priority_labels[:4] == (
        ("impact", "fidelity"),
        ("currentIssueCount", "2"),
        ("observedRepresentativePackages", "0"),
        ("observedUniquePackages", "0"),
    )


def test_representative_corpus_precedes_generated_validation_volume():
    common = {
        "impact": "fidelity",
        "current_issue_count": 0,
        "failure_kind": "none",
        "oracle_ready": False,
        "dependency_depth": 0,
        "evidence_state": "observed",
        "render_mode": "fallback",
        "blockers": (),
        "issue_urls": (),
    }
    ranked = rank_capabilities(
        [
            LedgerRow(
                capability_id="cap.generated-heavy",
                observed_unique_packages=20,
                observed_representative_packages=0,
                **common,
            ),
            LedgerRow(
                capability_id="cap.representative",
                observed_unique_packages=2,
                observed_representative_packages=2,
                **common,
            ),
        ]
    )

    assert [item.capability_id for item in ranked] == [
        "cap.representative",
        "cap.generated-heavy",
    ]
    assert ranked[0].priority_labels[2:4] == (
        ("observedRepresentativePackages", "2"),
        ("observedUniquePackages", "2"),
    )


def test_legacy_ledger_rows_treat_all_observations_as_representative():
    payload = ledger_row_to_dict(row("cap.legacy", observed=3))
    del payload["observedRepresentativePackages"]

    restored = ledger_row_from_dict(payload)

    assert restored.observed_representative_packages == 3


def test_ledger_counts_representative_and_validation_observations_separately():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    capability_id = "drawingml.shape.3d.scene"
    inventory = {
        "packages": [
            {
                "packageId": "sha256:" + "a" * 64,
                "sha256": "a" * 64,
                "corpusRole": "representative",
                "capabilityIds": [capability_id],
            },
            {
                "packageId": "sha256:" + "b" * 64,
                "sha256": "b" * 64,
                "corpusRole": "validation",
                "capabilityIds": [capability_id],
            },
        ]
    }

    rows = build_ledger_rows(registry, inventory)
    scene = {item.capability_id: item for item in rows}[capability_id]

    assert scene.observed_unique_packages == 2
    assert scene.observed_representative_packages == 1
    assert scene.planning_mode == "observation-only"


def test_oracle_readiness_then_dependency_depth_then_id_break_ties():
    ranked = rank_capabilities(
        [
            row("cap.d", oracle_ready=False, dependency_depth=0),
            row("cap.c", oracle_ready=True, dependency_depth=2),
            row("cap.b", oracle_ready=True, dependency_depth=1),
            row("cap.a", oracle_ready=True, dependency_depth=1),
        ]
    )

    assert [item.capability_id for item in ranked] == ["cap.a", "cap.b", "cap.c", "cap.d"]


def test_unknown_historical_verified_and_blocked_rows_are_watched_but_not_selected():
    ranked = rank_capabilities(
        [
            row("cap.a", evidence_state="unknown"),
            row("cap.b", evidence_state="verified"),
            row("cap.c", evidence_state="blocked"),
            row("cap.d", evidence_state="regressed"),
            row("cap.e", evidence_state="historical"),
        ]
    )

    assert [item.capability_id for item in ranked] == ["cap.d"]


def test_observation_only_rows_remain_visible_but_are_not_executable():
    observation = row(
        "cap.scene-residual",
        observed=20,
        representative=5,
        planning_mode="observation-only",
    )
    executable = row("cap.next-bounded-family", observed=1)

    restored = ledger_row_from_dict(ledger_row_to_dict(observation))
    ranked = rank_capabilities([observation, executable])

    assert restored.planning_mode == "observation-only"
    assert [item.capability_id for item in ranked] == ["cap.next-bounded-family"]


def test_ranking_rejects_invalid_or_duplicate_rows():
    with pytest.raises(ValueError, match="duplicate ledger capability"):
        rank_capabilities([row("cap.a"), row("cap.a")])
    with pytest.raises(ValueError, match="non-negative"):
        rank_capabilities([row("cap.a", observed=-1)])
    with pytest.raises(ValueError, match="must not exceed"):
        rank_capabilities([row("cap.a", observed=1, representative=2)])
    with pytest.raises(ValueError, match="failure kind"):
        rank_capabilities([row("cap.a", failure="mystery")])


def test_non_top_selection_requires_and_records_a_reason():
    ranked = rank_capabilities([row("cap.a", observed=2), row("cap.b", observed=1)])

    with pytest.raises(ValueError, match="selection reason"):
        select_ranked_capability(ranked, "cap.b")

    selected, selection = select_ranked_capability(
        ranked,
        "cap.b",
        "active-goal:prove-adjustment-cohort",
    )
    assert selected.capability_id == "cap.b"
    assert selection == {
        "rank": 2,
        "topRanked": False,
        "reason": "active-goal:prove-adjustment-cohort",
    }


def test_work_packet_contains_one_bounded_donut_matrix():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    selected = rank_capabilities(
        [
            row(
                "drawingml.shape.geometry.adjustment.donut",
                observed=2,
                failure="native-failure",
                oracle_ready=True,
                blockers=("refresh-current-native-report",),
            )
        ]
    )[0]

    packet = build_work_packet(selected, registry)

    assert packet["schemaVersion"] == 1
    assert packet["capabilityId"] == "drawingml.shape.geometry.adjustment.donut"
    assert packet["acceptedScope"]["bounds"] == [0, 50000]
    assert packet["caseMatrix"]["adjustment"] == [0, 25000, 50000]
    assert packet["caseMatrix"]["aspectRatio"] == ["square", "wide", "tall"]
    assert packet["caseMatrix"]["container"] == [
        "standalone",
        "grouped",
        "picture-clip-sentinel",
    ]
    assert packet["observations"]["observedRepresentativePackages"] == 2
    assert packet["blockers"] == ["refresh-current-native-report"]
    assert packet["rollbackConditions"]
    assert packet["requiredGates"] == list(
        registry.by_id()[selected.capability_id].required_gates
    )


def test_work_packet_contains_bounded_outer_shadow_matrix_without_placeholders():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    selected = rank_capabilities(
        [
            row(
                "drawingml.shape.effect.outer-shadow",
                observed=6,
                oracle_ready=False,
                blockers=("oracle-report:missing-or-unmapped",),
            )
        ]
    )[0]

    packet = build_work_packet(selected, registry)

    assert packet["caseMatrix"]["caseId"] == [
        "no-shadow-inverse",
        "rect-blur-defaults",
        "wide-roundrect-common-offset",
        "tall-ellipse-directional-offset",
        "rect-uniform-scale-102",
        "rect-uniform-scale-92-top-right",
        "grouped-roundrect",
        "scheme-color-modifiers",
    ]
    assert packet["caseMatrix"]["container"] == ["standalone", "grouped"]
    assert packet["caseMatrix"]["geometry"] == ["rect", "roundRect", "ellipse"]
    assert "placeholder-parent" not in packet["caseMatrix"]["container"]


def test_work_packet_contains_bounded_reflection_matrix_without_text_or_pictures():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    selected = rank_capabilities(
        [
            row(
                "drawingml.shape.effect.reflection",
                observed=6,
                oracle_ready=False,
                blockers=("oracle-report:missing-or-unmapped",),
            )
        ]
    )[0]

    packet = build_work_packet(selected, registry)

    assert packet["caseMatrix"]["caseId"] == [
        "no-reflection-inverse",
        "rect-common-alpha-fade",
        "wide-roundrect-common-alpha-fade",
        "tall-gradient-ellipse",
        "wide-gradient-arrow-broad-blur",
        "grouped-roundrect",
        "rect-distance-neighbor",
    ]
    assert packet["caseMatrix"]["container"] == ["standalone", "grouped"]
    assert packet["caseMatrix"]["geometry"] == [
        "rect",
        "roundRect",
        "ellipse",
        "upArrow",
    ]
    assert packet["caseMatrix"]["paint"] == ["solid", "simple-gradient"]


def test_work_packet_contains_bounded_entrance_fade_matrix():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    selected = rank_capabilities(
        [
            row(
                "presentation.animation.entrance.fade",
                impact="enhancement",
                observed=1,
                oracle_ready=False,
                blockers=("oracle-report:missing-or-unmapped",),
            )
        ]
    )[0]

    packet = build_work_packet(selected, registry)

    assert packet["caseMatrix"]["caseId"] == [
        "shape-click-fade-500ms",
        "shape-with-effect-fade-500ms",
        "shape-after-effect-excluded",
        "picture-target-excluded",
        "group-target-excluded",
        "paragraph-range-excluded",
        "non-fade-inverse",
    ]
    assert packet["caseMatrix"]["trigger"] == ["clickEffect", "withEffect"]
    assert packet["caseMatrix"]["timelineSample"] == [
        "initial-hidden",
        "midpoint-opacity",
        "completed-visible",
        "replay-reset",
    ]


def test_oracle_row_with_uninventoried_source_hash_is_stale():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    capability_id = "drawingml.shape.geometry.adjustment.donut"
    inventory = {
        "renderer": {"revision": "a" * 40, "dirty": False},
        "packages": [
            {
                "sha256": "b" * 64,
                "capabilityIds": [capability_id],
            }
        ],
    }
    oracle_report = {
        "results": [
            {
                "capabilityId": capability_id,
                "passed": True,
                "provenance": {
                    "renderer": {"revision": "a" * 40, "dirty": False},
                    "inputs": {
                        "sourcePptx": {"sha256": "c" * 64},
                        "groundTruth": {"combinedSha256": "d" * 64},
                    },
                },
            }
        ]
    }

    rows = build_ledger_rows(registry, inventory, oracle_report=oracle_report)
    row_by_id = {item.capability_id: item for item in rows}

    assert row_by_id[capability_id].oracle_ready is False
    assert row_by_id[capability_id].blockers == ("oracle-report:stale",)


def test_accepted_state_is_preserved_until_fresh_failure_regresses_it():
    registry = load_capability_registry(Path("oracle/capabilities.json"))
    capability_id = "drawingml.shape.geometry.adjustment.donut"
    inventory = {
        "renderer": {"revision": "a" * 40, "dirty": False},
        "packages": [{"sha256": "c" * 64, "capabilityIds": [capability_id]}],
    }
    verified = build_ledger_rows(
        registry,
        inventory,
        accepted_states={capability_id: "verified"},
    )
    assert {item.capability_id: item for item in verified}[capability_id].evidence_state == "verified"

    failing_report = {
        "results": [
            {
                "capabilityId": capability_id,
                "passed": False,
                "provenance": {
                    "renderer": {"revision": "a" * 40, "dirty": False},
                    "inputs": {
                        "sourcePptx": {"sha256": "c" * 64},
                        "groundTruth": {"combinedSha256": "d" * 64},
                    },
                },
            }
        ]
    }
    regressed = build_ledger_rows(
        registry,
        inventory,
        oracle_report=failing_report,
        accepted_states={capability_id: "verified"},
    )
    assert {item.capability_id: item for item in regressed}[capability_id].evidence_state == (
        "regressed"
    )
