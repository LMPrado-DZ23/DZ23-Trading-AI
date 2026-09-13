"""M6a1 pure advisory multi-team contracts and deterministic consensus."""

import ast
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from tradingagents.platform.agent_teams import (
    AdvisoryConsensus,
    AdvisoryRating,
    AnalysisBatch,
    ConsensusOutcome,
    ConsensusReviewReason,
    TeamVersion,
    TeamVote,
    VoteStatus,
    aggregate_advisory_votes,
    canonical_agent_team_bytes,
)

TENANT = UUID("00000000-0000-4000-8000-000000000101")
RUN = UUID("00000000-0000-4000-8000-000000000102")
INSTRUMENT = UUID("00000000-0000-4000-8000-000000000103")
BATCH = UUID("00000000-0000-4000-8000-000000000104")


def _team(index: int) -> TeamVersion:
    return TeamVersion(
        tenant_id=TENANT,
        portfolio_id=UUID("00000000-0000-4000-8000-000000000105"),
        team_version_id=UUID(f"00000000-0000-4000-8000-{index:012d}"),
        team_name=f"desk-{index}",
        version=1,
        definition_digest=f"{index:x}" * 64,
    )


TEAMS = (_team(1), _team(2), _team(3))


def _batch(**updates: object) -> AnalysisBatch:
    data: dict[str, object] = {
        "batch_id": BATCH,
        "tenant_id": TENANT,
        "portfolio_id": UUID("00000000-0000-4000-8000-000000000105"),
        "analysis_run_id": RUN,
        "instrument_id": INSTRUMENT,
        "mandate_id": UUID("00000000-0000-4000-8000-000000000106"),
        "mandate_version": 3,
        "mandate_digest": "e" * 64,
        "strategy_digest": "f" * 64,
        "input_digest": "a" * 64,
        "aggregation_policy_digest": "b" * 64,
        "required_team_versions": TEAMS,
    }
    data.update(updates)
    return AnalysisBatch(**data)


def _vote(team: TeamVersion, rating: AdvisoryRating = AdvisoryRating.BUY, **updates: object):
    index = int(str(team.team_version_id).rsplit("-", maxsplit=1)[1])
    data: dict[str, object] = {
        "vote_id": UUID(f"00000000-0000-4000-8001-{index:012d}"),
        "batch_id": BATCH,
        "tenant_id": TENANT,
        "portfolio_id": UUID("00000000-0000-4000-8000-000000000105"),
        "job_id": UUID(f"00000000-0000-4000-8002-{index:012d}"),
        "team_version_id": team.team_version_id,
        "team_version_digest": team.digest,
        "status": VoteStatus.SUCCESS,
        "rating": rating,
        "evidence_digest": "c" * 64,
        "analysis_digest": "d" * 64,
    }
    data.update(updates)
    return TeamVote(**data)


def test_contracts_are_frozen_strict_extra_forbid_and_nil_safe() -> None:
    team = TEAMS[0]
    with pytest.raises(ValidationError):
        team.version = 2
    with pytest.raises(ValidationError):
        TeamVersion(
            tenant_id=TENANT,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=0),
            team_name="nil",
            version=1,
            definition_digest="a" * 64,
        )
    with pytest.raises(ValidationError):
        TeamVersion(
            tenant_id=TENANT,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=10),
            team_name="desk",
            version="1",
            definition_digest="a" * 64,
        )
    with pytest.raises(ValidationError):
        TeamVersion(
            tenant_id=TENANT,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=10),
            team_name="desk",
            version=1,
            definition_digest="a" * 64,
            unknown=True,
        )


def test_rating_vocabulary_and_scores_are_closed() -> None:
    assert {rating.value: rating.score for rating in AdvisoryRating} == {
        "Buy": 2,
        "Overweight": 1,
        "Hold": 0,
        "Underweight": -1,
        "Sell": -2,
    }
    with pytest.raises(ValidationError):
        _vote(TEAMS[0], rating="BUY")


def test_batch_requires_unique_canonical_exact_team_versions() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        _batch(required_team_versions=tuple(reversed(TEAMS)))
    with pytest.raises(ValidationError, match="unique"):
        _batch(required_team_versions=(TEAMS[0], TEAMS[0]))
    with pytest.raises(ValidationError, match="exact TeamVersion"):
        _batch(required_team_versions=(TEAMS[0].model_dump(),))
    duplicate_name = _team(4).model_copy(update={"team_name": TEAMS[0].team_name})
    with pytest.raises(ValidationError, match="logical team names"):
        _batch(required_team_versions=(TEAMS[0], duplicate_name))
    wrong_scope = _team(4).model_copy(update={"portfolio_id": UUID(int=999)})
    with pytest.raises(ValidationError, match="batch tenant and portfolio"):
        _batch(required_team_versions=(*TEAMS, wrong_scope))


def test_vote_status_and_rating_are_consistent() -> None:
    with pytest.raises(ValidationError, match="requires a rating"):
        _vote(TEAMS[0], rating=None)
    with pytest.raises(ValidationError, match="cannot carry"):
        _vote(TEAMS[0], status=VoteStatus.REVIEW)


@pytest.mark.parametrize(
    ("ratings", "expected"),
    [
        ((AdvisoryRating.BUY,) * 3, ConsensusOutcome.BUY),
        (
            (AdvisoryRating.BUY, AdvisoryRating.OVERWEIGHT, AdvisoryRating.BUY),
            ConsensusOutcome.OVERWEIGHT,
        ),
        (
            (AdvisoryRating.SELL, AdvisoryRating.UNDERWEIGHT, AdvisoryRating.SELL),
            ConsensusOutcome.UNDERWEIGHT,
        ),
        (
            (AdvisoryRating.BUY, AdvisoryRating.HOLD, AdvisoryRating.BUY),
            ConsensusOutcome.HOLD,
        ),
    ],
)
def test_valid_consensus_uses_conservative_deterministic_rule(ratings, expected) -> None:
    votes = tuple(_vote(team, rating) for team, rating in zip(TEAMS, ratings, strict=True))
    result = aggregate_advisory_votes(_batch(), votes)
    assert result.outcome is expected
    assert result.review_reasons == ()


def test_canonical_order_makes_consensus_byte_identical() -> None:
    votes = tuple(_vote(team) for team in TEAMS)
    forward = aggregate_advisory_votes(_batch(), votes)
    reverse = aggregate_advisory_votes(_batch(), reversed(votes))
    assert forward == reverse
    assert forward.canonical_bytes == reverse.canonical_bytes
    assert forward.digest == reverse.digest
    assert forward.vote_digests == tuple(vote.digest for vote in votes)


def test_equal_primary_order_keys_and_overflow_remain_order_independent() -> None:
    first = _vote(TEAMS[0], evidence_digest="1" * 64)
    tied = first.model_copy(update={"evidence_digest": "2" * 64})
    base = [_vote(TEAMS[1]), _vote(TEAMS[2])]
    forward = aggregate_advisory_votes(_batch(), [first, tied, *base])
    reverse = aggregate_advisory_votes(_batch(), [*reversed(base), tied, first])
    assert forward.outcome is ConsensusOutcome.REVIEW
    assert forward.canonical_bytes == reverse.canonical_bytes
    assert forward.digest == reverse.digest

    overflow = [
        _vote(
            TEAMS[index % 3],
            vote_id=UUID(int=10_000 + index),
            job_id=UUID(int=20_000 + index),
        )
        for index in range(65)
    ]
    over_forward = aggregate_advisory_votes(_batch(), overflow)
    over_reverse = aggregate_advisory_votes(_batch(), reversed(overflow))
    assert over_forward.canonical_bytes == over_reverse.canonical_bytes
    assert over_forward.vote_digests == ()


def test_directional_conflict_fails_closed_to_review() -> None:
    votes = (
        _vote(TEAMS[0], AdvisoryRating.BUY),
        _vote(TEAMS[1], AdvisoryRating.SELL),
        _vote(TEAMS[2], AdvisoryRating.HOLD),
    )
    result = aggregate_advisory_votes(_batch(), votes)
    assert result.outcome is ConsensusOutcome.REVIEW
    assert ConsensusReviewReason.DIRECTIONAL_CONFLICT in result.review_reasons


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "failed",
        "review",
        "bad_digest",
        "wrong_batch",
        "wrong_tenant",
        "wrong_portfolio",
    ],
)
def test_incomplete_failed_or_unbound_votes_require_review(case: str) -> None:
    votes = [_vote(team) for team in TEAMS]
    if case == "missing":
        votes.pop()
    elif case == "failed":
        votes[0] = _vote(TEAMS[0], rating=None, status=VoteStatus.FAILED)
    elif case == "review":
        votes[0] = _vote(TEAMS[0], rating=None, status=VoteStatus.REVIEW)
    elif case == "bad_digest":
        votes[0] = _vote(TEAMS[0], team_version_digest="f" * 64)
    elif case == "wrong_batch":
        votes[0] = _vote(TEAMS[0], batch_id=UUID(int=999))
    elif case == "wrong_tenant":
        votes[0] = _vote(TEAMS[0], tenant_id=UUID(int=999))
    else:
        votes[0] = _vote(TEAMS[0], portfolio_id=UUID(int=999))
    result = aggregate_advisory_votes(_batch(), votes)
    assert result.outcome is ConsensusOutcome.REVIEW
    assert result.review_reasons


def test_duplicate_and_unexpected_team_votes_require_review() -> None:
    votes = [_vote(team) for team in TEAMS]
    votes.append(_vote(TEAMS[0], vote_id=UUID(int=999), job_id=UUID(int=998)))
    duplicate = aggregate_advisory_votes(_batch(), votes)
    assert ConsensusReviewReason.DUPLICATE_TEAM in duplicate.review_reasons

    extra_team = _team(9)
    unexpected = aggregate_advisory_votes(_batch(), [*votes[:3], _vote(extra_team)])
    assert ConsensusReviewReason.UNEXPECTED_TEAM in unexpected.review_reasons

    duplicate_job = [*votes[:3]]
    duplicate_job[1] = _vote(TEAMS[1], job_id=duplicate_job[0].job_id)
    reused = aggregate_advisory_votes(_batch(), duplicate_job)
    assert ConsensusReviewReason.INVALID_VOTE in reused.review_reasons


def test_model_copy_bypass_cannot_acquire_or_influence_a_valid_digest() -> None:
    forged_team = TEAMS[0].model_copy(update={"definition_digest": "not-a-digest"})
    with pytest.raises(ValidationError):
        _ = forged_team.digest
    forged_vote = _vote(TEAMS[0]).model_copy(update={"rating": "arbitrary"})
    with pytest.raises(ValidationError):
        _ = forged_vote.digest
    result = aggregate_advisory_votes(
        _batch(), (forged_vote, _vote(TEAMS[1]), _vote(TEAMS[2]))
    )
    assert result.outcome is ConsensusOutcome.REVIEW
    assert ConsensusReviewReason.INVALID_VOTE in result.review_reasons

    forged_batch = _batch().model_copy(update={"input_digest": "invalid"})
    with pytest.raises(ValidationError):
        _ = forged_batch.digest
    forged_consensus = result.model_copy(update={"outcome": "Buy"})
    with pytest.raises(ValidationError):
        _ = forged_consensus.digest
    with pytest.raises(ValidationError):
        type(result)(
            batch_id=result.batch_id,
            tenant_id=result.tenant_id,
            portfolio_id=result.portfolio_id,
            batch_digest=result.batch_digest,
            required_team_version_ids=result.required_team_version_ids,
            participating_team_version_ids=(UUID(int=0),),
            vote_digests=result.vote_digests,
            outcome=ConsensusOutcome.REVIEW,
            review_reasons=(ConsensusReviewReason.INVALID_VOTE,),
        )


def test_non_review_consensus_cannot_be_constructed_without_complete_votes() -> None:
    result = aggregate_advisory_votes(_batch(), tuple(_vote(team) for team in TEAMS))
    data = result.model_dump(mode="python")
    data["participating_team_version_ids"] = ()
    data["vote_digests"] = ()
    with pytest.raises(ValidationError, match="complete vote"):
        AdvisoryConsensus(**data)
    data = result.model_dump(mode="python")
    data["vote_digests"] = (result.vote_digests[0],) * len(result.vote_digests)
    with pytest.raises(ValidationError, match="complete vote"):
        AdvisoryConsensus(**data)


def test_all_contracts_have_safe_canonical_json_round_trip() -> None:
    batch = _batch()
    vote = _vote(TEAMS[0])
    consensus = aggregate_advisory_votes(batch, tuple(_vote(team) for team in TEAMS))
    for contract in (*TEAMS, batch, vote, consensus):
        encoded = canonical_agent_team_bytes(contract)
        assert type(contract).model_validate_json(encoded) == contract
        assert canonical_agent_team_bytes(type(contract).model_validate_json(encoded)) == encoded


def test_vote_stream_is_bounded_and_fails_closed() -> None:
    votes = [_vote(team) for team in TEAMS]
    votes.extend(
        _vote(TEAMS[0], vote_id=UUID(int=2_000 + index), job_id=UUID(int=3_000 + index))
        for index in range(70)
    )
    result = aggregate_advisory_votes(_batch(), votes)
    assert result.outcome is ConsensusOutcome.REVIEW
    assert ConsensusReviewReason.INVALID_VOTE in result.review_reasons
    assert result.vote_digests == ()


def test_throwing_vote_iterable_and_cyclic_or_deep_identifiers_fail_closed() -> None:
    class ThrowingVotes:
        def __iter__(self):
            yield _vote(TEAMS[0])
            raise RuntimeError("adversarial iterator")

    result = aggregate_advisory_votes(_batch(), ThrowingVotes())
    assert result.outcome is ConsensusOutcome.REVIEW
    assert result.vote_digests == ()
    assert ConsensusReviewReason.INVALID_VOTE in result.review_reasons

    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValidationError, match="container cycle"):
        TeamVersion(
            tenant_id=cyclic,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=10),
            team_name="cycle",
            version=1,
            definition_digest="a" * 64,
        )
    deep: object = UUID(int=10)
    for _ in range(20):
        deep = [deep]
    with pytest.raises(ValidationError, match="validation budget"):
        TeamVersion(
            tenant_id=deep,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=10),
            team_name="deep",
            version=1,
            definition_digest="a" * 64,
        )
    with pytest.raises(ValidationError, match="validation budget"):
        TeamVersion(
            tenant_id=[UUID(int=10)] * 5_000,
            portfolio_id=UUID(int=5),
            team_version_id=UUID(int=10),
            team_name="wide",
            version=1,
            definition_digest="a" * 64,
        )


def test_agent_team_package_has_no_capital_or_broker_dependency() -> None:
    package = Path("tradingagents/platform/agent_teams")
    forbidden_modules = {
        "tradingagents.brokers",
        "tradingagents.platform.execution",
        "tradingagents.platform.portfolio",
    }
    forbidden_fields = {"quantity", "price", "account", "broker", "reservation"}
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(item) for item in forbidden_modules)
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(item)
                    for alias in node.names
                    for item in forbidden_modules
                )
    for contract in (TeamVersion, AnalysisBatch, TeamVote, AdvisoryConsensus):
        assert forbidden_fields.isdisjoint(contract.model_fields)
