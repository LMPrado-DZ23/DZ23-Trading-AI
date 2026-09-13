"""Pure advisory team contracts and deterministic consensus aggregation."""

from .aggregation import aggregate_advisory_votes
from .contracts import (
    AdvisoryConsensus,
    AdvisoryRating,
    AnalysisBatch,
    ConsensusOutcome,
    ConsensusReviewReason,
    TeamVersion,
    TeamVote,
    VoteStatus,
    canonical_agent_team_bytes,
)

__all__ = [
    "AdvisoryConsensus",
    "AdvisoryRating",
    "AnalysisBatch",
    "ConsensusOutcome",
    "ConsensusReviewReason",
    "TeamVersion",
    "TeamVote",
    "VoteStatus",
    "aggregate_advisory_votes",
    "canonical_agent_team_bytes",
]
