import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import server


def test_testdata_route_rejects_directory_traversal():
    with pytest.raises(server.HTTPException) as exc_info:
        asyncio.run(server.serve_testdata("../../../package.json"))

    assert exc_info.value.status_code == 400


def test_testdata_route_serves_files_inside_testdata(tmp_path: Path, monkeypatch):
    testdata_root = tmp_path / "testdata"
    testdata_root.mkdir()
    allowed = testdata_root / "cases" / "demo" / "source.pptx"
    allowed.parent.mkdir(parents=True)
    allowed.write_bytes(b"pptx")
    monkeypatch.setattr(server, "TESTDATA_DIR", testdata_root)

    client = TestClient(server.app)
    resp = client.get("/testdata/cases/demo/source.pptx")

    assert resp.status_code == 200
    assert resp.content == b"pptx"


def test_manual_review_rejects_path_like_case_names(tmp_path: Path, monkeypatch):
    reports_root = tmp_path / "reports"
    oracle_reports = reports_root / "oracle-failures"
    monkeypatch.setattr(server, "REPORTS_DIR", reports_root)
    monkeypatch.setattr(server, "ORACLE_REPORTS_DIR", oracle_reports)
    monkeypatch.setattr(server, "MANUAL_REVIEW_PATH", oracle_reports / "manual-review.json")
    monkeypatch.setattr(server, "SUPPORT_CATALOG_PATH", oracle_reports / "support-catalog.json")
    monkeypatch.setattr(server, "ORACLE_CASES_DIR", tmp_path / "cases")

    client = TestClient(server.app)
    resp = client.post(
        "/api/manual-review",
        json={
            "test_file": "../package",
            "slide_idx": 0,
            "verdict": "supported",
            "note": "",
        },
    )

    assert resp.status_code == 400
