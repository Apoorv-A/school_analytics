"""Tests for the pure analytics functions.

These are the rules a parent, a teacher and the principal all rely on being identical,
so each one is pinned here rather than only checked through a chart.
"""

from __future__ import annotations

import pytest

from app.analytics import metrics


class TestPercentage:
    def test_converts_marks_to_percentage(self):
        assert metrics.percentage(18, 25) == 72.0

    def test_missing_marks_yields_none(self):
        assert metrics.percentage(None, 25) is None

    def test_zero_max_marks_does_not_divide_by_zero(self):
        assert metrics.percentage(10, 0) is None


class TestSafeMean:
    def test_ignores_missing_values(self):
        assert metrics.safe_mean([80, None, 60]) == 70.0

    def test_all_missing_yields_none(self):
        assert metrics.safe_mean([None, None]) is None

    def test_empty_yields_none(self):
        assert metrics.safe_mean([]) is None


class TestLetterGrade:
    @pytest.mark.parametrize(
        ("percentage", "expected"),
        [
            (100, "A1"),
            (91, "A1"),
            (90.9, "A2"),
            (81, "A2"),
            (75, "B1"),
            (65, "B2"),
            (55, "C1"),
            (45, "C2"),
            (35, "D"),
            (32.9, "E"),
            (0, "E"),
        ],
    )
    def test_bands(self, percentage, expected):
        assert metrics.letter_grade(percentage) == expected

    def test_missing_value(self):
        assert metrics.letter_grade(None) == "--"


class TestWeightedAverage:
    def test_weights_are_applied(self):
        # 90 at weight 3 and 50 at weight 1 -> (270 + 50) / 4 = 80
        assert metrics.weighted_average([(90, 3), (50, 1)]) == 80.0

    def test_skips_entries_without_a_percentage(self):
        assert metrics.weighted_average([(80, 1), (None, 5)]) == 80.0

    def test_falls_back_to_plain_mean_when_no_usable_weights(self):
        assert metrics.weighted_average([(80, 0), (60, 0)]) == 70.0

    def test_empty_input(self):
        assert metrics.weighted_average([]) is None


class TestRankAndPercentile:
    def test_highest_score_ranks_first(self):
        rank, cohort, percentile = metrics.rank_and_percentile(95, [95, 80, 70, 60])
        assert (rank, cohort, percentile) == (1, 4, 100.0)

    def test_ties_share_a_rank(self):
        rank, _cohort, _pct = metrics.rank_and_percentile(80, [95, 80, 80, 60])
        assert rank == 2

    def test_lowest_score(self):
        rank, cohort, percentile = metrics.rank_and_percentile(60, [95, 80, 70, 60])
        assert rank == 4
        assert cohort == 4
        assert percentile == 25.0

    def test_missing_value(self):
        assert metrics.rank_and_percentile(None, [80, 70]) == (None, 2, None)

    def test_empty_cohort(self):
        assert metrics.rank_and_percentile(70, []) == (None, None, None)


class TestTrendSlope:
    def test_rising_series_has_positive_slope(self):
        assert metrics.trend_slope([50, 60, 70]) == 10.0

    def test_falling_series_has_negative_slope(self):
        assert metrics.trend_slope([70, 60, 50]) == -10.0

    def test_flat_series_has_zero_slope(self):
        assert metrics.trend_slope([65, 65, 65]) == 0.0

    def test_gaps_are_skipped(self):
        assert metrics.trend_slope([50, None, 70]) == 10.0

    def test_single_point_is_not_a_trend(self):
        assert metrics.trend_slope([70]) is None

    def test_all_missing(self):
        assert metrics.trend_slope([None, None]) is None


class TestTermTrendLabels:
    @pytest.mark.parametrize(
        ("slope", "expected"),
        [
            (7.0, "Improving strongly"),
            (3.0, "Improving"),
            (0.0, "Holding steady"),
            (-3.0, "Declining"),
            (-8.0, "Declining sharply"),
            (None, "Not enough data"),
        ],
    )
    def test_labels(self, slope, expected):
        assert metrics.term_trend_label(slope) == expected

    def test_tone_follows_direction(self):
        assert metrics.term_trend_tone(6.0) == "positive"
        assert metrics.term_trend_tone(0.0) == "neutral"
        assert metrics.term_trend_tone(-6.0) == "negative"


class TestConsistency:
    def test_stable_series_has_low_deviation(self):
        assert metrics.consistency([70, 71, 69, 70]) < 1.5

    def test_volatile_series_has_high_deviation(self):
        assert metrics.consistency([30, 90, 40, 95]) > 25

    def test_needs_two_points(self):
        assert metrics.consistency([70]) is None

    def test_labels_describe_the_spread(self):
        assert metrics.consistency_label(3) == "Very consistent"
        assert metrics.consistency_label(9) == "Fairly consistent"
        assert metrics.consistency_label(14) == "Variable"
        assert metrics.consistency_label(25) == "Highly variable"
        assert metrics.consistency_label(None) == "Not enough data"


class TestDistribution:
    def test_counts_land_in_the_right_bands(self):
        buckets = dict(metrics.distribution([10, 35, 45, 55, 65, 75, 85, 95]))
        assert buckets["0-32"] == 1
        assert buckets["33-40"] == 1
        assert buckets["91-100"] == 1

    def test_boundaries_are_inclusive_at_the_lower_edge(self):
        buckets = dict(metrics.distribution([33, 41, 91]))
        assert buckets["33-40"] == 1
        assert buckets["41-50"] == 1
        assert buckets["91-100"] == 1

    def test_hundred_percent_is_counted(self):
        assert dict(metrics.distribution([100]))["91-100"] == 1

    def test_every_band_is_always_present(self):
        assert len(metrics.distribution([])) == len(metrics.DISTRIBUTION_BUCKETS)


class TestGradeCounts:
    def test_tallies_by_grade(self):
        counts = dict(metrics.grade_counts([95, 92, 85, 30]))
        assert counts["A1"] == 2
        assert counts["A2"] == 1
        assert counts["E"] == 1


class TestPassRate:
    def test_uses_the_configured_threshold(self):
        assert metrics.pass_rate([20, 40, 60, 80]) == 75.0

    def test_explicit_threshold_overrides(self):
        assert metrics.pass_rate([20, 40, 60, 80], threshold=50) == 50.0

    def test_no_values(self):
        assert metrics.pass_rate([]) is None


class TestDifficultyIndex:
    @pytest.mark.parametrize(
        ("average", "expected"),
        [(85, "Easy"), (70, "Moderate"), (55, "Challenging"), (30, "Hard")],
    )
    def test_bands(self, average, expected):
        assert metrics.difficulty_index(average) == expected

    def test_ungraded(self):
        assert metrics.difficulty_index(None) == "Not graded"


class TestAttendancePercentage:
    def test_computes_share_of_days(self):
        assert metrics.attendance_percentage(45, 50) == 90.0

    def test_no_days_recorded(self):
        assert metrics.attendance_percentage(0, 0) is None


class TestAssessRisk:
    def test_strong_student_is_not_flagged(self):
        assessment = metrics.assess_risk(82.0, 1.5, 96.0, failing_subjects=0)
        assert assessment.level == "none"
        assert not assessment.is_at_risk
        assert assessment.reasons == ()

    def test_failing_average_is_high_risk(self):
        assessment = metrics.assess_risk(28.0, 0.0, 95.0)
        assert assessment.level == "high"
        assert any("pass mark" in reason for reason in assessment.reasons)

    def test_poor_attendance_alone_is_high_risk(self):
        assessment = metrics.assess_risk(78.0, 0.5, 55.0)
        assert assessment.level == "high"
        assert any("Attendance" in reason for reason in assessment.reasons)

    def test_steep_slide_is_high_risk_even_at_a_decent_average(self):
        assessment = metrics.assess_risk(68.0, -7.0, 95.0)
        assert assessment.level == "high"

    def test_mild_decline_only_raises_a_watch(self):
        assessment = metrics.assess_risk(70.0, -3.0, 95.0)
        assert assessment.level == "watch"
        assert assessment.is_at_risk

    def test_several_failed_subjects_is_high_risk(self):
        assessment = metrics.assess_risk(52.0, 0.0, 92.0, failing_subjects=3)
        assert assessment.level == "high"

    def test_no_data_is_not_flagged(self):
        assert metrics.assess_risk(None, None, None).level == "none"

    def test_tone_and_label_match_the_level(self):
        assert metrics.assess_risk(None, None, None).label == "On track"
        assert metrics.assess_risk(28.0, None, None).tone == "negative"


class TestStrengthProfile:
    def test_splits_strongest_from_weakest(self):
        strengths, weaknesses = metrics.strength_profile(
            {
                "Maths": 92.0,
                "Science": 88.0,
                "English": 80.0,
                "Hindi": 55.0,
                "Art": 40.0,
            },
            limit=2,
        )
        assert strengths == ["Maths", "Science"]
        assert weaknesses == ["Art", "Hindi"]

    def test_ignores_subjects_without_a_score(self):
        strengths, _ = metrics.strength_profile({"Maths": None, "Science": 70.0})
        assert strengths == ["Science"]

    def test_does_not_list_a_subject_as_both(self):
        strengths, weaknesses = metrics.strength_profile({"Maths": 70.0}, limit=3)
        assert strengths == ["Maths"]
        assert weaknesses == []


class TestDelta:
    def test_difference_between_two_terms(self):
        assert metrics.delta(72.0, 65.0) == 7.0

    def test_missing_side_yields_none(self):
        assert metrics.delta(72.0, None) is None


class TestPerformanceTone:
    def test_tones(self):
        assert metrics.performance_tone(85) == "positive"
        assert metrics.performance_tone(60) == "neutral"
        assert metrics.performance_tone(20) == "negative"
        assert metrics.performance_tone(None) == "neutral"
