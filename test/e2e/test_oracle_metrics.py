import numpy as np

from oracle.metrics import (
    aggregate_quality_gate,
    compute_color_histogram_correlation,
    compute_visual_metrics,
)


def test_visual_metrics_identical_images_are_high():
    img = np.full((120, 180, 3), 255, dtype=np.uint8)
    metrics = compute_visual_metrics(img, img)
    assert metrics["ssim"] > 0.99


def test_visual_metrics_detect_large_difference():
    a = np.zeros((120, 180, 3), dtype=np.uint8)
    b = np.zeros((120, 180, 3), dtype=np.uint8)
    a[20:70, 20:70] = 255
    b[70:110, 110:170] = 255
    metrics = compute_visual_metrics(a, b)
    assert metrics["ssim"] < 0.75


def _solid_foreground(color: tuple[int, int, int]) -> np.ndarray:
    image = np.full((120, 180, 3), 255, dtype=np.uint8)
    image[20:100, 30:150] = color
    return image


def test_color_histogram_tolerates_one_bin_quantization_shift():
    native = _solid_foreground((87, 138, 202))
    browser = _solid_foreground((91, 138, 195))

    assert compute_color_histogram_correlation(native, browser) > 0.99


def test_color_histogram_rejects_large_hue_shift():
    red = _solid_foreground((255, 0, 0))
    blue = _solid_foreground((0, 0, 255))

    assert compute_color_histogram_correlation(red, blue) < 0.80


def test_color_histogram_treats_negligible_antialiasing_as_blank():
    blank = np.full((120, 180, 3), 255, dtype=np.uint8)
    antialiased = blank.copy()
    antialiased[60, 80:100] = 235

    assert compute_color_histogram_correlation(blank, antialiased) == 1.0


def test_color_histogram_rejects_missing_meaningful_sparse_ink():
    blank = np.full((120, 180, 3), 255, dtype=np.uint8)
    missing_line = blank.copy()
    missing_line[60, 80:100] = 0

    assert compute_color_histogram_correlation(blank, missing_line) == 0.0


def test_quality_gate_uses_structural_text_and_visual_signals():
    result = aggregate_quality_gate(
        {
            "text_coverage": 0.98,
            "shape_recall": 0.95,
            "ssim": 0.96,
        }
    )
    assert result["passed"] is True

    failed = aggregate_quality_gate(
        {
            "text_coverage": 0.85,
            "shape_recall": 0.95,
            "ssim": 0.96,
        }
    )
    assert failed["passed"] is False
    assert any("text_coverage" in reason for reason in failed["reasons"])
