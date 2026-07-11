"""Aggregate detection scorer for cross-branch comparison.

Runs every case in ``tests/fixtures/detection/cases.json`` through the live
``ButtonDetector`` and prints per-button-type and per-variant tables plus a
single overall "detection score". Run it on ``main`` and on a detection PR
branch, then diff the scores to settle "improved detection" claims objectively.

    python detection_report.py [--fail-under 0.99]

``--fail-under`` exits non-zero when the score drops below the bar, so the
report can double as a regression gate. Cases whose button type / template /
image is unavailable on the current branch are reported as skipped (same
gating as the pytest suite), so the score only reflects cases this branch can
actually run.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

from models import AppConfig, ButtonType
from services.button_detector import ButtonDetector
from tests.harness import (
    ASSETS_PATH,
    case_image_path,
    ground_truth_error,
    load_cases,
    read_rgb,
    skip_reason,
)


@dataclass
class Stats:
    """Tally for one grouping key (button type or variant)."""

    expected: int = 0  # present-cases that should be detected
    detected: int = 0  # present-cases correctly detected
    errors: list[float] = field(default_factory=list)  # px error per hit
    negatives: int = 0  # absent-cases
    false_positives: int = 0  # absent-cases that wrongly detected something
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def mean_error(self) -> float:
        return sum(self.errors) / len(self.errors) if self.errors else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return (
            sum(self.latencies_ms) / len(self.latencies_ms)
            if self.latencies_ms
            else 0.0
        )


def _bootstrap_synthetic() -> None:
    """Regenerate synthetic fixture images when absent (they are gitignored)."""
    from tests._generate_synthetic import SYNTHETIC_DIR, main as generate_synthetic

    if not any(SYNTHETIC_DIR.glob("*.png")):
        generate_synthetic()


def run(fail_under: float | None = None) -> int:
    _bootstrap_synthetic()
    cases = load_cases()
    if not cases:
        print("No cases found in cases.json. Run: python -m tests._generate_synthetic")
        return 1

    detector = ButtonDetector(ASSETS_PATH)
    defaults = AppConfig()

    by_type: dict[str, Stats] = {}
    by_variant: dict[str, Stats] = {}
    skipped = 0

    for case in cases:
        reason = skip_reason(case)
        if reason:
            skipped += 1
            continue

        button_type = ButtonType(case["button_type"])
        variant = str(case.get("meta", {}).get("variant", "?"))
        img = read_rgb(case_image_path(case))

        start = time.perf_counter()
        result = detector.detect(
            img,
            button_type,
            min_matches=int(case.get("min_matches", defaults.min_matches)),
            ratio=float(case.get("ratio", defaults.ratio_threshold)),
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        for st in (
            by_type.setdefault(button_type.value, Stats()),
            by_variant.setdefault(variant, Stats()),
        ):
            st.latencies_ms.append(elapsed_ms)
            if case["expect"] == "absent":
                st.negatives += 1
                if result is not None:
                    st.false_positives += 1
                continue
            st.expected += 1
            if result is not None:
                error = ground_truth_error((result.x, result.y), case)
                if error <= float(case["tolerance_px"]):
                    st.detected += 1
                    st.errors.append(error)

    print("By button type:")
    _print_table(by_type)
    print()
    print("By variant:")
    _print_table(by_variant)

    score = _print_score(by_type, skipped)
    if fail_under is not None and score < fail_under:
        print(f"FAIL: score {score:.3f} < required {fail_under:.3f}")
        return 1
    return 0


def _print_table(stats: dict[str, Stats]) -> None:
    header = (
        f"{'group':<18}{'detected':>10}{'mean_err_px':>13}"
        f"{'false_pos':>11}{'negatives':>11}{'mean_ms':>9}"
    )
    print(header)
    print("-" * len(header))
    for name in sorted(stats):
        st = stats[name]
        ratio = f"{st.detected}/{st.expected}" if st.expected else "-"
        mean = f"{st.mean_error:.1f}" if st.errors else "-"
        print(
            f"{name:<18}{ratio:>10}{mean:>13}{st.false_positives:>11}"
            f"{st.negatives:>11}{st.mean_latency_ms:>9.0f}"
        )


def _print_score(by_type: dict[str, Stats], skipped: int) -> float:
    total_detected = sum(st.detected for st in by_type.values())
    total_expected = sum(st.expected for st in by_type.values())
    total_fp = sum(st.false_positives for st in by_type.values())
    all_errors = [e for st in by_type.values() for e in st.errors]

    print("-" * 72)
    overall_ratio = f"{total_detected}/{total_expected}" if total_expected else "-"
    overall_mean = f"{sum(all_errors) / len(all_errors):.1f}" if all_errors else "-"
    print(f"TOTAL: {overall_ratio} detected, mean error {overall_mean}px")

    # Detection score: recall penalised by false positives, in [0, 1].
    recall = total_detected / total_expected if total_expected else 0.0
    fp_penalty = (
        total_fp / (total_expected + total_fp) if (total_expected + total_fp) else 0.0
    )
    score = max(0.0, recall - fp_penalty)
    print()
    print(
        f"Detection score: {score:.3f}  "
        f"(recall {recall:.3f}, false positives {total_fp}, skipped cases {skipped})"
    )
    return score


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit non-zero if the detection score is below this value",
    )
    raise SystemExit(run(parser.parse_args().fail_under))
