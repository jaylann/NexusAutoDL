"""Aggregate detection scorer for cross-branch comparison.

Runs every case in ``tests/fixtures/detection/cases.json`` through the live
``ButtonDetector`` and prints a per-button-type table plus a single overall
"detection score". Run it on ``main`` and on a detection PR branch, then diff
the scores to settle "improved detection" claims objectively.

    python detection_report.py

Cases whose button type / template / image is unavailable on the current
branch are reported as skipped (same gating as the pytest suite), so the score
only reflects cases this branch can actually run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models import AppConfig, ButtonType
from services.button_detector import ButtonDetector
from tests.harness import (
    ASSETS_PATH,
    case_image_path,
    centroid_error,
    load_cases,
    read_rgb,
    skip_reason,
)


@dataclass
class TypeStats:
    """Per-button-type tally."""

    expected: int = 0  # present-cases that should be detected
    detected: int = 0  # present-cases correctly detected
    errors: list[float] = field(default_factory=list)  # centroid error per hit
    negatives: int = 0  # absent-cases
    false_positives: int = 0  # absent-cases that wrongly detected something

    @property
    def mean_error(self) -> float:
        return sum(self.errors) / len(self.errors) if self.errors else 0.0


def run() -> int:
    cases = load_cases()
    if not cases:
        print("No cases found in cases.json. Run: python -m tests._generate_synthetic")
        return 1

    detector = ButtonDetector(ASSETS_PATH)
    defaults = AppConfig()

    stats: dict[str, TypeStats] = {}
    skipped = 0

    for case in cases:
        reason = skip_reason(case)
        if reason:
            skipped += 1
            continue

        button_type = ButtonType(case["button_type"])
        st = stats.setdefault(button_type.value, TypeStats())
        img = read_rgb(case_image_path(case))
        result = detector.detect(
            img,
            button_type,
            min_matches=int(case.get("min_matches", defaults.min_matches)),
            ratio=float(case.get("ratio", defaults.ratio_threshold)),
        )

        if case["expect"] == "absent":
            st.negatives += 1
            if result is not None:
                st.false_positives += 1
            continue

        # present
        st.expected += 1
        if result is not None:
            within = centroid_error((result.x, result.y), case["point"]) <= float(
                case["tolerance_px"]
            )
            if within:
                st.detected += 1
                st.errors.append(centroid_error((result.x, result.y), case["point"]))

    _print_table(stats, skipped)
    return 0


def _print_table(stats: dict[str, TypeStats], skipped: int) -> None:
    header = f"{'button_type':<14}{'detected':>12}{'mean_err_px':>14}{'false_pos':>12}"
    print(header)
    print("-" * len(header))

    total_detected = total_expected = total_fp = 0
    all_errors: list[float] = []

    for name in sorted(stats):
        st = stats[name]
        total_detected += st.detected
        total_expected += st.expected
        total_fp += st.false_positives
        all_errors.extend(st.errors)
        ratio = f"{st.detected}/{st.expected}" if st.expected else "-"
        mean = f"{st.mean_error:.1f}" if st.errors else "-"
        print(f"{name:<14}{ratio:>12}{mean:>14}{st.false_positives:>12}")

    print("-" * len(header))
    overall_ratio = f"{total_detected}/{total_expected}" if total_expected else "-"
    overall_mean = f"{sum(all_errors) / len(all_errors):.1f}" if all_errors else "-"
    print(f"{'TOTAL':<14}{overall_ratio:>12}{overall_mean:>14}{total_fp:>12}")

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


if __name__ == "__main__":
    raise SystemExit(run())
