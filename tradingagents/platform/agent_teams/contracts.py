"""Immutable, advisory-only contracts for parallel analysis teams.

These types deliberately cannot express an order or a capital authority.  They
bind multiple independently versioned team results to one analysis batch and
make their canonical bytes suitable for deterministic aggregation and audit.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TeamName = Annotated[
    str,
    Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_.-]*$"),
]

_DIGEST_PREFIX = b"tradingagents/agent-teams/"
_MAX_TEAMS = 32
_MAX_CANONICAL_BYTES = 256 * 1024
_MAX_IDENTIFIER_SCAN_ITEMS = 4_096
_MAX_IDENTIFIER_SCAN_DEPTH = 16


class AdvisoryRating(StrEnum):
    """The only directional vocabulary an analysis team may emit."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"

    @property
    def score(self) -> int:
        return {
            AdvisoryRating.BUY: 2,
            AdvisoryRating.OVERWEIGHT: 1,
            AdvisoryRating.HOLD: 0,
            AdvisoryRating.UNDERWEIGHT: -1,
            AdvisoryRating.SELL: -2,
        }[self]


class VoteStatus(StrEnum):
    SUCCESS = "success"
    REVIEW = "review"
    FAILED = "failed"


class ConsensusOutcome(StrEnum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"
    REVIEW = "Review"


class ConsensusReviewReason(StrEnum):
    MISSING_TEAM = "missing_team"
    UNEXPECTED_TEAM = "unexpected_team"
    DUPLICATE_TEAM = "duplicate_team"
    INVALID_VOTE = "invalid_vote"
    TEAM_NOT_SUCCESSFUL = "team_not_successful"
    DIRECTIONAL_CONFLICT = "directional_conflict"


class AgentTeamContract(BaseModel):
    """Strict, frozen base with nil-UUID rejection."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    @field_validator("*", mode="before")
    @classmethod
    def reject_nil_uuid(cls, value: Any, info: ValidationInfo) -> Any:
        stack: list[tuple[Any, int]] = [(value, 0)]
        visited_containers: set[int] = set()
        inspected = 0
        while stack:
            item, depth = stack.pop()
            inspected += 1
            if inspected > _MAX_IDENTIFIER_SCAN_ITEMS or depth > _MAX_IDENTIFIER_SCAN_DEPTH:
                raise ValueError("agent-team identifier input exceeds the validation budget")
            if type(item) is UUID:
                if item.int == 0:
                    raise ValueError("agent-team identifiers must not use the nil UUID")
                continue
            if not isinstance(item, (list, tuple, dict)):
                continue
            identity = id(item)
            if identity in visited_containers:
                raise ValueError("agent-team identifier input contains a container cycle")
            visited_containers.add(identity)
            children = item.values() if isinstance(item, dict) else item
            if len(item) > _MAX_IDENTIFIER_SCAN_ITEMS:
                raise ValueError("agent-team identifier input exceeds the validation budget")
            materialized_children = tuple(children)
            if inspected + len(stack) + len(materialized_children) > _MAX_IDENTIFIER_SCAN_ITEMS:
                raise ValueError("agent-team identifier input exceeds the validation budget")
            stack.extend((child, depth + 1) for child in materialized_children)
        if info.mode == "json" and type(value) is list:
            return tuple(value)
        return value


def _canonical_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        raise ValueError("agent-team payload exceeds the nesting limit")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value.bit_length() > 64:
            raise ValueError("agent-team integer exceeds the resource limit")
        return value
    if isinstance(value, str):
        if len(value) > 4_096:
            raise ValueError("agent-team string exceeds the resource limit")
        return value
    if isinstance(value, dict):
        if len(value) > 256 or any(type(key) is not str for key in value):
            raise ValueError("agent-team mappings require bounded string keys")
        return {
            key: _canonical_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 256:
            raise ValueError("agent-team sequence exceeds the resource limit")
        return [_canonical_value(item, depth=depth + 1) for item in value]
    if value is None:
        return None
    raise ValueError("agent-team payload contains an unsupported scalar type")


def canonical_agent_team_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    """Return bounded, stable JSON bytes for an already validated contract."""

    if isinstance(value, AgentTeamContract):
        value = _deep_validate(value, type(value))
    payload = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_CANONICAL_BYTES:
        raise ValueError("agent-team canonical payload exceeds the byte limit")
    return encoded


def _domain_digest(domain: str, value: BaseModel | dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(_DIGEST_PREFIX)
    digest.update(domain.encode("ascii"))
    digest.update(b"\x00")
    digest.update(canonical_agent_team_bytes(value))
    return digest.hexdigest()


def _deep_validate(value: AgentTeamContract, expected_type: type[AgentTeamContract]):
    if type(value) is not expected_type:
        raise ValueError("agent-team contract type is not exact")
    fields = {name: getattr(value, name) for name in expected_type.model_fields}
    return expected_type.model_validate(fields)


class TeamVersion(AgentTeamContract):
    schema_version: Literal[1] = 1
    tenant_id: UUID
    portfolio_id: UUID
    team_version_id: UUID
    team_name: TeamName
    version: int = Field(ge=1, le=2_147_483_647)
    definition_digest: Digest

    @property
    def digest(self) -> str:
        checked = _deep_validate(self, TeamVersion)
        return _domain_digest("team-version/v1", checked)


class AnalysisBatch(AgentTeamContract):
    schema_version: Literal[1] = 1
    batch_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    analysis_run_id: UUID
    instrument_id: UUID
    mandate_id: UUID
    mandate_version: int = Field(ge=1, le=2_147_483_647)
    mandate_digest: Digest
    strategy_digest: Digest
    input_digest: Digest
    aggregation_policy_digest: Digest
    required_team_versions: tuple[TeamVersion, ...] = Field(
        min_length=1, max_length=_MAX_TEAMS
    )

    @field_validator("required_team_versions", mode="before")
    @classmethod
    def team_versions_are_exact(
        cls, value: Any, info: ValidationInfo
    ) -> tuple[TeamVersion, ...]:
        if info.mode == "json":
            if type(value) not in (list, tuple) or len(value) > _MAX_TEAMS:
                raise ValueError("JSON required teams must be a bounded array")
            return tuple(value)
        if type(value) is not tuple or any(type(item) is not TeamVersion for item in value):
            raise ValueError("required teams must be exact TeamVersion instances")
        return value

    @model_validator(mode="after")
    def validate_required_teams(self) -> AnalysisBatch:
        ids = tuple(item.team_version_id for item in self.required_team_versions)
        names = tuple(item.team_name for item in self.required_team_versions)
        if len(set(ids)) != len(ids):
            raise ValueError("required team versions must be unique")
        if len(set(names)) != len(names):
            raise ValueError("required logical team names must be unique")
        if ids != tuple(sorted(ids, key=str)):
            raise ValueError("required team versions must use canonical identifier order")
        for item in self.required_team_versions:
            if item.tenant_id != self.tenant_id or item.portfolio_id != self.portfolio_id:
                raise ValueError("required teams must belong to the batch tenant and portfolio")
            _ = item.digest
        return self

    @property
    def digest(self) -> str:
        checked = _deep_validate(self, AnalysisBatch)
        return _domain_digest("analysis-batch/v1", checked)


class TeamVote(AgentTeamContract):
    """One advisory result; it contains no capital or execution dimensions."""

    schema_version: Literal[1] = 1
    vote_id: UUID
    batch_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    job_id: UUID
    team_version_id: UUID
    team_version_digest: Digest
    status: VoteStatus
    rating: AdvisoryRating | None
    evidence_digest: Digest
    analysis_digest: Digest

    @model_validator(mode="after")
    def validate_outcome(self) -> TeamVote:
        if self.status is VoteStatus.SUCCESS and self.rating is None:
            raise ValueError("a successful team vote requires a rating")
        if self.status is not VoteStatus.SUCCESS and self.rating is not None:
            raise ValueError("a non-successful team vote cannot carry a rating")
        return self

    @property
    def digest(self) -> str:
        checked = _deep_validate(self, TeamVote)
        return _domain_digest("team-vote/v1", checked)


class AdvisoryConsensus(AgentTeamContract):
    """Deterministic advisory output; REVIEW carries no directional instruction."""

    schema_version: Literal[1] = 1
    batch_id: UUID
    tenant_id: UUID
    portfolio_id: UUID
    batch_digest: Digest
    required_team_version_ids: tuple[UUID, ...] = Field(
        min_length=1, max_length=_MAX_TEAMS
    )
    participating_team_version_ids: tuple[UUID, ...] = Field(
        min_length=0, max_length=_MAX_TEAMS
    )
    vote_digests: tuple[Digest, ...] = Field(min_length=0, max_length=_MAX_TEAMS * 2)
    outcome: ConsensusOutcome
    review_reasons: tuple[ConsensusReviewReason, ...] = Field(
        min_length=0, max_length=len(ConsensusReviewReason)
    )

    @model_validator(mode="after")
    def validate_consensus_shape(self) -> AdvisoryConsensus:
        if self.required_team_version_ids != tuple(
            sorted(self.required_team_version_ids, key=str)
        ):
            raise ValueError("required consensus teams must use canonical identifier order")
        if len(set(self.required_team_version_ids)) != len(self.required_team_version_ids):
            raise ValueError("required consensus teams must be unique")
        if self.participating_team_version_ids != tuple(
            sorted(self.participating_team_version_ids, key=str)
        ):
            raise ValueError("participating teams must use canonical identifier order")
        if len(set(self.participating_team_version_ids)) != len(
            self.participating_team_version_ids
        ):
            raise ValueError("participating teams must be unique")
        if self.review_reasons != tuple(sorted(set(self.review_reasons), key=str)):
            raise ValueError("review reasons must be unique and canonical")
        if self.outcome is ConsensusOutcome.REVIEW and not self.review_reasons:
            raise ValueError("REVIEW consensus requires at least one reason")
        if self.outcome is not ConsensusOutcome.REVIEW and self.review_reasons:
            raise ValueError("directional consensus cannot include review reasons")
        if self.outcome is not ConsensusOutcome.REVIEW and (
            self.participating_team_version_ids != self.required_team_version_ids
            or len(self.vote_digests) != len(self.required_team_version_ids)
            or len(set(self.vote_digests)) != len(self.vote_digests)
        ):
            raise ValueError("directional consensus requires one complete vote per required team")
        return self

    @property
    def digest(self) -> str:
        checked = _deep_validate(self, AdvisoryConsensus)
        return _domain_digest("advisory-consensus/v1", checked)

    @property
    def canonical_bytes(self) -> bytes:
        checked = _deep_validate(self, AdvisoryConsensus)
        return canonical_agent_team_bytes(checked)
