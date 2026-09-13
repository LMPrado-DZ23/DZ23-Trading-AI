"""Order-independent deterministic aggregation for advisory team votes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from .contracts import (
    AdvisoryConsensus,
    AnalysisBatch,
    ConsensusOutcome,
    ConsensusReviewReason,
    TeamVote,
    VoteStatus,
    _deep_validate,
)


def _validated_vote(value: Any) -> TeamVote | None:
    try:
        return _deep_validate(value, TeamVote)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return None


def aggregate_advisory_votes(
    batch: AnalysisBatch, votes: Iterable[TeamVote]
) -> AdvisoryConsensus:
    """Aggregate a complete batch without granting any capital authority.

    Invalid, missing, duplicate, failed, review, unexpected, or directionally
    conflicting inputs fail closed to REVIEW. Valid votes use a total canonical
    order, so caller iteration order never affects the consensus bytes.
    """

    checked_batch = _deep_validate(batch, AnalysisBatch)
    required = {
        item.team_version_id: item for item in checked_batch.required_team_versions
    }
    reasons: set[ConsensusReviewReason] = set()
    valid_votes: list[TeamVote] = []
    stream_failed = False
    try:
        iterator = iter(votes)
        for _index in range(65):
            try:
                raw_vote = next(iterator)
            except StopIteration:
                break
            except Exception:
                stream_failed = True
                break
            checked_vote = _validated_vote(raw_vote)
            if checked_vote is None:
                reasons.add(ConsensusReviewReason.INVALID_VOTE)
                continue
            valid_votes.append(checked_vote)
        else:
            stream_failed = True
    except Exception:
        stream_failed = True

    if stream_failed:
        reasons.add(ConsensusReviewReason.INVALID_VOTE)
        valid_votes.clear()

    ordered_votes = tuple(
        sorted(
            valid_votes,
            key=lambda item: (
                str(item.team_version_id),
                str(item.job_id),
                str(item.vote_id),
                item.digest,
            ),
        )
    )
    if len({vote.vote_id for vote in ordered_votes}) != len(ordered_votes):
        reasons.add(ConsensusReviewReason.INVALID_VOTE)
    if len({vote.job_id for vote in ordered_votes}) != len(ordered_votes):
        reasons.add(ConsensusReviewReason.INVALID_VOTE)
    by_team: dict[object, list[TeamVote]] = {}
    for vote in ordered_votes:
        if (
            vote.batch_id != checked_batch.batch_id
            or vote.tenant_id != checked_batch.tenant_id
            or vote.portfolio_id != checked_batch.portfolio_id
        ):
            reasons.add(ConsensusReviewReason.INVALID_VOTE)
            continue
        expected_team = required.get(vote.team_version_id)
        if expected_team is None:
            reasons.add(ConsensusReviewReason.UNEXPECTED_TEAM)
            continue
        if vote.team_version_digest != expected_team.digest:
            reasons.add(ConsensusReviewReason.INVALID_VOTE)
            continue
        by_team.setdefault(vote.team_version_id, []).append(vote)

    if set(by_team) != set(required):
        reasons.add(ConsensusReviewReason.MISSING_TEAM)
    if any(len(team_votes) != 1 for team_votes in by_team.values()):
        reasons.add(ConsensusReviewReason.DUPLICATE_TEAM)

    accepted_votes = tuple(
        team_votes[0]
        for team_id, team_votes in sorted(by_team.items(), key=lambda item: str(item[0]))
        if team_id in required and len(team_votes) == 1
    )
    if any(vote.status is not VoteStatus.SUCCESS for vote in accepted_votes):
        reasons.add(ConsensusReviewReason.TEAM_NOT_SUCCESSFUL)

    ratings = tuple(vote.rating for vote in accepted_votes if vote.rating is not None)
    scores = tuple(rating.score for rating in ratings)
    if any(score > 0 for score in scores) and any(score < 0 for score in scores):
        reasons.add(ConsensusReviewReason.DIRECTIONAL_CONFLICT)

    if reasons:
        outcome = ConsensusOutcome.REVIEW
    elif any(score == 0 for score in scores):
        outcome = ConsensusOutcome.HOLD
    elif scores and (
        all(score > 0 for score in scores) or all(score < 0 for score in scores)
    ):
        outcome = ConsensusOutcome(
            min(ratings, key=lambda rating: abs(rating.score)).value
        )
    else:
        outcome = ConsensusOutcome.REVIEW
        reasons.add(ConsensusReviewReason.INVALID_VOTE)

    return AdvisoryConsensus(
        batch_id=checked_batch.batch_id,
        tenant_id=checked_batch.tenant_id,
        portfolio_id=checked_batch.portfolio_id,
        batch_digest=checked_batch.digest,
        required_team_version_ids=tuple(sorted(required, key=str)),
        participating_team_version_ids=tuple(sorted(by_team, key=str)),
        vote_digests=tuple(vote.digest for vote in ordered_votes),
        outcome=outcome,
        review_reasons=tuple(sorted(reasons, key=str)),
    )
