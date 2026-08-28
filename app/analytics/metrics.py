"""Pure statistical helpers shared by every portal.

Nothing here touches the database or the request, which makes each rule directly
unit-testable and guarantees a parent, a teacher and the principal all see the same
number computed the same way.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from app.config import settings

# CBSE-style bands: (minimum percentage, grade label, descriptor)
GRADE_BANDS: tuple[tuple[float, str, str], ...] = (
    (91.0, "A1", "Outstanding"),
    (81.0, "A2", "Excellent"),
    (71.0, "B1", "Very Good"),
    (61.0, "B2", "Good"),
    (51.0, "C1", "Fair"),
    (41.0, "C2", "Satisfactory"),
    (33.0, "D", "Needs Improvement"),
    (0.0, "E", "Unsatisfactory"),
)

DISTRIBUTION_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-32", 0.0, 33.0),
    ("33-40", 33.0, 41.0),
    ("41-50", 41.0, 51.0),
    ("51-60", 51.0, 61.0),
    ("61-70", 61.0, 71.0),
    ("71-80", 71.0, 81.0),
    ("81-90", 81.0, 91.0),
    ("91-100", 91.0, 100.01),
)

# Trend thresholds in percentage points per term. A term is a much coarser step than a
# single assessment, so term-scale trends and risk use these rather than the
# per-assessment thresholds in `trend_label`.
TERM_SLIDE_SHARP = -5.0
TERM_SLIDE_MILD = -2.0


def percentage(marks_obtained: float | None, max_marks: float) -> float | None:
    """Convert raw marks to a percentage, or None when there is nothing to convert."""
    if marks_obtained is None or max_marks is None or max_marks <= 0:
        return None
    return round(marks_obtained / max_marks * 100, 2)


def safe_mean(values: list[float | None] | list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(statistics.fmean(clean), 2)


def letter_grade(pct: float | None) -> str:
    if pct is None:
        return "--"
    for minimum, label, _descriptor in GRADE_BANDS:
        if pct >= minimum:
            return label
    return GRADE_BANDS[-1][1]


def grade_descriptor(pct: float | None) -> str:
    if pct is None:
        return "Not assessed"
    for minimum, _label, descriptor in GRADE_BANDS:
        if pct >= minimum:
            return descriptor
    return GRADE_BANDS[-1][2]


def weighted_average(pairs: list[tuple[float | None, float]]) -> float | None:
    """Weighted mean of (percentage, weight), skipping entries with no percentage."""
    numerator = 0.0
    denominator = 0.0
    for pct, weight in pairs:
        if pct is None or weight is None or weight <= 0:
            continue
        numerator += pct * weight
        denominator += weight
    if denominator == 0:
        # No usable weights: fall back to a plain mean so a result is still produced.
        return safe_mean([pct for pct, _ in pairs])
    return round(numerator / denominator, 2)


def rank_and_percentile(
    value: float | None, cohort: list[float]
) -> tuple[int | None, int | None, float | None]:
    """Return (rank, cohort size, percentile) for `value` within `cohort`.

    Rank 1 is the highest score and ties share a rank. The percentile is the share of
    the cohort scoring at or below `value`.
    """
    clean = [c for c in cohort if c is not None]
    if value is None or not clean:
        return None, len(clean) or None, None
    higher = sum(1 for c in clean if c > value)
    at_or_below = sum(1 for c in clean if c <= value)
    percentile = round(at_or_below / len(clean) * 100, 1)
    return higher + 1, len(clean), percentile


def trend_slope(values: list[float | None]) -> float | None:
    """Least-squares slope in percentage points per assessment.

    Positive means the student is climbing over the sequence.
    """
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return round(numerator / denominator, 3)


def trend_label(slope: float | None) -> str:
    if slope is None:
        return "Not enough data"
    if slope >= 0.8:
        return "Improving strongly"
    if slope >= 0.25:
        return "Improving"
    if slope <= -0.8:
        return "Declining sharply"
    if slope <= -0.25:
        return "Declining"
    return "Holding steady"


def trend_tone(slope: float | None) -> str:
    if slope is None:
        return "neutral"
    if slope >= 0.25:
        return "positive"
    if slope <= -0.25:
        return "negative"
    return "neutral"


def term_trend_label(term_slope: float | None) -> str:
    """Label a slope measured in percentage points per term."""
    if term_slope is None:
        return "Not enough data"
    if term_slope >= -TERM_SLIDE_SHARP:
        return "Improving strongly"
    if term_slope >= -TERM_SLIDE_MILD:
        return "Improving"
    if term_slope <= TERM_SLIDE_SHARP:
        return "Declining sharply"
    if term_slope <= TERM_SLIDE_MILD:
        return "Declining"
    return "Holding steady"


def term_trend_tone(term_slope: float | None) -> str:
    if term_slope is None:
        return "neutral"
    if term_slope >= -TERM_SLIDE_MILD:
        return "positive"
    if term_slope <= TERM_SLIDE_MILD:
        return "negative"
    return "neutral"


def consistency(values: list[float | None]) -> float | None:
    """Standard deviation of a score series; lower means more predictable."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    return round(statistics.stdev(clean), 2)


def consistency_label(std_dev: float | None) -> str:
    if std_dev is None:
        return "Not enough data"
    if std_dev <= 6:
        return "Very consistent"
    if std_dev <= 11:
        return "Fairly consistent"
    if std_dev <= 17:
        return "Variable"
    return "Highly variable"


def distribution(values: list[float | None]) -> list[tuple[str, int]]:
    """Count scores per band, for a histogram."""
    clean = [v for v in values if v is not None]
    counts: list[tuple[str, int]] = []
    for label, low, high in DISTRIBUTION_BUCKETS:
        counts.append((label, sum(1 for v in clean if low <= v < high)))
    return counts


def grade_counts(values: list[float | None]) -> list[tuple[str, int]]:
    clean = [v for v in values if v is not None]
    order = [band[1] for band in GRADE_BANDS]
    tally = dict.fromkeys(order, 0)
    for value in clean:
        tally[letter_grade(value)] += 1
    return [(label, tally[label]) for label in order]


def pass_rate(values: list[float | None], threshold: float | None = None) -> float | None:
    limit = settings.pass_percentage if threshold is None else threshold
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return round(sum(1 for v in clean if v >= limit) / len(clean) * 100, 1)


def difficulty_index(class_average: float | None) -> str:
    """How hard a paper turned out to be, judged by how the class actually did."""
    if class_average is None:
        return "Not graded"
    if class_average >= 80:
        return "Easy"
    if class_average >= 65:
        return "Moderate"
    if class_average >= 50:
        return "Challenging"
    return "Hard"


def delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def attendance_percentage(present_days: int, total_days: int) -> float | None:
    if total_days <= 0:
        return None
    return round(present_days / total_days * 100, 1)


@dataclass(frozen=True)
class RiskAssessment:
    """Why a student is flagged, and how urgently."""

    level: str  # none | watch | high
    reasons: tuple[str, ...]

    @property
    def is_at_risk(self) -> bool:
        return self.level in ("watch", "high")

    @property
    def tone(self) -> str:
        return {"none": "positive", "watch": "warning", "high": "negative"}[self.level]

    @property
    def label(self) -> str:
        return {"none": "On track", "watch": "Needs watching", "high": "At risk"}[
            self.level
        ]


def assess_risk(
    average_pct: float | None,
    term_slope: float | None,
    attendance_pct: float | None,
    failing_subjects: int = 0,
) -> RiskAssessment:
    """Combine performance, direction, and attendance into one flag.

    `term_slope` is percentage points gained or lost per term. Any single hard signal
    (a failing average, several failed subjects, a steep slide, or poor attendance)
    escalates to high risk; softer signals only raise a watch.
    """
    reasons: list[str] = []
    score = 0

    if average_pct is not None:
        if average_pct < settings.pass_percentage:
            reasons.append(f"Average {average_pct:.1f}% is below the pass mark")
            score += 3
        elif average_pct < settings.at_risk_percentage:
            reasons.append(f"Average {average_pct:.1f}% is in the at-risk band")
            score += 2

    if failing_subjects >= 3:
        reasons.append(f"Failing {failing_subjects} subjects")
        score += 3
    elif failing_subjects > 0:
        reasons.append(f"Failing {failing_subjects} subject(s)")
        score += 1

    if term_slope is not None:
        if term_slope <= TERM_SLIDE_SHARP:
            reasons.append(
                f"Losing {abs(term_slope):.1f} points a term across the year"
            )
            score += 3
        elif term_slope <= TERM_SLIDE_MILD:
            reasons.append("Scores are trending downward term on term")
            score += 1

    if attendance_pct is not None and attendance_pct < settings.at_risk_attendance:
        reasons.append(f"Attendance is {attendance_pct:.0f}%")
        score += 3 if attendance_pct < settings.at_risk_attendance - 10 else 2

    if score >= 3:
        level = "high"
    elif score >= 1:
        level = "watch"
    else:
        level = "none"
    return RiskAssessment(level=level, reasons=tuple(reasons))


def performance_tone(pct: float | None) -> str:
    if pct is None:
        return "neutral"
    if pct >= 75:
        return "positive"
    if pct >= settings.at_risk_percentage:
        return "neutral"
    return "negative"


def strength_profile(
    subject_averages: dict[str, float | None], limit: int = 3
) -> tuple[list[str], list[str]]:
    """Split subjects into the strongest and weakest few, for a summary card."""
    ranked = sorted(
        ((name, pct) for name, pct in subject_averages.items() if pct is not None),
        key=lambda item: item[1],
        reverse=True,
    )
    strengths = [name for name, _ in ranked[:limit]]
    weaknesses = [name for name, _ in ranked[-limit:][::-1] if name not in strengths]
    return strengths, weaknesses
