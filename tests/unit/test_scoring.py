from modules.scoring import AlertScore


def test_default_score_is_zero():
    score = AlertScore()
    assert score.total() == 0.0


def test_total_average_works():
    score = AlertScore(
        source_reliability=1.0,
        keyword_strength=0.5,
        recency=0.5,
        uniqueness=0.0,
        watchlist_match=1.0,
    )
    assert score.total() == 0.6


def test_labels_work_for_high_medium_low():
    high = AlertScore(1.0, 1.0, 1.0, 1.0, 0.75)
    medium = AlertScore(0.4, 0.4, 0.4, 0.4, 0.4)
    low = AlertScore(0.1, 0.2, 0.3, 0.2, 0.1)
    assert high.label() == "high"
    assert medium.label() == "medium"
    assert low.label() == "low"


def test_values_above_one_are_clamped():
    score = AlertScore(1.2, 1.5, 3.0, 1.01, 2.0)
    assert score.source_reliability == 1.0
    assert score.keyword_strength == 1.0
    assert score.recency == 1.0
    assert score.uniqueness == 1.0
    assert score.watchlist_match == 1.0


def test_values_below_zero_are_clamped():
    score = AlertScore(-0.1, -1.0, -3.0, -0.01, -2.0)
    assert score.source_reliability == 0.0
    assert score.keyword_strength == 0.0
    assert score.recency == 0.0
    assert score.uniqueness == 0.0
    assert score.watchlist_match == 0.0
