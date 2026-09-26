import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

from oracle.verification_impact import build_verification_plan
from scripts.verify_affected import (
    _discover_changed_paths,
    _is_release_metadata_package_change,
    _native_case_hashes,
    _run_commands,
)


def _capability(
    capability_id: str,
    *,
    affected_paths: list[str],
    required_gates: list[str],
) -> dict:
    return {
        "id": capability_id,
        "affectedPaths": affected_paths,
        "requiredGates": required_gates,
    }


def _cmd(*argv: str, cwd: str = ".") -> dict:
    return {"argv": list(argv), "cwd": cwd}


def test_docs_only_change_skips_runtime_and_native_verification():
    plan = build_verification_plan(
        changed_paths=["README.md", "docs/TESTING.md"],
        capabilities=[
            _capability(
                "drawingml.chart.2d.common",
                affected_paths=[
                    "README.md",
                    "src/renderer/ChartRenderer.ts",
                    "test/unit/renderer/ChartRenderer.test.ts",
                ],
                required_gates=["unit", "browser", "native-powerpoint", "docs"],
            )
        ],
        latest_case_ids={"drawingml.chart.2d.common": ["oracle-chart-0001"]},
        repository_paths=[
            "test/unit/build/browserDistribution.test.ts",
            "test/unit/docs/geometryDocs.test.ts",
            "test/unit/docs/securityDocs.test.ts",
        ],
    )

    assert plan["mode"] == "docs-only"
    assert plan["impactedCapabilityIds"] == []
    assert plan["nativeCaseIds"] == []
    assert plan["commands"] == [
        _cmd("pnpm", "exec", "prettier", "--check", "README.md", "docs/TESTING.md"),
        _cmd(
            "pnpm",
            "exec",
            "vitest",
            "run",
            "test/unit/build/browserDistribution.test.ts",
            "test/unit/docs/geometryDocs.test.ts",
            "test/unit/docs/securityDocs.test.ts",
        ),
    ]


def test_third_party_notice_change_runs_its_distribution_contract():
    plan = build_verification_plan(
        changed_paths=["THIRD_PARTY_NOTICES.md"],
        capabilities=[],
        latest_case_ids={},
        repository_paths=[
            "test/unit/build/browserDistribution.test.ts",
            "test/unit/docs/geometryDocs.test.ts",
            "test/unit/docs/securityDocs.test.ts",
        ],
    )

    assert plan["mode"] == "docs-only"
    assert "test/unit/build/browserDistribution.test.ts" in plan["unitTestPaths"]
    assert plan["commands"][1] == _cmd(
        "pnpm",
        "exec",
        "vitest",
        "run",
        "test/unit/build/browserDistribution.test.ts",
        "test/unit/docs/geometryDocs.test.ts",
        "test/unit/docs/securityDocs.test.ts",
    )


def test_renderer_change_selects_only_claiming_capabilities_and_their_tests_and_cases():
    plan = build_verification_plan(
        changed_paths=["src/renderer/ChartRenderer.ts"],
        capabilities=[
            _capability(
                "drawingml.chart.2d.common",
                affected_paths=[
                    "src/renderer/ChartRenderer.ts",
                    "test/unit/renderer/ChartRenderer.test.ts",
                    "test/e2e/test_chart_metrics.py",
                ],
                required_gates=["unit", "browser", "native-powerpoint"],
            ),
            _capability(
                "drawingml.shape.3d.camera-projected-plane",
                affected_paths=[
                    "src/renderer/Shape3DRenderer.ts",
                    "test/unit/renderer/Shape3DRenderer.test.ts",
                ],
                required_gates=["unit", "browser", "native-powerpoint"],
            ),
        ],
        latest_case_ids={
            "drawingml.chart.2d.common": ["oracle-chart-0001", "oracle-chart-0002"],
            "drawingml.shape.3d.camera-projected-plane": ["oracle-shape3d-0013"],
        },
        python_executable="python-for-test",
    )

    assert plan["mode"] == "targeted"
    assert plan["impactedCapabilityIds"] == ["drawingml.chart.2d.common"]
    assert plan["unitTestPaths"] == ["test/unit/renderer/ChartRenderer.test.ts"]
    assert plan["pythonTestPaths"] == ["test/e2e/test_chart_metrics.py"]
    assert plan["nativeCaseIds"] == ["oracle-chart-0001", "oracle-chart-0002"]
    assert plan["requiresBrowser"] is True
    assert plan["nativeCapabilitiesWithoutCases"] == []
    assert plan["commands"] == [
        _cmd("pnpm", "exec", "vitest", "run", "test/unit/renderer/ChartRenderer.test.ts"),
        _cmd("python-for-test", "-m", "pytest", "test_chart_metrics.py", "-q", cwd="test/e2e"),
        _cmd("pnpm", "typecheck"),
    ]


def test_native_gate_without_accepted_cases_is_reported_instead_of_silently_skipped():
    plan = build_verification_plan(
        changed_paths=["src/renderer/ChartRenderer.ts"],
        capabilities=[
            _capability(
                "drawingml.chart.3d.view",
                affected_paths=["src/renderer/ChartRenderer.ts"],
                required_gates=["unit", "native-powerpoint"],
            )
        ],
        latest_case_ids={},
    )

    assert plan["requiresNative"] is True
    assert plan["nativeCaseIds"] == []
    assert plan["nativeCapabilitiesWithoutCases"] == ["drawingml.chart.3d.view"]


def test_declared_verification_cases_cover_non_native_capability_without_receipt():
    case_id = "oracle-chart-3d-fallback"
    capability = _capability(
        "drawingml.chart.3d.view",
        affected_paths=["src/renderer/ChartRenderer.ts"],
        required_gates=["unit", "native-powerpoint"],
    )
    capability["verificationCases"] = [case_id]
    plan = build_verification_plan(
        changed_paths=["src/renderer/ChartRenderer.ts"],
        capabilities=[capability],
        latest_case_ids={},
        latest_case_hashes={},
        available_native_case_hashes={case_id: {("a" * 64, "b" * 64)}},
    )

    assert plan["nativeCaseIds"] == [case_id]
    assert plan["nativeCapabilitiesWithoutCases"] == []
    assert plan["nativeCasesMissingArtifacts"] == []


def test_native_artifacts_and_local_metric_obligations_are_explicit():
    plan = build_verification_plan(
        changed_paths=["src/renderer/Shape3DRenderer.ts"],
        capabilities=[
            _capability(
                "drawingml.shape.3d.camera-projected-plane",
                affected_paths=["src/renderer/Shape3DRenderer.ts"],
                required_gates=["unit", "native-powerpoint", "camera-local"],
            )
        ],
        latest_case_ids={
            "drawingml.shape.3d.camera-projected-plane": ["case-present", "case-missing"]
        },
        available_native_case_ids=["case-present"],
        case_reports={
            "case-present": "test/e2e/reports/present.json",
            "case-missing": "test/e2e/reports/missing.json",
        },
        python_executable="python-for-test",
    )

    assert plan["nativeCasesMissingArtifacts"] == ["case-missing"]
    assert plan["localGates"] == ["camera-local"]
    assert plan["localGateMissingCaseReports"] == {"camera-local": ["case-missing"]}
    assert plan["localGatesWithoutCaseReport"] == []
    assert plan["localGateCommands"] == []


def test_native_artifact_must_match_the_receipt_source_and_ground_truth_sha256():
    capability_id = "drawingml.shape.geometry.adjustment.donut"
    case_id = "oracle-pypptx-shape-adj-0009-donut-thin-ring"
    expected = ("a" * 64, "b" * 64)
    plan = build_verification_plan(
        changed_paths=["src/shapes/presets.ts"],
        capabilities=[
            _capability(
                capability_id,
                affected_paths=["src/shapes/presets.ts"],
                required_gates=["native-powerpoint"],
            )
        ],
        latest_case_ids={capability_id: [case_id]},
        latest_case_hashes={capability_id: {case_id: expected}},
        available_native_case_hashes={
            case_id: {("c" * 64, "d" * 64)},
        },
    )

    assert plan["nativeCasesMissingArtifacts"] == [case_id]
    assert plan["nativeCaseArtifactIssues"] == [
        {
            "capabilityId": capability_id,
            "caseId": case_id,
            "reason": "input-hash-mismatch",
        }
    ]


def test_complete_local_metric_gate_lists_an_executable_repeatable_report_command():
    plan = build_verification_plan(
        changed_paths=["src/renderer/Shape3DRenderer.ts"],
        capabilities=[
            _capability(
                "drawingml.shape.3d.camera-projected-plane",
                affected_paths=["src/renderer/Shape3DRenderer.ts"],
                required_gates=["camera-local"],
            )
        ],
        latest_case_ids={
            "drawingml.shape.3d.camera-projected-plane": ["case-a", "case-b"]
        },
        available_native_case_ids=["case-a", "case-b"],
        case_reports={
            "case-b": "test/e2e/reports/b.json",
            "case-a": "test/e2e/reports/a.json",
        },
        python_executable="python-for-test",
    )

    assert plan["localGateMissingCaseReports"] == {}
    assert plan["localGateCommands"] == [
        _cmd(
            "python-for-test",
            "test/e2e/scripts/shape3d_camera_metrics.py",
            "--case-report",
            "test/e2e/reports/a.json",
            "--case-report",
            "test/e2e/reports/b.json",
            "--out",
            "test/e2e/reports/capability-loop/affected-camera-local.json",
        )
    ]


def test_local_metric_gate_without_case_report_is_not_presented_as_complete():
    plan = build_verification_plan(
        changed_paths=["src/renderer/Shape3DRenderer.ts"],
        capabilities=[
            _capability(
                "drawingml.shape.3d.camera-projected-plane",
                affected_paths=["src/renderer/Shape3DRenderer.ts"],
                required_gates=["camera-local"],
            )
        ],
        latest_case_ids={
            "drawingml.shape.3d.camera-projected-plane": ["case-present"]
        },
        available_native_case_ids=["case-present"],
    )

    assert plan["localGateCommands"] == []
    assert plan["localGatesWithoutCaseReport"] == ["camera-local"]
    assert plan["localGateMissingCaseReports"] == {}


def test_unclaimed_runtime_change_fails_closed_to_full_verification():
    plan = build_verification_plan(
        changed_paths=["src/parser/NewParser.ts"],
        capabilities=[],
        latest_case_ids={},
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["fullReason"] == "unmapped-runtime-path"
    assert plan["unmappedRuntimePaths"] == ["src/parser/NewParser.ts"]
    assert plan["commands"] == [
        _cmd("pnpm", "test"),
        _cmd("python-for-test", "-m", "pytest", ".", "-q", cwd="test/e2e"),
        _cmd("pnpm", "typecheck"),
        _cmd("pnpm", "test:browser"),
    ]


def test_changed_test_is_run_even_when_it_is_not_listed_by_a_capability():
    plan = build_verification_plan(
        changed_paths=["test/unit/renderer/NewRenderer.test.ts"],
        capabilities=[],
        latest_case_ids={},
    )

    assert plan["mode"] == "targeted"
    assert plan["unitTestPaths"] == ["test/unit/renderer/NewRenderer.test.ts"]
    assert plan["commands"] == [
        _cmd("pnpm", "exec", "vitest", "run", "test/unit/renderer/NewRenderer.test.ts")
    ]


def test_unclassified_control_plane_change_fails_closed_instead_of_running_zero_checks():
    plan = build_verification_plan(
        changed_paths=["scripts/build.mjs"],
        capabilities=[],
        latest_case_ids={},
    )

    assert plan["mode"] == "full"
    assert plan["fullReason"] == "global-runtime-control-change"
    assert plan["unmappedRuntimePaths"] == ["scripts/build.mjs"]
    assert plan["commands"]


def test_global_control_change_is_full_even_if_a_capability_claims_the_path():
    plan = build_verification_plan(
        changed_paths=["package.json"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["package.json"],
                required_gates=["native-powerpoint"],
            )
        ],
        latest_case_ids={"capability.a": ["case-a"]},
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["fullReason"] == "global-runtime-control-change"
    assert plan["nativeScope"] == "all-capabilities"
    assert _cmd(
        "python-for-test", "-m", "pytest", ".", "-q", cwd="test/e2e"
    ) in plan["commands"]


def test_version_only_package_change_uses_package_gates_without_native_expansion():
    plan = build_verification_plan(
        changed_paths=["package.json"],
        release_metadata_paths=["package.json"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["package.json"],
                required_gates=["native-powerpoint"],
            )
        ],
        latest_case_ids={"capability.a": ["case-a"]},
    )

    assert plan["mode"] == "release-metadata"
    assert plan["releaseMetadataPaths"] == ["package.json"]
    assert plan["impactedCapabilityIds"] == []
    assert plan["nativeCaseIds"] == []
    assert plan["requiresNative"] is False
    assert plan["commands"] == [
        _cmd("pnpm", "exec", "prettier", "--check", "package.json"),
        _cmd("pnpm", "build"),
        _cmd("pnpm", "test:package"),
        _cmd("pnpm", "publint"),
        _cmd("pnpm", "size"),
    ]


def test_browser_test_change_requires_browser_without_expanding_native_scope():
    plan = build_verification_plan(
        changed_paths=["test/browser/rendering-coverage.spec.ts"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["test/browser/rendering-coverage.spec.ts"],
                required_gates=["browser", "native-powerpoint"],
            ),
            _capability(
                "capability.b",
                affected_paths=["test/browser/rendering-coverage.spec.ts"],
                required_gates=["browser", "native-powerpoint"],
            ),
        ],
        latest_case_ids={"capability.a": ["case-a"], "capability.b": ["case-b"]},
    )

    assert plan["mode"] == "targeted"
    assert plan["impactedCapabilityIds"] == []
    assert plan["requiresBrowser"] is True
    assert plan["requiresNative"] is False
    assert plan["nativeCaseIds"] == []
    assert plan["unclassifiedPaths"] == []


def test_shared_case_generator_runs_its_test_without_expanding_every_capability():
    plan = build_verification_plan(
        changed_paths=["test/e2e/scripts/generate_pypptx_cases.py"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["test/e2e/scripts/generate_pypptx_cases.py"],
                required_gates=["native-powerpoint"],
            ),
            _capability(
                "capability.b",
                affected_paths=["test/e2e/scripts/generate_pypptx_cases.py"],
                required_gates=["native-powerpoint"],
            ),
        ],
        latest_case_ids={"capability.a": ["case-a"], "capability.b": ["case-b"]},
        repository_paths=["test/e2e/test_pypptx_generator_cases.py"],
        python_executable="python-for-test",
    )

    assert plan["mode"] == "targeted"
    assert plan["impactedCapabilityIds"] == []
    assert plan["pythonTestPaths"] == ["test/e2e/test_pypptx_generator_cases.py"]
    assert plan["nativeCaseIds"] == []
    assert plan["commands"] == [
        _cmd(
            "python-for-test",
            "-m",
            "pytest",
            "test_pypptx_generator_cases.py",
            "-q",
            cwd="test/e2e",
        )
    ]


def test_package_release_metadata_classifier_rejects_dependency_or_script_changes():
    before = b'{"name":"pkg","version":"1.2.4","knip":{"ignoreBinaries":["python3"]},"dependencies":{"a":"1"}}\n'
    version_only = b'{"name":"pkg","version":"1.3.0","dependencies":{"a":"1"}}\n'
    dependency_change = b'{"name":"pkg","version":"1.3.0","dependencies":{"a":"2"}}\n'
    script_change = b'{"name":"pkg","version":"1.3.0","dependencies":{"a":"1"},"scripts":{"test":"false"}}\n'

    assert _is_release_metadata_package_change(before, version_only) is True
    assert _is_release_metadata_package_change(before, dependency_change) is False
    assert _is_release_metadata_package_change(before, script_change) is False
    assert _is_release_metadata_package_change(before, before) is False


def test_full_mode_preserves_and_expands_native_and_local_obligations_for_global_runtime_changes():
    capabilities = [
        _capability(
            "drawingml.shape.3d.camera-projected-plane",
            affected_paths=["src/renderer/Shape3DRenderer.ts"],
            required_gates=["unit", "native-powerpoint", "camera-local"],
        ),
        _capability(
            "drawingml.chart.2d.common",
            affected_paths=["src/renderer/ChartRenderer.ts"],
            required_gates=["unit", "browser", "native-powerpoint"],
        ),
    ]
    plan = build_verification_plan(
        changed_paths=["src/renderer/Shape3DRenderer.ts", "scripts/build.mjs"],
        capabilities=capabilities,
        latest_case_ids={
            "drawingml.shape.3d.camera-projected-plane": ["shape3d-case"],
            "drawingml.chart.2d.common": ["chart-case"],
        },
        available_native_case_ids=["shape3d-case", "chart-case"],
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["nativeScope"] == "all-capabilities"
    assert plan["impactedCapabilityIds"] == [
        "drawingml.chart.2d.common",
        "drawingml.shape.3d.camera-projected-plane",
    ]
    assert plan["requiresNative"] is True
    assert plan["nativeCaseIds"] == ["chart-case", "shape3d-case"]
    assert plan["localGates"] == ["camera-local"]
    assert plan["localGatesWithoutCaseReport"] == ["camera-local"]


def test_capability_registry_change_impacts_every_capability_and_runs_contract_check():
    capabilities = [
        _capability(
            "capability.a",
            affected_paths=["src/a.ts"],
            required_gates=["native-powerpoint"],
        ),
        _capability(
            "capability.b",
            affected_paths=["src/b.ts"],
            required_gates=["browser", "native-powerpoint"],
        ),
    ]
    plan = build_verification_plan(
        changed_paths=["test/e2e/oracle/capabilities.json"],
        capabilities=capabilities,
        latest_case_ids={
            "capability.a": ["case-a"],
            "capability.b": ["case-b"],
        },
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["fullReason"] == "capability-contract-change"
    assert plan["nativeScope"] == "all-capabilities"
    assert plan["impactedCapabilityIds"] == ["capability.a", "capability.b"]
    assert plan["nativeCaseIds"] == ["case-a", "case-b"]
    assert _cmd("pnpm", "capability:check") in plan["commands"]


def test_shared_evaluation_server_change_expands_to_every_capability_and_native_gate():
    capabilities = [
        _capability(
            "capability.a",
            affected_paths=["test/e2e/server.py"],
            required_gates=["native-powerpoint"],
        ),
        _capability(
            "capability.b",
            affected_paths=["src/b.ts"],
            required_gates=["native-powerpoint", "camera-local"],
        ),
    ]
    plan = build_verification_plan(
        changed_paths=["test/e2e/server.py"],
        capabilities=capabilities,
        latest_case_ids={
            "capability.a": ["case-a"],
            "capability.b": ["case-b"],
        },
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["fullReason"] == "evidence-control-change"
    assert plan["nativeScope"] == "all-capabilities"
    assert plan["impactedCapabilityIds"] == ["capability.a", "capability.b"]
    assert plan["nativeCaseIds"] == ["case-a", "case-b"]
    assert plan["localGates"] == ["camera-local"]
    assert _cmd("pnpm", "capability:check") in plan["commands"]


def test_unclaimed_provenance_change_cannot_skip_receipt_and_native_gates():
    plan = build_verification_plan(
        changed_paths=["test/e2e/oracle/provenance.py"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["src/a.ts"],
                required_gates=["native-powerpoint"],
            )
        ],
        latest_case_ids={"capability.a": ["case-a"]},
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert plan["nativeScope"] == "all-capabilities"
    assert plan["impactedCapabilityIds"] == ["capability.a"]
    assert plan["nativeCaseIds"] == ["case-a"]
    assert _cmd("pnpm", "capability:check") in plan["commands"]


def test_python_control_change_runs_the_full_python_suite_from_e2e_directory():
    plan = build_verification_plan(
        changed_paths=["test/e2e/pyproject.toml"],
        capabilities=[],
        latest_case_ids={},
        python_executable="python-for-test",
    )

    assert plan["mode"] == "full"
    assert _cmd(
        "python-for-test", "-m", "pytest", ".", "-q", cwd="test/e2e"
    ) in plan["commands"]


def test_shared_implementation_path_deduplicates_tests_and_native_cases():
    plan = build_verification_plan(
        changed_paths=["src/renderer/ShapeRenderer.ts"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=[
                    "src/renderer/ShapeRenderer.ts",
                    "test/unit/renderer/ShapeRenderer.test.ts",
                ],
                required_gates=["unit", "native-powerpoint"],
            ),
            _capability(
                "capability.b",
                affected_paths=[
                    "src/renderer/ShapeRenderer.ts",
                    "test/unit/renderer/ShapeRenderer.test.ts",
                ],
                required_gates=["unit", "native-powerpoint"],
            ),
        ],
        latest_case_ids={
            "capability.a": ["case-a", "case-shared"],
            "capability.b": ["case-shared", "case-b"],
        },
    )

    assert plan["unitTestPaths"] == ["test/unit/renderer/ShapeRenderer.test.ts"]
    assert plan["nativeCaseIds"] == ["case-a", "case-b", "case-shared"]


def test_globbed_affected_paths_match_changes_and_expand_tracked_tests():
    plan = build_verification_plan(
        changed_paths=["src/renderer/shape3d/CameraProjection.ts"],
        capabilities=[
            _capability(
                "drawingml.shape.3d.top-bevel-contour",
                affected_paths=[
                    "src/renderer/shape3d/*.ts",
                    "test/unit/renderer/shape3d/*.test.ts",
                ],
                required_gates=["unit"],
            )
        ],
        latest_case_ids={},
        repository_paths=[
            "src/renderer/shape3d/CameraProjection.ts",
            "test/unit/renderer/shape3d/CameraProjection.test.ts",
            "test/unit/renderer/shape3d/BevelLighting.test.ts",
        ],
    )

    assert plan["mode"] == "targeted"
    assert plan["impactedCapabilityIds"] == ["drawingml.shape.3d.top-bevel-contour"]
    assert plan["unitTestPaths"] == [
        "test/unit/renderer/shape3d/BevelLighting.test.ts",
        "test/unit/renderer/shape3d/CameraProjection.test.ts",
    ]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_git_discovery_preserves_deletions_and_unicode_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    deleted = repo / "src" / "runtime.ts"
    deleted.write_text("export {};\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(
        repo,
        "-c",
        "user.name=Verifier",
        "-c",
        "user.email=verifier@example.invalid",
        "commit",
        "-qm",
        "initial",
    )

    deleted.unlink()
    unicode_path = repo / "src" / "测试.ts"
    unicode_path.write_text("export const value = 1;\n", encoding="utf-8")

    assert _discover_changed_paths(repo, None, "HEAD") == [
        "src/runtime.ts",
        "src/测试.ts",
    ]


def _write_native_case(case_dir: Path, *, source_marker: bytes, ground_truth: bytes) -> tuple[str, str]:
    case_dir.mkdir(parents=True)
    source_path = case_dir / "source.pptx"
    with zipfile.ZipFile(source_path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="slides/slide1.xml" Type="slide"/></Relationships>""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            + source_marker
            + b"</p:sld>",
        )
    ground_truth_path = case_dir / "ground-truth.pdf"
    ground_truth_path.write_bytes(ground_truth)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    ground_truth_file_hash = hashlib.sha256(ground_truth).hexdigest()
    combined_hash = hashlib.sha256(ground_truth_file_hash.encode("ascii")).hexdigest()
    return source_hash, combined_hash


def test_native_case_discovery_keeps_same_stem_corpora_distinct_by_exact_hash(tmp_path: Path):
    stem = "same-stem"
    default_hashes = _write_native_case(
        tmp_path / "test/e2e/testdata/cases" / stem,
        source_marker=b"<p:cSld/>",
        ground_truth=b"default ground truth",
    )
    windows_hashes = _write_native_case(
        tmp_path / "test/e2e/testdata/windows-cases" / stem,
        source_marker=b"<p:clrMapOvr/>",
        ground_truth=b"windows ground truth",
    )

    discovered = _native_case_hashes(tmp_path, [stem, f"win__{stem}"])

    assert discovered[stem] == {default_hashes, windows_hashes}
    assert discovered[f"win__{stem}"] == {windows_hashes}

    (tmp_path / "test/e2e/testdata/cases" / stem / "source.pptx").unlink()
    windows_only = _native_case_hashes(tmp_path, [stem])
    plan = build_verification_plan(
        changed_paths=["src/shapes/presets.ts"],
        capabilities=[
            _capability(
                "capability.a",
                affected_paths=["src/shapes/presets.ts"],
                required_gates=["native-powerpoint"],
            )
        ],
        latest_case_ids={"capability.a": [stem]},
        latest_case_hashes={"capability.a": {stem: default_hashes}},
        available_native_case_hashes=windows_only,
    )

    assert plan["nativeCaseArtifactIssues"] == [
        {
            "capabilityId": "capability.a",
            "caseId": stem,
            "reason": "input-hash-mismatch",
        }
    ]


def test_command_runner_honors_each_planned_working_directory():
    results = _run_commands(
        [
            _cmd(
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path.cwd().name == 'e2e'",
                cwd="test/e2e",
            )
        ]
    )

    assert results[0]["exitCode"] == 0
    assert results[0]["cwd"] == "test/e2e"
