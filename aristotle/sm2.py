"""SM-2 spaced repetition algorithm — ADR-001 §2.

SM-2 (SuperMemo 2) is the spaced repetition algorithm ARISTOTLE uses to
schedule when a concept comes due for review. ADR-001 §2 says "VIGIL is
reused from core" for SM-2, but the platform's Vigil actor is a quality
evaluation actor, NOT a spaced repetition scheduler (platform gap logged
as ARISTOTLE-DEBT-006). ARISTOTLE implements SM-2 directly — the algorithm
is self-contained and doesn't belong in the platform anyway.

The algorithm (from https://www.supermemo.com/en/blog/application-of-a-computer-to-improve-the-results-obtained-in-working-with-the-supermemo-method):
  - Quality of response: 0-5 (0 = complete blackout, 5 = perfect)
  - Easiness Factor (EF): starts at 2.5, updated after each review
  - If quality >= 3: repetitions += 1, interval = 1 day (1st), 6 days (2nd),
    interval * EF (subsequent)
  - If quality < 3: repetitions = 0, interval = 1 day (start over)
  - EF update: EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    where q is the quality. EF never goes below 1.3.

ARISTOTLE maps EXAMINER's score (0.0-1.0) to SM-2 quality (0-5) via:
  quality = round(score * 5)

Layer: pure Python, no aip imports. Tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class SM2State:
    """The SM-2 state for one (student, concept) pair.

    Persisted in the aristotle_mastery table.
    """

    easiness_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    next_review_at: str | None = None  # ISO timestamp

    def to_dict(self) -> dict:
        return {
            "easiness_factor": self.easiness_factor,
            "interval_days": self.interval_days,
            "repetitions": self.repetitions,
            "next_review_at": self.next_review_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SM2State":
        return cls(
            easiness_factor=d.get("easiness_factor", 2.5),
            interval_days=d.get("interval_days", 0),
            repetitions=d.get("repetitions", 0),
            next_review_at=d.get("next_review_at"),
        )


def score_to_quality(score: float) -> int:
    """Map EXAMINER's score (0.0-1.0) to SM-2 quality (0-5).

    0.0 → 0 (complete blackout)
    0.2 → 1
    0.4 → 2
    0.6 → 3 (barely correct)
    0.8 → 4
    1.0 → 5 (perfect)
    """
    return max(0, min(5, round(score * 5)))


def update_sm2(state: SM2State, score: float) -> SM2State:
    """Update SM-2 state after a review with the given score.

    Args:
        state: current SM-2 state.
        score: EXAMINER's evaluation score (0.0-1.0).

    Returns:
        New SM2State with updated EF, interval, repetitions, next_review_at.
    """
    quality = score_to_quality(score)

    # Update Easiness Factor
    # EF' = EF + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    ef = state.easiness_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    # EF never goes below 1.3
    ef = max(1.3, ef)

    if quality >= 3:
        # Correct response — advance
        repetitions = state.repetitions + 1
        if repetitions == 1:
            interval = 1
        elif repetitions == 2:
            interval = 6
        else:
            interval = round(state.interval_days * ef)
    else:
        # Incorrect response — start over
        repetitions = 0
        interval = 1

    # Calculate next review timestamp
    now = datetime.now(timezone.utc)
    next_review = now + timedelta(days=interval)
    next_review_at = next_review.isoformat()

    return SM2State(
        easiness_factor=ef,
        interval_days=interval,
        repetitions=repetitions,
        next_review_at=next_review_at,
    )


def is_due(state: SM2State) -> bool:
    """Check if a concept is due for review.

    A concept is due if:
    - next_review_at is None (never reviewed), OR
    - next_review_at is in the past.
    """
    if state.next_review_at is None:
        return True
    try:
        next_review = datetime.fromisoformat(state.next_review_at)
        now = datetime.now(timezone.utc)
        return now >= next_review
    except (ValueError, TypeError):
        return True  # malformed timestamp → treat as due


def mastery_probability(
    repetitions: int,
    hint_assisted_correct: int = 0,
    transfer_correct: int = 0,
    slip_count: int = 0,
) -> float:
    """Compute a BKT-inspired mastery probability (ADR-002 §8, B.5 item 8).

    A pragmatic extension of SM-2 that captures the most important BKT
    insights (slip/guess distinction, skill transfer) without requiring
    training data or neural infrastructure. This is a READ-ONLY diagnostic
    — it does NOT change the SM-2 interval logic. The SM-2 scheduler
    continues to drive next_review_at; mastery_probability is surfaced
    alongside it for observability.

    Formula:
        p = (unassisted_correct + 0.5 * hint_assisted_correct
             + 1.5 * transfer_correct) / max(1, total_attempts)
        p -= 0.15 * slip_rate
        return clamp(p, 0.0, 1.0)

    where:
      - total_attempts = repetitions (SM-2 consecutive correct reviews)
      - unassisted_correct = repetitions - hint_assisted_correct
        (correct answers that did NOT require hints)
      - slip_rate = slip_count / max(1, repetitions)
        (wrong answers on known concepts — penalizes overreliance)

    Weights:
      - unassisted_correct: 1.0 (full credit — independent recall)
      - hint_assisted_correct: 0.5 (partial credit — needed a nudge)
      - transfer_correct: 1.5 (bonus — applied concept to new situation)
      - slip penalty: -0.15 per slip_rate unit (slips signal fragility)

    Args:
        repetitions: SM-2 repetitions (consecutive correct reviews).
        hint_assisted_correct: correct answers that required hints (M003).
        transfer_correct: correct transfer questions (M003).
        slip_count: wrong answers on known concepts (M003).

    Returns:
        float in [0.0, 1.0] — the estimated probability of mastery.
    """
    total_attempts = max(1, repetitions)
    unassisted_correct = max(0, repetitions - hint_assisted_correct)

    numerator = (
        unassisted_correct + 0.5 * hint_assisted_correct + 1.5 * transfer_correct
    )
    p = numerator / total_attempts

    slip_rate = slip_count / max(1, repetitions)
    p -= 0.15 * slip_rate

    return max(0.0, min(1.0, p))
