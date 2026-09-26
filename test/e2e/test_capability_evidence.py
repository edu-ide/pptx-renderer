import json
import subprocess
from pathlib import Path

import pytest

from oracle.capability_contract import (
    PromotionReceipt,
    load_capability_registry,
)
from oracle.capability_evidence import (
    build_promotion_receipt,
    evaluate_evidence_state,
    sanitize_receipt_for_tracking,
)


SOURCE_HASH = "c" * 64
GROUND_TRUTH_HASH = "d" * 64


def build_repo_fixture(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for relative_path, contents in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    return repo


def load_capability(tmp_path: Path):
    payload = {
        "schemaVersion": 2,
        "capabilities": [
            {
                "id": "drawingml.shape.geometry.rect",
                "component": "shape",
                "renderMode": "native",
                "impact": "fidelity",
                "selectors": [],
                "scope": {"presets": ["rect"]},
                "fallback": "none",
                "affectedPaths": [
                    "src/renderer/ShapeRenderer.ts",
                    "test/unit/renderer/ShapeRenderer.test.ts",
                ],
                "requiredGates": [
                    "source",
                    "structural",
                    "unit",
                    "browser",
                    "native-powerpoint",
                    "manual-visual",
                    "docs",
                ],
                "issueUrls": [],
            }
        ],
    }
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_capability_registry(path).capabilities[0]


def fresh_native_report(capability, revision: str) -> dict:
    return {
        "schemaVersion": 2,
        "capabilityId": capability.id,
        "renderer": {
            "revision": revision,
            "dirty": False,
        },
        "environment": {
            "oracle": "powerpoint-macos",
            "browser": {"name": "chromium", "version": "140"},
        },
        "gates": {gate: "passed" for gate in capability.required_gates},
        "caseResults": [
            {
                "caseId": "oracle-shape-0001",
                "sourceSha256": SOURCE_HASH,
                "groundTruthSha256": GROUND_TRUTH_HASH,
                "skipped": False,
                "passed": True,
                "needsReview": False,
                "manualVerdict": "not-required",
                "runtimeErrors": [],
            }
        ],
    }


def accepted_receipt(capability, revision: str) -> PromotionReceipt:
    return PromotionReceipt(
        capability_id=capability.id,
        accepted_revision=revision,
        case_ids=("oracle-shape-0001",),
        case_input_sha256=(SOURCE_HASH,),
        ground_truth_sha256=(GROUND_TRUTH_HASH,),
        gates=capability.required_gates,
        environment={"oracle": "powerpoint-macos"},
        accepted_at="2026-09-09T00:00:00Z",
    )


@pytest.fixture
def evidence_fixture(tmp_path: Path):
    capability = load_capability(tmp_path)
    repo = build_repo_fixture(
        tmp_path,
        {
            "src/renderer/ShapeRenderer.ts": "renderer",
            "test/unit/renderer/ShapeRenderer.test.ts": "test",
            "docs/README.md": "unrelated",
        },
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return capability, repo, revision


def test_matching_receipt_and_fresh_evidence_yield_verified(evidence_fixture):
    capability, repo, revision = evidence_fixture

    state = evaluate_evidence_state(
        capability,
        accepted_receipt(capability, revision),
        fresh_native_report(capability, revision),
        repo,
    )

    assert state.name == "verified"
    assert state.reasons == ()


def test_uncommitted_change_invalidates_report_without_hashing_code(evidence_fixture):
    capability, repo, revision = evidence_fixture
    receipt = accepted_receipt(capability, revision)
    report = fresh_native_report(capability, revision)
    (repo / "src/renderer/ShapeRenderer.ts").write_text("changed", encoding="utf-8")

    state = evaluate_evidence_state(capability, receipt, report, repo)

    assert state.name == "regressed"
    assert state.reasons == ("evidence:current-worktree-not-clean",)


def test_passing_current_report_is_verified_without_a_receipt(evidence_fixture):
    capability, repo, revision = evidence_fixture

    state = evaluate_evidence_state(
        capability,
        None,
        fresh_native_report(capability, revision),
        repo,
    )

    assert state.name == "verified"
    assert state.reasons == ()


def test_receipt_without_current_report_is_historical_only(evidence_fixture):
    capability, repo, revision = evidence_fixture

    state = evaluate_evidence_state(
        capability,
        accepted_receipt(capability, revision),
        None,
        repo,
    )

    assert state.name == "historical"
    assert state.reasons == ("evidence:historical-verification-record",)


def test_dirty_report_cannot_be_promoted(evidence_fixture):
    capability, repo, revision = evidence_fixture
    report = fresh_native_report(capability, revision)
    report["renderer"]["dirty"] = True

    state = evaluate_evidence_state(capability, None, report, repo)

    assert state.name == "candidate"
    assert state.reasons == ("evidence:dirty-worktree",)


def test_missing_input_hash_stops_at_reproducible(evidence_fixture):
    capability, repo, revision = evidence_fixture
    report = fresh_native_report(capability, revision)
    report["caseResults"][0].pop("sourceSha256")

    state = evaluate_evidence_state(capability, None, report, repo)

    assert state.name == "reproducible"
    assert state.reasons == ("evidence:missing-input-hash",)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda report: report["caseResults"][0].update(skipped=True),
            "evidence:skipped-required-case",
        ),
        (
            lambda report: report["gates"].update(structural="failed"),
            "gate:structural:failed",
        ),
        (
            lambda report: report["caseResults"][0].update(
                needsReview=True,
                manualVerdict=None,
            ),
            "evidence:manual-review-required",
        ),
    ],
)
def test_required_verification_failures_prevent_promotion(evidence_fixture, mutate, reason):
    capability, repo, revision = evidence_fixture
    report = fresh_native_report(capability, revision)
    mutate(report)

    state = evaluate_evidence_state(capability, None, report, repo)

    assert state.name == "candidate"
    assert reason in state.reasons


def test_build_and_sanitize_promotion_receipt(evidence_fixture):
    capability, repo, revision = evidence_fixture
    report = fresh_native_report(capability, revision)
    report["environment"]["privateCasePath"] = "/Users/example/private/source.pptx"
    report["environment"]["username"] = "example"
    report["environment"]["windowsPath"] = "C:\\Users\\example\\source.pptx"

    receipt = build_promotion_receipt(capability, report, repo, "2026-09-09T01:02:03Z")
    tracked = sanitize_receipt_for_tracking(receipt)

    assert receipt.accepted_revision == revision
    assert "definitionFingerprint" not in tracked
    assert "implementationFingerprint" not in tracked
    assert tracked["caseIds"] == ["oracle-shape-0001"]
    assert tracked["caseInputSha256"] == [SOURCE_HASH]
    assert tracked["groundTruthSha256"] == [GROUND_TRUTH_HASH]
    assert "privateCasePath" not in json.dumps(tracked)
    assert "/Users/example" not in json.dumps(tracked)
    assert "C:\\\\Users" not in json.dumps(tracked)
