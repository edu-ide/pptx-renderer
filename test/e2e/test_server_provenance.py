from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import server


RUN_ALL_PATH = Path(__file__).resolve().parent / "scripts" / "run_all_shapes_eval.py"


class _ScreenshotLocator:
    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def screenshot(self):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 3), "white").save(buffer, format="PNG")
        return buffer.getvalue()


class _ScreenshotPage:
    def __init__(self, render_error: str | None):
        self.render_error = render_error
        self.closed = False

    def set_default_timeout(self, _timeout):
        pass

    async def goto(self, _url):
        pass

    async def wait_for_function(self, _expression, timeout):
        assert timeout == server.PAGE_TIMEOUT_MS

    async def evaluate(self, _expression):
        return self.render_error

    def locator(self, _selector):
        return _ScreenshotLocator()

    async def close(self):
        self.closed = True


class _ScreenshotContext:
    def __init__(self, page: _ScreenshotPage):
        self.page = page
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _ScreenshotBrowser:
    def __init__(self, errors: list[str | None]):
        self.contexts = [
            _ScreenshotContext(_ScreenshotPage(render_error)) for render_error in errors
        ]
        self.created = 0

    async def new_context(self, **_kwargs):
        context = self.contexts[self.created]
        self.created += 1
        return context


def test_render_artifacts_bind_reference_and_candidate_screenshots(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    reference = tmp_path / "test/e2e/reports/case_pdf.png"
    candidate = tmp_path / "test/e2e/reports/case_html.png"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"native")
    candidate.write_bytes(b"renderer")

    artifacts = server._render_artifacts(reference, candidate)

    assert artifacts == {
        "reference": {
            "path": "test/e2e/reports/case_pdf.png",
            "sizeBytes": 6,
            "sha256": hashlib.sha256(b"native").hexdigest(),
        },
        "candidate": {
            "path": "test/e2e/reports/case_html.png",
            "sizeBytes": 8,
            "sha256": hashlib.sha256(b"renderer").hexdigest(),
        },
    }


def test_screenshot_retries_one_transient_visual_stability_timeout():
    browser = _ScreenshotBrowser(
        ["Visual output did not stabilize within 5000ms", None]
    )

    image = asyncio.run(server.screenshot_slide(browser, "sample", 0))

    assert image.shape == (3, 4, 3)
    assert browser.created == 2
    assert all(context.closed and context.page.closed for context in browser.contexts)


def test_screenshot_does_not_retry_other_render_errors():
    browser = _ScreenshotBrowser(["broken formula"])

    with pytest.raises(RuntimeError, match="broken formula"):
        asyncio.run(server.screenshot_slide(browser, "sample", 0))

    assert browser.created == 1
    assert browser.contexts[0].closed is True
    assert browser.contexts[0].page.closed is True


def test_screenshot_still_fails_after_two_visual_stability_timeouts():
    message = "Visual output did not stabilize within 5000ms"
    browser = _ScreenshotBrowser([message, message])

    with pytest.raises(RuntimeError, match="did not stabilize"):
        asyncio.run(server.screenshot_slide(browser, "sample", 0))

    assert browser.created == 2
    assert all(context.closed and context.page.closed for context in browser.contexts)


def _load_run_all_module():
    spec = importlib.util.spec_from_file_location("run_all_shapes_eval", RUN_ALL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_slide_url_includes_explicit_font_profile(monkeypatch):
    monkeypatch.setenv("PPTX_E2E_FONT_PROFILE", "font-profiles/office-zh.json")

    url = server._render_slide_url("sample", 2, None)

    assert "file=testdata/cases/sample/source.pptx" in url
    assert "slide=2" in url
    assert "fontProfile=font-profiles%2Foffice-zh.json" in url


def test_render_slide_url_omits_font_profile_by_default(monkeypatch):
    monkeypatch.delenv("PPTX_E2E_FONT_PROFILE", raising=False)

    assert "fontProfile=" not in server._render_slide_url("sample", 0, None)


def test_pdf_capture_scale_matches_the_reference_raster_density():
    assert server._capture_device_scale_factor(using_png_ground_truth=False) == pytest.approx(
        server.PDF_RASTER_DPI / server.BROWSER_CSS_DPI
    )
    assert server._capture_device_scale_factor(using_png_ground_truth=True) == 1.0


@pytest.mark.parametrize(
    "profile_ref",
    ["../escape.json", "/absolute.json", "https://example.com/profile.json", r"..\escape.json"],
)
def test_render_slide_url_rejects_non_local_font_profile(monkeypatch, profile_ref: str):
    monkeypatch.setenv("PPTX_E2E_FONT_PROFILE", profile_ref)

    with pytest.raises(ValueError, match="testdata-relative"):
        server._render_slide_url("sample", 0, None)


def test_batch_result_preserves_case_provenance():
    run_all = _load_run_all_module()
    provenance = {
        "schemaVersion": 1,
        "inputs": {"sourcePptx": {"sha256": "abc"}},
        "runtime": {"browser": {"name": "chromium", "version": "140"}},
    }

    result = run_all._result_from_evaluate_response(
        "sample",
        {
            "avgSsim": 0.9,
            "avgColorHistCorr": 0.99,
            "slideCount": 1,
            "visibleSlideCount": 1,
            "supported": False,
            "quality": {"needsReview": True},
            "provenance": provenance,
        },
    )

    assert result["provenance"] == provenance


def test_server_counts_per_slide_runtime_errors_separately_from_visual_metrics():
    per_slide = [
        {"slideIdx": 0, "ssim": 0.99},
        {"slideIdx": 1, "ssim": None, "error": "Target page closed"},
    ]

    assert server._evaluation_errors(per_slide) == [
        {"slideIdx": 1, "error": "Target page closed"},
    ]


def test_empty_runtime_error_message_remains_an_evaluation_error():
    run_all = _load_run_all_module()
    per_slide = [{"slideIdx": 0, "error": ""}]
    expected = [{"slideIdx": 0, "error": "Unknown slide evaluation error"}]

    assert server._evaluation_errors(per_slide) == expected
    assert run_all._evaluation_errors_from_response({"perSlide": per_slide}) == expected


def test_case_requires_review_when_one_slide_is_below_the_review_threshold(
    tmp_path: Path,
    monkeypatch,
):
    pptx_path = tmp_path / "source.pptx"
    pdf_path = tmp_path / "ground-truth.pdf"
    pptx_path.write_bytes(b"pptx")
    pdf_path.write_bytes(b"pdf")
    image = np.full((8, 8, 3), 255, dtype=np.uint8)
    visual_metrics = iter(
        (
            {"ssim": 0.985, "mae": 1.0, "color_hist_corr": 1.0},
            {"ssim": 0.995, "mae": 1.0, "color_hist_corr": 1.0},
        )
    )

    class Browser:
        version = "test"

    async def get_browser():
        return Browser()

    capture_scales = []

    async def screenshot_slide(*_args, **kwargs):
        capture_scales.append(kwargs.get("device_scale_factor"))
        return image

    def save_image(_image, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    monkeypatch.setattr(server.tdp, "source_pptx", lambda *_args: pptx_path)
    monkeypatch.setattr(server.tdp, "ground_truth_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(server.tdp, "has_png_ground_truth", lambda *_args: False)
    monkeypatch.setattr(server.tdp, "slide_png", lambda *_args: tmp_path / "missing.png")
    monkeypatch.setattr(server, "get_browser", get_browser)
    monkeypatch.setattr(server, "build_slide_to_pdf_mapping", lambda *_args: [0, 1])
    monkeypatch.setattr(server, "get_pdf_page_count", lambda *_args: 2)
    monkeypatch.setattr(server, "collect_evaluation_provenance", lambda **_kwargs: {})
    monkeypatch.setattr(server, "pdf_page_to_image", lambda *_args: image)
    monkeypatch.setattr(server, "screenshot_slide", screenshot_slide)
    monkeypatch.setattr(server, "compute_visual_metrics", lambda *_args: next(visual_metrics))
    monkeypatch.setattr(
        server,
        "compute_foreground_shape_metrics",
        lambda *_args: {
            "fg_iou": 1.0,
            "fg_iou_tolerant": 1.0,
            "chamfer_score": 1.0,
        },
    )
    monkeypatch.setattr(server, "make_diff_heatmap", lambda *_args: image)
    monkeypatch.setattr(server, "_save_image", save_image)
    monkeypatch.setattr(server, "REPORTS_DIR", tmp_path / "reports")
    server._eval_cache.clear()

    result = asyncio.run(server.evaluate_file("multi-slide"))

    assert result["avgSsim"] == 0.99
    assert [slide["needsReview"] for slide in result["perSlide"]] == [True, False]
    assert result["quality"]["needsReview"] is True
    assert "warn:ssim_below_review_threshold" in result["quality"]["warnings"]
    assert capture_scales == [server.PDF_RASTER_DPI / server.BROWSER_CSS_DPI] * 2
    assert [slide["captureDeviceScaleFactor"] for slide in result["perSlide"]] == capture_scales


def test_low_foreground_overlap_warning_requires_manual_review(tmp_path: Path, monkeypatch):
    pptx_path = tmp_path / "source.pptx"
    pdf_path = tmp_path / "ground-truth.pdf"
    pptx_path.write_bytes(b"pptx")
    pdf_path.write_bytes(b"pdf")
    image = np.full((8, 8, 3), 255, dtype=np.uint8)

    class Browser:
        version = "test"

    async def get_browser():
        return Browser()

    async def screenshot_slide(*_args, **_kwargs):
        return image

    def save_image(_image, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    monkeypatch.setattr(server.tdp, "source_pptx", lambda *_args: pptx_path)
    monkeypatch.setattr(server.tdp, "ground_truth_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(server.tdp, "has_png_ground_truth", lambda *_args: False)
    monkeypatch.setattr(server.tdp, "slide_png", lambda *_args: tmp_path / "missing.png")
    monkeypatch.setattr(server, "get_browser", get_browser)
    monkeypatch.setattr(server, "build_slide_to_pdf_mapping", lambda *_args: [0])
    monkeypatch.setattr(server, "get_pdf_page_count", lambda *_args: 1)
    monkeypatch.setattr(server, "collect_evaluation_provenance", lambda **_kwargs: {})
    monkeypatch.setattr(server, "pdf_page_to_image", lambda *_args: image)
    monkeypatch.setattr(server, "screenshot_slide", screenshot_slide)
    monkeypatch.setattr(
        server,
        "compute_visual_metrics",
        lambda *_args: {"ssim": 0.995, "mae": 0.1, "color_hist_corr": 1.0},
    )
    monkeypatch.setattr(
        server,
        "compute_foreground_shape_metrics",
        lambda *_args: {
            "fg_iou": 0.05,
            "fg_iou_tolerant": 0.08,
            "chamfer_score": 0.98,
        },
    )
    monkeypatch.setattr(server, "make_diff_heatmap", lambda *_args: image)
    monkeypatch.setattr(server, "_save_image", save_image)
    monkeypatch.setattr(server, "REPORTS_DIR", tmp_path / "reports")
    server._eval_cache.clear()

    result = asyncio.run(server.evaluate_file("formula-like-sparse-content"))

    assert result["supported"] is True
    assert result["quality"]["needsReview"] is True
    assert "warn:low_foreground_overlap_check_manually" in result["quality"]["warnings"]


def test_cartesian_chart_evidence_is_attached_without_reclassifying_full_slide(
    tmp_path: Path,
    monkeypatch,
):
    pptx_path = tmp_path / "source.pptx"
    pdf_path = tmp_path / "ground-truth.pdf"
    pptx_path.write_bytes(b"pptx")
    pdf_path.write_bytes(b"pdf")
    image = np.full((8, 8, 3), 255, dtype=np.uint8)
    profile = {
        "evaluable": True,
        "family": "line",
        "orientation": "vertical",
    }
    chart_evidence = {
        "evaluable": True,
        "family": "line",
        "passed": True,
    }

    class Browser:
        version = "test"

    async def get_browser():
        return Browser()

    async def screenshot_slide(*_args, **_kwargs):
        return image

    monkeypatch.setattr(server.tdp, "source_pptx", lambda *_args: pptx_path)
    monkeypatch.setattr(server.tdp, "ground_truth_pdf", lambda *_args: pdf_path)
    monkeypatch.setattr(server.tdp, "has_png_ground_truth", lambda *_args: False)
    monkeypatch.setattr(server.tdp, "slide_png", lambda *_args: tmp_path / "missing.png")
    monkeypatch.setattr(server, "get_browser", get_browser)
    monkeypatch.setattr(server, "build_slide_to_pdf_mapping", lambda *_args: [0])
    monkeypatch.setattr(server, "get_pdf_page_count", lambda *_args: 1)
    monkeypatch.setattr(server, "collect_evaluation_provenance", lambda **_kwargs: {})
    monkeypatch.setattr(server, "extract_cartesian_chart_profiles", lambda *_args: {0: profile})
    monkeypatch.setattr(server, "pdf_page_to_image", lambda *_args: image)
    monkeypatch.setattr(server, "screenshot_slide", screenshot_slide)
    monkeypatch.setattr(
        server,
        "compute_visual_metrics",
        lambda *_args: {"ssim": 0.94, "mae": 0.1, "color_hist_corr": 1.0},
    )
    monkeypatch.setattr(
        server,
        "compute_foreground_shape_metrics",
        lambda *_args: {
            "fg_iou": 1.0,
            "fg_iou_tolerant": 1.0,
            "chamfer_score": 1.0,
        },
    )
    monkeypatch.setattr(
        server,
        "compute_cartesian_chart_metrics",
        lambda reference, candidate, received_profile: (
            chart_evidence
            if reference is image and candidate is image and received_profile is profile
            else None
        ),
    )
    monkeypatch.setattr(server, "make_diff_heatmap", lambda *_args: image)
    monkeypatch.setattr(server, "_save_image", lambda *_args: None)
    monkeypatch.setattr(server, "_render_artifacts", lambda *_args: {})
    server._eval_cache.clear()

    result = asyncio.run(server.evaluate_file("cartesian-chart"))

    assert result["perSlide"][0]["cartesianChart"] == chart_evidence
    assert result["supported"] is False
    assert "metric:ssim" in result["quality"]["reasons"]


def test_cartesian_chart_metric_error_is_scoped_to_its_evidence(monkeypatch):
    image = np.full((8, 8, 3), 255, dtype=np.uint8)

    def fail(*_args):
        raise ValueError("axis detector failed")

    monkeypatch.setattr(server, "compute_cartesian_chart_metrics", fail)

    assert server._cartesian_chart_evidence(image, image, {"evaluable": True}) == {
        "evaluable": False,
        "reason": "metric-error",
        "error": "axis detector failed",
    }


def test_cartesian_chart_evidence_is_not_scored_against_a_mismatched_oracle_page(
    monkeypatch,
):
    image = np.full((8, 8, 3), 255, dtype=np.uint8)

    def fail(*_args):
        raise AssertionError("local chart metric must not run against the wrong PDF page")

    monkeypatch.setattr(server, "compute_cartesian_chart_metrics", fail)

    assert server._cartesian_chart_evidence(
        image,
        image,
        {"evaluable": True, "family": "line"},
        oracle_mismatch={"expectedPdfPage": 0, "bestPdfPage": 1},
    ) == {
        "evaluable": False,
        "reason": "oracle-ground-truth-mismatch",
    }


def test_batch_retry_recovers_a_transient_per_slide_runtime_error():
    run_all = _load_run_all_module()

    class Response:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class Client:
        def __init__(self):
            self.calls = 0

        async def post(self, _url):
            self.calls += 1
            if self.calls == 1:
                return Response(
                    {
                        "perSlide": [
                            {"slideIdx": 0, "ssim": None, "error": "Target page closed"}
                        ]
                    }
                )
            return Response(
                {
                    "avgSsim": 0.99,
                    "avgColorHistCorr": 1.0,
                    "slideCount": 1,
                    "visibleSlideCount": 1,
                    "supported": True,
                    "quality": {"needsReview": False},
                    "perSlide": [{"slideIdx": 0, "ssim": 0.99}],
                }
            )

    client = Client()
    result, error = asyncio.run(
        run_all._eval_one(
            client,
            asyncio.Semaphore(1),
            "http://127.0.0.1:8081",
            "sample",
            retries=1,
        )
    )

    assert error is None
    assert result is not None
    assert result["summary"]["ssim"] == 0.99
    assert client.calls == 2


def test_browser_init_replaces_a_disconnected_browser(monkeypatch):
    events = []
    close_started = None
    release_close = None

    class Browser:
        def __init__(self, connected):
            self.connected = connected

        def is_connected(self):
            return self.connected

        async def close(self):
            events.append("close-browser")
            if not self.connected:
                close_started.set()
                await release_close.wait()

    class Playwright:
        def __init__(self, browser):
            self.chromium = self
            self.browser = browser

        async def launch(self, **_options):
            events.append("launch-browser")
            return self.browser

        async def stop(self):
            events.append("stop-playwright")

    class Starter:
        async def start(self):
            events.append("start-playwright")
            return fresh_playwright

    stale_browser = Browser(False)
    stale_playwright = Playwright(stale_browser)
    fresh_browser = Browser(True)
    fresh_playwright = Playwright(fresh_browser)
    monkeypatch.setattr(server, "_browser", stale_browser)
    monkeypatch.setattr(server, "_playwright", stale_playwright)
    monkeypatch.setattr(server, "async_playwright", lambda: Starter())
    monkeypatch.setattr(server, "_browser_init_lock", asyncio.Lock())

    async def get_concurrently():
        nonlocal close_started, release_close
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        first = asyncio.create_task(server.get_browser())
        await close_started.wait()
        second = asyncio.create_task(server.get_browser())
        await asyncio.sleep(0)
        release_close.set()
        results = await asyncio.gather(first, second)
        initialization_events = list(events)
        await server.close_browser()
        return results, initialization_events

    results, initialization_events = asyncio.run(get_concurrently())

    assert results == [fresh_browser, fresh_browser]
    assert initialization_events == [
        "close-browser",
        "stop-playwright",
        "start-playwright",
        "launch-browser",
    ]


def test_browser_init_stops_playwright_when_launch_is_cancelled(monkeypatch):
    events = []

    class Playwright:
        chromium = None

        def __init__(self):
            self.chromium = self

        async def launch(self, **_options):
            events.append("launch-browser")
            raise asyncio.CancelledError()

        async def stop(self):
            events.append("stop-playwright")

    playwright = Playwright()

    class Starter:
        async def start(self):
            events.append("start-playwright")
            return playwright

    monkeypatch.setattr(server, "_browser", None)
    monkeypatch.setattr(server, "_playwright", None)
    monkeypatch.setattr(server, "async_playwright", lambda: Starter())
    monkeypatch.setattr(server, "_browser_init_lock", asyncio.Lock())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server.get_browser())

    assert events == ["start-playwright", "launch-browser", "stop-playwright"]
    assert server._browser is None
    assert server._playwright is None
