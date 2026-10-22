from dataclasses import dataclass


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class AlertScore:
    source_reliability: float = 0.0
    keyword_strength: float = 0.0
    recency: float = 0.0
    uniqueness: float = 0.0
    watchlist_match: float = 0.0

    def __post_init__(self):
        self.source_reliability = _clamp(self.source_reliability)
        self.keyword_strength = _clamp(self.keyword_strength)
        self.recency = _clamp(self.recency)
        self.uniqueness = _clamp(self.uniqueness)
        self.watchlist_match = _clamp(self.watchlist_match)

    def total(self) -> float:
        return (
            self.source_reliability
            + self.keyword_strength
            + self.recency
            + self.uniqueness
            + self.watchlist_match
        ) / 5.0

    def label(self) -> str:
        score = self.total()
        if score >= 0.75:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"
