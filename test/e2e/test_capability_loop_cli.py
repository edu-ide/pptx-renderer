import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from oracle.capability_contract import load_capability_registry


SCRIPT = Path(__file__).resolve().parent / "scripts" / "run_capability_loop.py"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
    )


def write_contract(repo: Path, *, render_mode: str = "fallback") -> tuple[Path, Path]:
    registry = repo / "capabilities.json"
    acceptance = repo / "capability-acceptance.json"
    required_gates = ["source", "unit", "browser", "docs"]
    if render_mode == "native":
        required_gates.insert(3, "native-powerpoint")
    registry.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "capabilities": [
                    {
                        "id": "drawingml.shape.geometry.donut",
                        "component": "shape",
                        "renderMode": render_mode,
                        "impact": "fidelity",
                        "selectors": [
                            {
                                "partGlob": "ppt/slides/slide*.xml",
                                "namespace": A_NS,
                                "localName": "prstGeom",
                                "attributes": {"prst": ["donut"]},
                            }
                        ],
                        "scope": {"presets": ["donut"]},
                        "fallback": "none" if render_mode == "native" else "Render flat geometry.",
                        "affectedPaths": [
                            "src/renderer/ShapeRenderer.ts",
                            "test/ShapeRenderer.test.ts",
                        ],
                        "requiredGates": required_gates,
                        "issueUrls": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    acceptance.write_text(
        json.dumps({"schemaVersion": 2, "receipts": []}),
        encoding="utf-8",
    )
    return registry, acceptance


def write_donut_pptx(path: Path) -> Path:
    slide = f"""
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="{A_NS}">
      <p:cSld><p:spTree><p:sp><p:spPr>
        <a:prstGeom prst="donut"><a:avLst/></a:prstGeom>
      </p:spPr></p:sp></p:spTree></p:cSld>
    </p:sld>
    """
    with ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
    return path


def create_repo(tmp_path: Path, *, render_mode: str = "fallback") -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    (repo / "src/renderer").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "src/renderer/ShapeRenderer.ts").write_text("renderer", encoding="utf-8")
    (repo / "test/ShapeRenderer.test.ts").write_text("test", encoding="utf-8")
    registry, acceptance = write_contract(repo, render_mode=render_mode)
    return repo, registry, acceptance


def write_native_evaluation(path: Path, case_id: str, revision: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "testFile": case_id,
                "evaluationErrorCount": 0,
                "evaluationErrors": [],
                "oracleMismatchCount": 0,
                "avgSsim": 0.99,
                "supported": True,
                "quality": {"passed": True, "needsReview": False},
                "provenance": {
                    "inputs": {
                        "sourcePptx": {"sha256": "c" * 64},
                        "groundTruth": {"kind": "png", "combinedSha256": "d" * 64},
                    },
                    "renderer": {"revision": revision, "dirty": False},
                    "runtime": {
                        "platform": "macOS-test",
                        "python": "3.11.11",
                        "browser": {"name": "chrome", "version": "152"},
                        "fontProfile": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_validate_rejects_invalid_contract_and_accepts_valid_contract(tmp_path: Path):
    repo, registry, acceptance = create_repo(tmp_path)
    valid = run_cli(
        "validate",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--acceptance",
        acceptance,
    )
    assert valid.returncode == 0, valid.stderr
    assert "validated 1 capabilities" in valid.stdout

    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["capabilities"][0]["renderMdoe"] = "native"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    invalid = run_cli(
        "validate",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--acceptance",
        acceptance,
    )
    assert invalid.returncode == 2
    assert "unknown keys" in invalid.stderr


def test_inventory_is_deterministic_and_rejects_unsafe_zip(tmp_path: Path):
    repo, registry, acceptance = create_repo(tmp_path)
    corpus = repo / "corpus"
    corpus.mkdir()
    write_donut_pptx(corpus / "donut.pptx")
    first = repo / "first.json"
    second = repo / "second.json"

    for output in (first, second):
        result = run_cli(
            "inventory",
            "--repo-root",
            repo,
            "--registry",
            registry,
            "--corpus",
            corpus,
            "--representative-alias",
            "corpus-0/donut.pptx",
            "--out",
            output,
        )
        assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes()
    classified = json.loads(first.read_text(encoding="utf-8"))
    assert "registryFingerprint" not in classified
    assert classified["packages"][0]["corpusRole"] == "representative"
    assert classified["corpusClassification"]["representativeUniquePackageCount"] == 1

    with ZipFile(corpus / "unsafe.pptx", "w") as archive:
        archive.writestr("../escape.xml", "<x/>")
    unsafe = run_cli(
        "inventory",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--corpus",
        corpus,
        "--out",
        repo / "unsafe.json",
        "--fail-on-rejected",
    )
    assert unsafe.returncode == 2
    assert "rejected 1 package" in unsafe.stderr
    rejected = json.loads((repo / "unsafe.json").read_text(encoding="utf-8"))
    assert rejected["rejectedPackageCount"] == 1
    assert rejected["rejectedPackages"][0]["reasonCode"] == "unsafe-zip-member"


def test_inventory_cli_accepts_validation_alias_globs(tmp_path: Path):
    repo, registry, _ = create_repo(tmp_path)
    corpus = repo / "corpus"
    ordinary = corpus / "customer-deck"
    oracle = corpus / "oracle-generated-case"
    ordinary.mkdir(parents=True)
    oracle.mkdir(parents=True)
    write_donut_pptx(ordinary / "source.pptx")
    write_donut_pptx(oracle / "source.pptx")
    with ZipFile(oracle / "source.pptx", "a") as archive:
        archive.writestr("validation-marker.txt", "oracle")
    output = repo / "inventory.json"

    result = run_cli(
        "inventory",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--corpus",
        corpus,
        "--validation-alias",
        "corpus-0/oracle-*",
        "--out",
        output,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    roles = {
        package["aliases"][0]: package["corpusRole"] for package in payload["packages"]
    }
    assert roles == {
        "corpus-0/customer-deck/source.pptx": "representative",
        "corpus-0/oracle-generated-case/source.pptx": "validation",
    }


def test_default_inventory_classifies_oracle_named_cases_as_validation(tmp_path: Path):
    repo, registry, _ = create_repo(tmp_path)
    corpus = repo / "test/e2e/testdata/cases"
    ordinary = corpus / "customer-deck"
    oracle = corpus / "oracle-generated-case"
    ordinary.mkdir(parents=True)
    oracle.mkdir(parents=True)
    write_donut_pptx(ordinary / "source.pptx")
    write_donut_pptx(oracle / "source.pptx")
    with ZipFile(oracle / "source.pptx", "a") as archive:
        archive.writestr("validation-marker.txt", "oracle")
    output = repo / "inventory.json"

    result = run_cli(
        "inventory",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--out",
        output,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["corpusClassification"] == {
        "mode": "default-testdata-case-convention",
        "representativeAliasGlobs": [],
        "validationAliasGlobs": ["corpus-0/*oracle-*"],
        "representativeUniquePackageCount": 1,
        "validationUniquePackageCount": 1,
    }


def test_rank_and_work_packet_keep_unreproduced_issue_out_of_demand(tmp_path: Path):
    repo, registry, acceptance = create_repo(tmp_path)
    corpus = repo / "corpus"
    corpus.mkdir()
    write_donut_pptx(corpus / "donut.pptx")
    inventory = repo / "inventory.json"
    assert (
        run_cli(
            "inventory",
            "--repo-root",
            repo,
            "--registry",
            registry,
            "--corpus",
            corpus,
            "--out",
            inventory,
        ).returncode
        == 0
    )
    issues = repo / "issues.json"
    issues.write_text(
        json.dumps(
            [
                {
                    "number": 99,
                    "url": "https://github.com/example/repo/issues/99",
                    "title": "Unverified report",
                }
            ]
        ),
        encoding="utf-8",
    )
    ledger = repo / "ledger.json"
    ranking = repo / "ranking.json"

    ranked = run_cli(
        "rank",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--inventory",
        inventory,
        "--issues",
        issues,
        "--acceptance",
        acceptance,
        "--ledger-out",
        ledger,
        "--ranking-out",
        ranking,
    )
    assert ranked.returncode == 0, ranked.stderr
    ledger_payload = json.loads(ledger.read_text(encoding="utf-8"))
    ranking_payload = json.loads(ranking.read_text(encoding="utf-8"))
    assert "registryFingerprint" not in ledger_payload
    assert "registryFingerprint" not in ranking_payload
    row = ledger_payload["rows"][0]
    assert row["observedUniquePackages"] == 1
    assert row["currentIssueCount"] == 0

    packet = repo / "work-packet.json"
    built = run_cli(
        "work-packet",
        "--repo-root",
        repo,
        "--registry",
        registry,
        "--ledger",
        ledger,
        "--capability",
        "drawingml.shape.geometry.donut",
        "--out",
        packet,
    )
    assert built.returncode == 0, built.stderr
    assert json.loads(packet.read_text(encoding="utf-8"))["capabilityId"] == (
        "drawingml.shape.geometry.donut"
    )


def test_accept_writes_fresh_receipt_and_rejects_dirty_repo(tmp_path: Path):
    repo, registry_path, acceptance = create_repo(tmp_path, render_mode="native")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    registry = load_capability_registry(registry_path)
    capability = registry.capabilities[0]
    report = {
        "schemaVersion": 2,
        "capabilityId": capability.id,
        "renderer": {
            "revision": revision,
            "dirty": False,
        },
        "environment": {"oracle": "powerpoint-macos"},
        "gates": {gate: "passed" for gate in capability.required_gates},
        "caseResults": [
            {
                "caseId": "oracle-shape-0001",
                "sourceSha256": "c" * 64,
                "groundTruthSha256": "d" * 64,
                "skipped": False,
                "passed": True,
                "needsReview": False,
                "manualVerdict": "not-required",
                "runtimeErrors": [],
            }
        ],
    }
    verification = tmp_path / "verification.json"
    verification.write_text(json.dumps(report), encoding="utf-8")

    accepted = run_cli(
        "accept",
        "--repo-root",
        repo,
        "--registry",
        registry_path,
        "--acceptance",
        acceptance,
        "--verification",
        verification,
        "--capability",
        capability.id,
        "--accepted-at",
        "2026-09-09T01:02:03Z",
    )
    assert accepted.returncode == 0, accepted.stderr
    payload = json.loads(acceptance.read_text(encoding="utf-8"))
    assert payload["receipts"][0]["acceptedRevision"] == revision

    subprocess.run(["git", "checkout", "--", acceptance.name], cwd=repo, check=True)
    (repo / "src/renderer/ShapeRenderer.ts").write_text("dirty", encoding="utf-8")
    rejected = run_cli(
        "accept",
        "--repo-root",
        repo,
        "--registry",
        registry_path,
        "--acceptance",
        acceptance,
        "--verification",
        verification,
        "--capability",
        capability.id,
        "--accepted-at",
        "2026-09-09T01:02:04Z",
    )
    assert rejected.returncode == 2
    assert "clean repository" in rejected.stderr
    assert json.loads(acceptance.read_text(encoding="utf-8"))["receipts"] == []


def test_accept_replaces_the_previous_receipt_for_the_same_capability(tmp_path: Path):
    repo, registry_path, acceptance = create_repo(tmp_path, render_mode="native")
    registry = load_capability_registry(registry_path)
    capability = registry.capabilities[0]
    acceptance.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "receipts": [
                    {
                        "capabilityId": capability.id,
                        "acceptedRevision": "a" * 40,
                        "caseIds": ["oracle-shape-0001"],
                        "caseInputSha256": ["c" * 64],
                        "groundTruthSha256": ["d" * 64],
                        "gates": list(capability.required_gates),
                        "environment": {"oracle": "powerpoint-macos"},
                        "acceptedAt": "2026-09-09T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    verification = tmp_path / "verification.json"
    verification.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "capabilityId": capability.id,
                "renderer": {
                    "revision": revision,
                    "dirty": False,
                },
                "environment": {"oracle": "powerpoint-macos"},
                "gates": {gate: "passed" for gate in capability.required_gates},
                "caseResults": [
                    {
                        "caseId": "oracle-shape-0001",
                        "sourceSha256": "e" * 64,
                        "groundTruthSha256": "f" * 64,
                        "skipped": False,
                        "passed": True,
                        "needsReview": False,
                        "manualVerdict": "not-required",
                        "runtimeErrors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    accepted = run_cli(
        "accept",
        "--repo-root",
        repo,
        "--registry",
        registry_path,
        "--acceptance",
        acceptance,
        "--verification",
        verification,
        "--capability",
        capability.id,
        "--accepted-at",
        "2026-09-09T01:02:03Z",
    )

    assert accepted.returncode == 0, accepted.stderr
    receipts = json.loads(acceptance.read_text(encoding="utf-8"))["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["acceptedRevision"] == revision


def test_verify_normalizes_api_output_at_the_current_clean_revision(tmp_path: Path):
    repo, registry_path, _acceptance = create_repo(tmp_path, render_mode="native")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    raw = write_native_evaluation(tmp_path / "native.json", "donut-thin", revision)
    output = tmp_path / "verification.json"

    result = run_cli(
        "verify",
        "--repo-root",
        repo,
        "--registry",
        registry_path,
        "--capability",
        "drawingml.shape.geometry.donut",
        "--case-report",
        raw,
        "--oracle",
        "powerpoint-macos",
        "--passed-gate",
        "source",
        "--passed-gate",
        "unit",
        "--passed-gate",
        "browser",
        "--passed-gate",
        "docs",
        "--out",
        output,
    )

    assert result.returncode == 0, result.stderr
    verification = json.loads(output.read_text(encoding="utf-8"))
    assert verification["renderer"]["revision"] == revision
    assert set(verification["gates"].values()) == {"passed"}
    assert verification["caseResults"][0]["caseId"] == "donut-thin"


def test_tracked_cli_contract_validates_current_repository():
    project_root = Path(__file__).resolve().parents[2]
    result = run_cli("validate", "--repo-root", project_root)

    assert result.returncode == 0, result.stderr
    assert "validated 21 capabilities" in result.stdout
