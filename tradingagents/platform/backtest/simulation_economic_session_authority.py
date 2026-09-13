"""Integrated authenticated authority for complete M5.2c economic sessions.

This is the sole production-code consumer of the private economic reducer and
corporate-cash basis methods. It accepts opaque authority references, resolves
all market/economic evidence itself, and publishes one authenticated economic
checkpoint before advancing its dedicated CAS stream.

The local CAS implementation remains development/test only. Production must
provide one PostgreSQL transaction for checkpoint bytes, lineage, head and
receipt visibility. This module is backtest-only and has no broker/live path.
"""

from __future__ import annotations

import hmac
from datetime import UTC, date, datetime
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from tradingagents.platform.backtest.contracts import (
    BacktestSpecV3,
    DatasetRole,
    canonical_digest,
)
from tradingagents.platform.backtest.repository import (
    FrozenDatasetRepository,
    resolve_execution_price_authority,
)
from tradingagents.platform.backtest.simulation_authority import (
    AuthorizedSimulationRequest,
    FrozenSimulationAuthorityRepository,
    SimulationCheckpointReference,
    SimulationRequestAlreadyExistsError,
    SimulationRequestReference,
)
from tradingagents.platform.backtest.simulation_checkpoint_head import (
    AuthenticatedSimulationCheckpointReference,
    LocalSimulationCheckpointHeadStore,
    SimulationCheckpointCandidate,
    SimulationCheckpointExpectation,
    SimulationCheckpointHead,
    SimulationCheckpointHeadConflictError,
    SimulationCheckpointHeadStore,
    SimulationCheckpointScope,
)
from tradingagents.platform.backtest.simulation_economic_ledger import (
    DividendEntitlementV2,
    EconomicLedgerPolicyV2,
    EconomicPositionV2,
    EconomicSessionResultV2,
    EconomicSessionTransitionV2,
    FillEligibilityV2,
    SimulationEconomicLedgerStateV2,
    _add,
    _commit_simulation_economic_session_v2,
    _digest,
    _mul,
    create_simulation_economic_ledger_v2,
)
from tradingagents.platform.backtest.simulation_economics import (
    CorporateActionApplication,
    CorporateCashLedgerBasis,
    EconomicPhase,
    SimulationEconomicsPolicy,
)
from tradingagents.platform.backtest.simulation_economics_authority import (
    FrozenSimulationEconomicsAuthority,
    open_simulation_economics_authority,
)
from tradingagents.platform.backtest.simulation_ledger import (
    ExecutionFill,
    SimulationLedgerPolicy,
    SimulationOrder,
    SimulationSide,
    _derive_execution_fill_from_balances,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_DECIMAL_CONTEXT = Context(prec=76)


class EconomicSessionAuthorityError(ValueError):
    """The integrated economic authority could not prove a safe transition."""


class EconomicSessionAuthorityContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )


class EconomicFillAuthorityV2(EconomicSessionAuthorityContract):
    evidence_version: Literal["economic-fill-authority/2"] = "economic-fill-authority/2"
    order_id: UUID
    fill_digest: Digest
    request_authority_digest: Digest
    dataset_id: UUID
    manifest_digest: Digest
    execution_price_bundle_digest: Digest
    execution_price_projection_digest: Digest
    execution_price_provenance_digest: Digest
    submitted_quantity: Decimal
    liquidity_cap: Decimal

    @field_validator("submitted_quantity", "liquidity_cap", mode="before")
    @classmethod
    def exact_decimal(cls, value: Any):
        if isinstance(value, (bool, float)):
            raise ValueError("fill authority decimals must be exact")
        if isinstance(value, str) and len(value) > 80:
            raise ValueError("fill authority decimal exceeds its text bound")
        return value

    @model_validator(mode="after")
    def valid(self):
        for value in (self.submitted_quantity, self.liquidity_cap):
            _, digits, exponent = value.as_tuple()
            if (
                not value.is_finite()
                or len(digits) > 38
                or value.adjusted() >= 38
                or exponent < -18
            ):
                raise ValueError("fill authority decimal exceeds its numeric bound")
        if self.submitted_quantity <= 0 or self.liquidity_cap <= 0:
            raise ValueError("fill authority quantities must be positive")
        if self.submitted_quantity > self.liquidity_cap:
            raise ValueError("submitted quantity exceeds authenticated liquidity")
        return self


class EconomicSessionAuthorityEvidenceV2(EconomicSessionAuthorityContract):
    evidence_version: Literal["economic-session-authority-evidence/2"] = (
        "economic-session-authority-evidence/2"
    )
    session_id: UUID
    session_date: date
    order_request_reference: SimulationRequestReference | None = None
    fills: tuple[EconomicFillAuthorityV2, ...] = Field(max_length=9_000)
    pre_open_authority_digests: tuple[Digest, ...] = Field(min_length=1, max_length=1_000)
    post_close_authority_digests: tuple[Digest, ...] = Field(min_length=1, max_length=1_000)
    transition_request_digest: Digest
    evidence_digest: Digest

    @model_validator(mode="after")
    def valid(self):
        if self.order_request_reference is not None and type(
            self.order_request_reference
        ) is not SimulationRequestReference:
            raise ValueError("order reference uses another contract type")
        if any(type(item) is not EconomicFillAuthorityV2 for item in self.fills):
            raise ValueError("fill evidence uses another contract type")
        order_ids = tuple(item.order_id for item in self.fills)
        if order_ids != tuple(sorted(order_ids, key=str)) or len(set(order_ids)) != len(
            order_ids
        ):
            raise ValueError("fill evidence must be unique and canonical")
        if (self.order_request_reference is None) != (not self.fills):
            raise ValueError("order reference and fill evidence must be present together")
        expected = canonical_digest(
            self.model_dump(mode="python", exclude={"evidence_digest"})
        )
        if not hmac.compare_digest(expected, self.evidence_digest):
            raise ValueError("economic session authority evidence digest is invalid")
        return self


class EconomicLedgerCheckpointV2(EconomicSessionAuthorityContract):
    checkpoint_version: Literal["economic-ledger-checkpoint/2"] = (
        "economic-ledger-checkpoint/2"
    )
    checkpoint_id: UUID
    generation: int = Field(strict=True, ge=1)
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest
    created_at: datetime
    previous_checkpoint_digest: Digest | None = None
    backtest_spec: BacktestSpecV3
    trade_policy: SimulationLedgerPolicy
    economics_policy: SimulationEconomicsPolicy
    economic_policy: EconomicLedgerPolicyV2
    state: SimulationEconomicLedgerStateV2
    session_authorities: tuple[EconomicSessionAuthorityEvidenceV2, ...] = Field(
        max_length=10_000
    )

    @model_validator(mode="after")
    def valid(self):
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("economic checkpoint timestamp must use UTC")
        if self.created_at.utcoffset().total_seconds() != 0:
            raise ValueError("economic checkpoint timestamp must use UTC")
        exact_types = (
            type(self.backtest_spec) is BacktestSpecV3,
            type(self.trade_policy) is SimulationLedgerPolicy,
            type(self.economics_policy) is SimulationEconomicsPolicy,
            type(self.economic_policy) is EconomicLedgerPolicyV2,
            type(self.state) is SimulationEconomicLedgerStateV2,
            all(
                type(item) is EconomicSessionAuthorityEvidenceV2
                for item in self.session_authorities
            ),
        )
        if not all(exact_types):
            raise ValueError("economic checkpoint contains another contract type")
        if (self.generation == 1) != (self.previous_checkpoint_digest is None):
            raise ValueError("economic checkpoint predecessor is inconsistent")
        if (
            self.tenant_id != self.backtest_spec.tenant_id
            or self.tenant_id != self.economics_policy.tenant_id
            or self.tenant_id != self.economic_policy.tenant_id
            or self.backtest_id != self.backtest_spec.backtest_id
            or self.backtest_id != self.economics_policy.backtest_id
            or self.backtest_id != self.economic_policy.backtest_id
            or self.ledger_id != self.trade_policy.ledger_id
            or self.ledger_id != self.economics_policy.ledger_id
            or self.ledger_id != self.economic_policy.ledger_id
            or not hmac.compare_digest(self.backtest_spec.digest, self.backtest_spec_digest)
            or not hmac.compare_digest(
                self.trade_policy.digest, self.backtest_spec.simulation_policy_digest
            )
            or not hmac.compare_digest(
                self.economics_policy.digest, self.backtest_spec.economics_policy_digest
            )
            or not hmac.compare_digest(
                self.economic_policy.trade_ledger_policy_digest,
                self.trade_policy.digest,
            )
            or not hmac.compare_digest(
                self.economic_policy.economics_policy_digest,
                self.economics_policy.digest,
            )
            or self.state.policy != self.economic_policy
            or len(self.session_authorities) != len(self.state.session_receipts)
            or self.generation != len(self.session_authorities) + 1
        ):
            raise ValueError("economic checkpoint custody bindings diverge")
        for evidence, receipt in zip(
            self.session_authorities, self.state.session_receipts, strict=True
        ):
            if (
                evidence.session_id != receipt.transition.session_id
                or evidence.session_date != receipt.transition.session_date
                or not hmac.compare_digest(
                    evidence.transition_request_digest,
                    receipt.transition.request_digest,
                )
                or {item.order_id: item.fill_digest for item in evidence.fills}
                != {
                    fill.order_id: fill.digest
                    for fill in receipt.transition.trade_fills
                }
                or evidence.pre_open_authority_digests
                != tuple(item.authority_digest for item in receipt.transition.pre_open_views)
                or evidence.post_close_authority_digests
                != tuple(item.authority_digest for item in receipt.transition.post_close_views)
            ):
                raise ValueError("session evidence differs from economic receipt")
        return self

    @property
    def checkpoint_digest(self) -> str:
        return canonical_digest(self)


class EconomicCheckpointReferenceV2(EconomicSessionAuthorityContract):
    reference_version: Literal["economic-checkpoint-reference/2"] = (
        "economic-checkpoint-reference/2"
    )
    tenant_id: UUID
    backtest_id: UUID
    ledger_id: UUID
    backtest_spec_digest: Digest
    checkpoint_id: UUID
    checkpoint_digest: Digest
    generation: int = Field(strict=True, ge=1)
    previous_checkpoint_digest: Digest | None = None

    @classmethod
    def from_checkpoint(cls, checkpoint: EconomicLedgerCheckpointV2):
        if type(checkpoint) is not EconomicLedgerCheckpointV2:
            raise EconomicSessionAuthorityError(
                "economic checkpoint reference requires the exact checkpoint contract"
            )
        return cls(
            tenant_id=checkpoint.tenant_id,
            backtest_id=checkpoint.backtest_id,
            ledger_id=checkpoint.ledger_id,
            backtest_spec_digest=checkpoint.backtest_spec_digest,
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_digest=checkpoint.checkpoint_digest,
            generation=checkpoint.generation,
            previous_checkpoint_digest=checkpoint.previous_checkpoint_digest,
        )

    @property
    def authority_record_digest(self) -> str:
        return canonical_digest(
            {
                "authority_record_version": "economic-checkpoint-authority-record/2",
                "reference": self.model_dump(mode="python"),
            }
        )


class AuthorizedEconomicGenesisV2(EconomicSessionAuthorityContract):
    result_version: Literal["authorized-economic-genesis/2"] = (
        "authorized-economic-genesis/2"
    )
    checkpoint: EconomicCheckpointReferenceV2
    head: SimulationCheckpointHead
    result_digest: Digest

    @model_validator(mode="after")
    def valid(self):
        if (
            type(self.checkpoint) is not EconomicCheckpointReferenceV2
            or type(self.head) is not SimulationCheckpointHead
            or self.head.generation != self.checkpoint.generation
            or self.head.checkpoint_id != self.checkpoint.checkpoint_id
            or not hmac.compare_digest(
                self.head.checkpoint_digest, self.checkpoint.checkpoint_digest
            )
            or self.head.scope
            != _scope(
                self.checkpoint.tenant_id,
                self.checkpoint.backtest_id,
                self.checkpoint.ledger_id,
                self.checkpoint.backtest_spec_digest,
            )
            or not hmac.compare_digest(
                self.head.authenticated_reference_digest,
                _head_candidate(self.checkpoint).reference.digest,
            )
        ):
            raise ValueError("economic genesis does not match its committed head")
        expected = canonical_digest(self.model_dump(mode="python", exclude={"result_digest"}))
        if not hmac.compare_digest(expected, self.result_digest):
            raise ValueError("economic genesis result digest is invalid")
        return self


class AuthorizedEconomicSessionCommitV2(EconomicSessionAuthorityContract):
    result_version: Literal["authorized-economic-session-commit/2"] = (
        "authorized-economic-session-commit/2"
    )
    prior_checkpoint: EconomicCheckpointReferenceV2
    committed_checkpoint: EconomicCheckpointReferenceV2
    committed_head: SimulationCheckpointHead
    evidence: EconomicSessionAuthorityEvidenceV2
    ledger_result: EconomicSessionResultV2
    result_digest: Digest

    @model_validator(mode="after")
    def valid(self):
        if (
            type(self.prior_checkpoint) is not EconomicCheckpointReferenceV2
            or type(self.committed_checkpoint) is not EconomicCheckpointReferenceV2
            or type(self.committed_head) is not SimulationCheckpointHead
            or type(self.evidence) is not EconomicSessionAuthorityEvidenceV2
            or type(self.ledger_result) is not EconomicSessionResultV2
        ):
            raise ValueError("economic commit contains another contract type")
        if (
            self.committed_checkpoint.generation != self.prior_checkpoint.generation + 1
            or self.committed_checkpoint.previous_checkpoint_digest
            != self.prior_checkpoint.checkpoint_digest
            or self.committed_head.generation != self.committed_checkpoint.generation
            or self.committed_head.checkpoint_id != self.committed_checkpoint.checkpoint_id
            or self.committed_head.checkpoint_digest
            != self.committed_checkpoint.checkpoint_digest
            or self.committed_head.scope
            != _scope(
                self.committed_checkpoint.tenant_id,
                self.committed_checkpoint.backtest_id,
                self.committed_checkpoint.ledger_id,
                self.committed_checkpoint.backtest_spec_digest,
            )
            or not hmac.compare_digest(
                self.committed_head.authenticated_reference_digest,
                _head_candidate(self.committed_checkpoint).reference.digest,
            )
            or self.evidence.transition_request_digest
            != self.ledger_result.request_digest
            or self.ledger_result.replay_status != "applied"
        ):
            raise ValueError("economic commit lineage or result binding is invalid")
        expected = canonical_digest(self.model_dump(mode="python", exclude={"result_digest"}))
        if not hmac.compare_digest(expected, self.result_digest):
            raise ValueError("economic commit result digest is invalid")
        return self


def _scope(
    tenant_id: UUID,
    backtest_id: UUID,
    ledger_id: UUID,
    spec_digest: str,
) -> SimulationCheckpointScope:
    return SimulationCheckpointScope(
        stream_kind="economic-v2",
        tenant_id=tenant_id,
        backtest_id=backtest_id,
        ledger_id=ledger_id,
        backtest_spec_digest=spec_digest,
    )


def _head_candidate(
    reference: EconomicCheckpointReferenceV2,
) -> SimulationCheckpointCandidate:
    scope = _scope(
        reference.tenant_id,
        reference.backtest_id,
        reference.ledger_id,
        reference.backtest_spec_digest,
    )
    return SimulationCheckpointCandidate(
        reference=AuthenticatedSimulationCheckpointReference(
            scope=scope,
            checkpoint_id=reference.checkpoint_id,
            checkpoint_digest=reference.checkpoint_digest,
            previous_checkpoint_digest=reference.previous_checkpoint_digest,
            authority_record_digest=reference.authority_record_digest,
        )
    )


def _validate_head_store(store: object) -> SimulationCheckpointHeadStore:
    if type(store) is not LocalSimulationCheckpointHeadStore:
        raise EconomicSessionAuthorityError(
            "offline economic custody requires the exact trusted local CAS store"
        )
    return store  # type: ignore[return-value]


def _compare_append_and_verify(
    store: SimulationCheckpointHeadStore,
    scope: SimulationCheckpointScope,
    expected: SimulationCheckpointExpectation,
    candidate: SimulationCheckpointCandidate,
    *,
    visible_head_before: SimulationCheckpointHead | None = None,
) -> SimulationCheckpointHead:
    if candidate.reference.scope != scope:
        raise EconomicSessionAuthorityError("checkpoint candidate uses another scope")
    committed = store.compare_and_append(scope, expected, candidate)
    prior_is_empty = expected.generation == 0
    if (
        type(committed) is not SimulationCheckpointHead
        or committed.scope != scope
        or committed.generation != expected.generation + 1
        or committed.checkpoint_id != candidate.checkpoint_id
        or not hmac.compare_digest(
            committed.checkpoint_digest, candidate.checkpoint_digest
        )
        or not hmac.compare_digest(
            committed.authenticated_reference_digest, candidate.reference.digest
        )
        or (committed.previous_checkpoint_id is None) != prior_is_empty
        or (committed.previous_checkpoint_digest is None) != prior_is_empty
        or (
            not prior_is_empty
            and (
                committed.previous_checkpoint_id != expected.checkpoint_id
                or committed.previous_checkpoint_digest != expected.checkpoint_digest
            )
        )
    ):
        raise SimulationCheckpointHeadConflictError(
            "checkpoint CAS returned a head outside the requested append"
        )
    visible = store.read_head(scope)
    is_current_append = visible == committed
    is_historical_retry = (
        type(visible_head_before) is SimulationCheckpointHead
        and visible_head_before.generation > committed.generation
        and visible == visible_head_before
    )
    if not is_current_append and not is_historical_retry:
        raise SimulationCheckpointHeadConflictError(
            "checkpoint CAS result is not the visible custody head"
        )
    return committed


def _publish_or_load(
    repository: FrozenSimulationAuthorityRepository,
    checkpoint: EconomicLedgerCheckpointV2,
) -> EconomicCheckpointReferenceV2:
    expected = EconomicCheckpointReferenceV2.from_checkpoint(checkpoint)
    try:
        return repository._publish_economic_checkpoint(checkpoint)
    except SimulationRequestAlreadyExistsError as exc:
        existing = repository._load_economic_checkpoint(
            expected,
            expected_tenant_id=checkpoint.tenant_id,
            expected_backtest_id=checkpoint.backtest_id,
            expected_backtest_spec_digest=checkpoint.backtest_spec_digest,
            expected_ledger_id=checkpoint.ledger_id,
        )
        if existing != checkpoint:
            raise EconomicSessionAuthorityError(
                "economic checkpoint identity is bound to different bytes"
            ) from exc
        return expected


def _validate_spec_and_economics(
    backtest_spec: BacktestSpecV3,
    economics_policy: SimulationEconomicsPolicy,
    *,
    expected_tenant_id: UUID,
    expected_backtest_id: UUID,
    expected_backtest_spec_digest: str,
) -> tuple[BacktestSpecV3, SimulationEconomicsPolicy]:
    if type(backtest_spec) is not BacktestSpecV3 or type(
        economics_policy
    ) is not SimulationEconomicsPolicy:
        raise EconomicSessionAuthorityError("genesis requires exact v3 policy contracts")
    try:
        spec = BacktestSpecV3.model_validate(backtest_spec.model_dump(mode="python"))
        economics = SimulationEconomicsPolicy.model_validate(
            economics_policy.model_dump(mode="python")
        )
    except ValidationError as exc:
        raise EconomicSessionAuthorityError("genesis policy contracts are invalid") from exc
    if (
        spec.tenant_id != expected_tenant_id
        or spec.backtest_id != expected_backtest_id
        or not hmac.compare_digest(spec.digest, expected_backtest_spec_digest)
        or not hmac.compare_digest(spec.economics_policy_digest, economics.digest)
        or economics.tenant_id != spec.tenant_id
        or economics.backtest_id != spec.backtest_id
    ):
        raise EconomicSessionAuthorityError("genesis policy identity is not anchored")
    return spec, economics


def _validate_genesis_instrument_bindings(
    spec: BacktestSpecV3,
    trade_policy: SimulationLedgerPolicy,
    economics_policy: SimulationEconomicsPolicy,
) -> None:
    tradeable_references = tuple(
        sorted(
            (
                reference
                for reference in spec.dataset_references
                if reference.role is DatasetRole.TRADEABLE
            ),
            key=lambda reference: str(reference.instrument_id),
        )
    )
    rules = tuple(trade_policy.instrument_rules)
    bindings = tuple(economics_policy.instrument_bindings)
    expected_ids = tuple(reference.instrument_id for reference in tradeable_references)
    if (
        tuple(rule.instrument_id for rule in rules) != expected_ids
        or tuple(binding.instrument_id for binding in bindings) != expected_ids
    ):
        raise EconomicSessionAuthorityError(
            "genesis policies must cover every tradeable instrument exactly once"
        )
    for reference, rule, binding in zip(
        tradeable_references, rules, bindings, strict=True
    ):
        if (
            binding.dataset_id != reference.dataset_id
            or not hmac.compare_digest(
                binding.manifest_digest, reference.manifest_digest
            )
            or binding.quote_currency != rule.currency
        ):
            raise EconomicSessionAuthorityError(
                "genesis instrument policies do not match the frozen dataset bindings"
            )


def provision_authenticated_economic_genesis_v2(
    authority_repository: FrozenSimulationAuthorityRepository,
    head_store: SimulationCheckpointHeadStore,
    trade_checkpoint_reference: SimulationCheckpointReference,
    backtest_spec: BacktestSpecV3,
    economics_policy: SimulationEconomicsPolicy,
    *,
    expected_tenant_id: UUID,
    expected_backtest_id: UUID,
    expected_backtest_spec_digest: str,
) -> AuthorizedEconomicGenesisV2:
    """Create generation one solely from an authenticated pristine trade checkpoint."""

    if type(authority_repository) is not FrozenSimulationAuthorityRepository:
        raise EconomicSessionAuthorityError("genesis requires the repository authority")
    store = _validate_head_store(head_store)
    spec, economics = _validate_spec_and_economics(
        backtest_spec,
        economics_policy,
        expected_tenant_id=expected_tenant_id,
        expected_backtest_id=expected_backtest_id,
        expected_backtest_spec_digest=expected_backtest_spec_digest,
    )
    trade_checkpoint = authority_repository.load_checkpoint(
        trade_checkpoint_reference,
        expected_tenant_id=expected_tenant_id,
        expected_backtest_id=expected_backtest_id,
        expected_backtest_spec_digest=expected_backtest_spec_digest,
    )
    trade_policy = trade_checkpoint.policy
    if (
        trade_checkpoint.state.processed_batches
        or len(trade_checkpoint.state.events) != 1
        or trade_checkpoint.state.positions
        or trade_checkpoint.state.cash != trade_policy.initial_cash
        or not hmac.compare_digest(
            trade_policy.digest, spec.simulation_policy_digest
        )
        or economics.ledger_id != trade_policy.ledger_id
        or economics.functional_currency != trade_policy.base_currency
        or economics.money_quantum != trade_policy.money_quantum
    ):
        raise EconomicSessionAuthorityError(
            "economic genesis requires a pristine, spec-bound trade checkpoint"
        )
    _validate_genesis_instrument_bindings(spec, trade_policy, economics)
    instrument_ids = tuple(sorted(spec.instrument_ids, key=str))
    economic_policy = EconomicLedgerPolicyV2(
        tenant_id=spec.tenant_id,
        backtest_id=spec.backtest_id,
        ledger_id=trade_policy.ledger_id,
        backtest_spec_digest=spec.digest,
        trade_ledger_policy_digest=trade_policy.digest,
        economics_policy_digest=economics.digest,
        opened_at=trade_policy.opened_at,
        functional_currency=economics.functional_currency,
        money_quantum=economics.money_quantum,
        initial_cash=trade_policy.initial_cash,
        instrument_ids=instrument_ids,
        initial_positions=tuple(
            EconomicPositionV2(
                instrument_id=instrument_id,
                quantity=Decimal("0"),
                cost_basis=Decimal("0"),
                currency=economics.functional_currency,
            )
            for instrument_id in instrument_ids
        ),
    )
    state = create_simulation_economic_ledger_v2(
        economic_policy, expected_policy_digest=economic_policy.digest
    )
    checkpoint_id = uuid5(
        NAMESPACE_URL,
        "tradingagents/economic-checkpoint/2:genesis:"
        f"{spec.digest}:{trade_checkpoint.checkpoint_digest}:{state.state_hash}",
    )
    checkpoint = EconomicLedgerCheckpointV2(
        checkpoint_id=checkpoint_id,
        generation=1,
        tenant_id=spec.tenant_id,
        backtest_id=spec.backtest_id,
        ledger_id=trade_policy.ledger_id,
        backtest_spec_digest=spec.digest,
        created_at=trade_checkpoint.created_at,
        backtest_spec=spec,
        trade_policy=trade_policy,
        economics_policy=economics,
        economic_policy=economic_policy,
        state=state,
        session_authorities=(),
    )
    reference = _publish_or_load(authority_repository, checkpoint)
    scope = _scope(spec.tenant_id, spec.backtest_id, trade_policy.ledger_id, spec.digest)
    head = _compare_append_and_verify(
        store,
        scope,
        SimulationCheckpointExpectation.empty(),
        _head_candidate(reference),
    )
    payload = {
        "result_version": "authorized-economic-genesis/2",
        "checkpoint": reference,
        "head": head,
    }
    return AuthorizedEconomicGenesisV2(
        **payload, result_digest=_digest(payload)
    )


def _open_economic_authorities(
    checkpoint: EconomicLedgerCheckpointV2,
    dataset_repository: FrozenDatasetRepository,
) -> dict[UUID, FrozenSimulationEconomicsAuthority]:
    refs = tuple(
        sorted(
            (
                item
                for item in checkpoint.backtest_spec.dataset_references
                if item.role is DatasetRole.TRADEABLE
            ),
            key=lambda item: str(item.instrument_id),
        )
    )
    if tuple(item.instrument_id for item in refs) != checkpoint.economic_policy.instrument_ids:
        raise EconomicSessionAuthorityError(
            "tradeable datasets differ from economic policy instruments"
        )
    return {
        item.instrument_id: open_simulation_economics_authority(
            dataset_repository,
            item.dataset_id,
            backtest_spec=checkpoint.backtest_spec,
            ledger_policy=checkpoint.trade_policy,
            economics_policy=checkpoint.economics_policy,
            expected_tenant_id=checkpoint.tenant_id,
            expected_backtest_id=checkpoint.backtest_id,
            expected_backtest_spec_digest=checkpoint.backtest_spec_digest,
            expected_economics_policy_digest=checkpoint.economics_policy.digest,
        )
        for item in refs
    }


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _load_request_and_derive_fills(
    checkpoint: EconomicLedgerCheckpointV2,
    dataset_repository: FrozenDatasetRepository,
    authority_repository: FrozenSimulationAuthorityRepository,
    reference: SimulationRequestReference | None,
    session_date: date,
    pre_open_views,
) -> tuple[UUID, tuple[ExecutionFill, ...], tuple[EconomicFillAuthorityV2, ...], SimulationRequestReference | None]:
    if reference is None:
        session_id = uuid5(
            NAMESPACE_URL,
            "tradingagents/economic-session/2:empty:"
            f"{checkpoint.backtest_spec_digest}:{checkpoint.ledger_id}:{session_date.isoformat()}",
        )
        return session_id, (), (), None
    request = authority_repository.load_request(
        reference,
        expected_tenant_id=checkpoint.tenant_id,
        expected_backtest_id=checkpoint.backtest_id,
        expected_backtest_spec_digest=checkpoint.backtest_spec_digest,
        expected_ledger_id=checkpoint.ledger_id,
        expected_policy_digest=checkpoint.trade_policy.digest,
    )
    if type(request) is not AuthorizedSimulationRequest:
        raise EconomicSessionAuthorityError("order authority returned another contract type")
    trusted_reference = SimulationRequestReference(
        tenant_id=request.tenant_id,
        backtest_id=request.backtest_id,
        backtest_spec_digest=request.backtest_spec_digest,
        ledger_id=request.ledger_id,
        batch_id=request.batch_id,
        authority_digest=request.authority_digest,
    )
    pre_by_instrument = {item.instrument_id: item for item in pre_open_views}
    refs = {
        item.instrument_id: item
        for item in checkpoint.backtest_spec.dataset_references
        if item.role is DatasetRole.TRADEABLE
    }
    rules = {item.instrument_id: item for item in checkpoint.trade_policy.instrument_rules}
    positions = {item.instrument_id: item for item in checkpoint.state.positions}
    cash = checkpoint.state.cash
    fills: list[ExecutionFill] = []
    evidence: list[EconomicFillAuthorityV2] = []
    execution_at: datetime | None = None
    if request.created_at < checkpoint.created_at:
        raise EconomicSessionAuthorityError(
            "authorized order request predates the economic checkpoint"
        )
    for authorized in sorted(
        request.orders, key=lambda item: (str(item.instrument_id), str(item.order_id))
    ):
        pre_view = pre_by_instrument.get(authorized.instrument_id)
        if pre_view is None:
            raise EconomicSessionAuthorityError("order instrument has no complete PRE_OPEN view")
        if pre_view.view.actions:
            raise EconomicSessionAuthorityError(
                "orders affected by PRE_OPEN corporate actions require reauthorization"
            )
        dataset_reference = refs.get(authorized.instrument_id)
        rule = rules.get(authorized.instrument_id)
        if (
            dataset_reference is None
            or rule is None
            or dataset_reference.dataset_id != authorized.dataset_id
            or not hmac.compare_digest(
                dataset_reference.manifest_digest, authorized.manifest_digest
            )
        ):
            raise EconomicSessionAuthorityError(
                "authorized order is outside the frozen economic specification"
            )
        resolved = resolve_execution_price_authority(
            dataset_repository,
            authorized.dataset_id,
            expected_tenant_id=checkpoint.tenant_id,
            expected_instrument_id=authorized.instrument_id,
            expected_manifest_digest=authorized.manifest_digest,
            decision_at=authorized.decision_at,
            decision_session_date=authorized.decision_session_date,
            expected_eligible_view_digest=authorized.eligible_view_digest,
        )
        if resolved.price.session_date != session_date:
            raise EconomicSessionAuthorityError(
                "authorized next-open fill belongs to another economic session"
            )
        if execution_at is None:
            execution_at = resolved.price.execution_at
        elif execution_at != resolved.price.execution_at:
            raise EconomicSessionAuthorityError("orders do not share one execution session")
        with localcontext(_DECIMAL_CONTEXT):
            available = (
                resolved.price.decision_volume
                * checkpoint.backtest_spec.max_volume_participation
            )
        liquidity_cap = _floor_to_step(available, rule.lot_size)
        submitted = _floor_to_step(
            min(authorized.requested_quantity, liquidity_cap), rule.lot_size
        )
        if submitted <= 0:
            raise EconomicSessionAuthorityError("authorized order has no executable liquidity")
        position = positions[authorized.instrument_id]
        if position.fill_eligibility is not FillEligibilityV2.ACTIVE:
            raise EconomicSessionAuthorityError("authorized order targets a delisted instrument")
        order = SimulationOrder(
            order_id=authorized.order_id,
            instrument_id=authorized.instrument_id,
            side=authorized.side,
            requested_quantity=submitted,
            raw_open=resolved.price.raw_open,
            execution_at=resolved.price.execution_at,
        )
        fill = _derive_execution_fill_from_balances(
            order,
            checkpoint.trade_policy,
            current_cash=cash,
            position_quantity=position.quantity,
            position_cost_basis=position.cost_basis,
        )
        cash = _add(cash, fill.cash_delta)
        if fill.side is SimulationSide.BUY:
            positions[fill.instrument_id] = position.model_copy(
                update={
                    "quantity": _add(position.quantity, fill.executed_quantity),
                    "cost_basis": _add(position.cost_basis, fill.fill_notional),
                }
            )
        else:
            remaining = _add(position.quantity, fill.executed_quantity.copy_negate())
            positions[fill.instrument_id] = position.model_copy(
                update={
                    "quantity": remaining,
                    "cost_basis": Decimal("0")
                    if remaining == 0
                    else _add(position.cost_basis, fill.cost_basis_removed.copy_negate()),
                }
            )
        fills.append(fill)
        evidence.append(
            EconomicFillAuthorityV2(
                order_id=fill.order_id,
                fill_digest=fill.digest,
                request_authority_digest=request.authority_digest,
                dataset_id=authorized.dataset_id,
                manifest_digest=authorized.manifest_digest,
                execution_price_bundle_digest=resolved.bundle_digest,
                execution_price_projection_digest=resolved.price.projection_digest,
                execution_price_provenance_digest=resolved.provenance.provenance_digest,
                submitted_quantity=submitted,
                liquidity_cap=liquidity_cap,
            )
        )
    return (
        request.session_id,
        tuple(fills),
        tuple(sorted(evidence, key=lambda item: str(item.order_id))),
        trusted_reference,
    )


def _project_cash_bases(
    checkpoint: EconomicLedgerCheckpointV2,
    pre_open_views,
    fills: tuple[ExecutionFill, ...],
    post_close_views,
):
    positions = {item.instrument_id: item for item in checkpoint.state.positions}
    entitlements = {
        (item.instrument_id, item.action_id): item
        for item in checkpoint.state.entitlements
    }
    for view in pre_open_views:
        for action in view.view.actions:
            position = positions[action.instrument_id]
            key = (action.instrument_id, action.action_id)
            if action.application is CorporateActionApplication.SPLIT:
                positions[action.instrument_id] = position.model_copy(
                    update={"quantity": _mul(position.quantity, action.split_ratio)}
                )
            elif action.application is CorporateActionApplication.DIVIDEND_ENTITLEMENT:
                entitlements[key] = DividendEntitlementV2(
                    instrument_id=action.instrument_id,
                    action_id=action.action_id,
                    action_digest=action.action_digest,
                    action_revision=action.revision,
                    entitled_quantity=position.quantity,
                    cash_amount_per_share=action.cash_amount,
                    source_currency=action.currency,
                )
            elif action.application is CorporateActionApplication.DELISTING:
                positions[action.instrument_id] = position.model_copy(
                    update={"fill_eligibility": FillEligibilityV2.BLOCKED_DELISTED}
                )
            else:
                raise EconomicSessionAuthorityError("invalid PRE_OPEN application")
    for fill in fills:
        position = positions[fill.instrument_id]
        if fill.side is SimulationSide.BUY:
            positions[fill.instrument_id] = position.model_copy(
                update={
                    "quantity": _add(position.quantity, fill.executed_quantity),
                    "cost_basis": _add(position.cost_basis, fill.fill_notional),
                }
            )
        else:
            remaining = _add(position.quantity, fill.executed_quantity.copy_negate())
            positions[fill.instrument_id] = position.model_copy(
                update={
                    "quantity": remaining,
                    "cost_basis": Decimal("0")
                    if remaining == 0
                    else _add(position.cost_basis, fill.cost_basis_removed.copy_negate()),
                }
            )
    result = []
    for view in post_close_views:
        for action in view.view.actions:
            if action.application is CorporateActionApplication.DIVIDEND_PAYMENT:
                source = entitlements.get((action.instrument_id, action.action_id))
                if source is None:
                    raise EconomicSessionAuthorityError(
                        "dividend payment has no authenticated entitlement"
                    )
                quantity = source.entitled_quantity
                basis_name = "entitled_quantity"
                source_digest = source.digest
            elif action.application is CorporateActionApplication.DELISTING_SETTLEMENT:
                source = positions[action.instrument_id]
                quantity = source.quantity
                basis_name = "position_quantity"
                source_digest = source.digest
            else:
                raise EconomicSessionAuthorityError("invalid POST_CLOSE application")
            if quantity == 0:
                continue
            receipt_id = canonical_digest(
                {
                    "receipt_id_version": "economic-ledger-receipt-id/2",
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "checkpoint_digest": checkpoint.checkpoint_digest,
                    "checkpoint_generation": checkpoint.generation,
                    "prior_session_receipt_digest": checkpoint.state.last_session_receipt_hash,
                    "quantity_source_digest": source_digest,
                }
            )
            payload = {
                "basis_version": "corporate-cash-ledger-basis/1",
                "tenant_id": checkpoint.tenant_id,
                "backtest_id": checkpoint.backtest_id,
                "ledger_id": checkpoint.ledger_id,
                "backtest_spec_digest": checkpoint.backtest_spec_digest,
                "ledger_policy_digest": checkpoint.trade_policy.digest,
                "economics_policy_digest": checkpoint.economics_policy.digest,
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_digest": checkpoint.checkpoint_digest,
                "checkpoint_generation": checkpoint.generation,
                "prior_session_receipt_digest": checkpoint.state.last_session_receipt_hash,
                "instrument_id": action.instrument_id,
                "action_id": action.action_id,
                "application": action.application,
                "session_date": action.session_date,
                "phase": EconomicPhase.POST_CLOSE,
                "quantity_basis": basis_name,
                "quantity": quantity,
                "quantity_source_digest": source_digest,
                "ledger_receipt_id": receipt_id,
            }
            result.append(
                CorporateCashLedgerBasis(**payload, basis_digest=canonical_digest(payload))
            )
    return tuple(result)


def commit_authorized_economic_session_v2(
    authority_repository: FrozenSimulationAuthorityRepository,
    dataset_repository: FrozenDatasetRepository,
    head_store: SimulationCheckpointHeadStore,
    prior_checkpoint_reference: EconomicCheckpointReferenceV2,
    order_request_reference: SimulationRequestReference | None,
    session_date: date,
    *,
    expected_tenant_id: UUID,
    expected_backtest_id: UUID,
    expected_backtest_spec_digest: str,
    expected_ledger_id: UUID,
) -> AuthorizedEconomicSessionCommitV2:
    """Resolve, reduce, authenticate and CAS one complete economic session."""

    if type(authority_repository) is not FrozenSimulationAuthorityRepository:
        raise EconomicSessionAuthorityError("economic commit requires repository authority")
    if type(dataset_repository) is not FrozenDatasetRepository:
        raise EconomicSessionAuthorityError("economic commit requires dataset authority")
    if type(prior_checkpoint_reference) is not EconomicCheckpointReferenceV2:
        raise EconomicSessionAuthorityError("economic commit requires an opaque checkpoint ref")
    if order_request_reference is not None and type(
        order_request_reference
    ) is not SimulationRequestReference:
        raise EconomicSessionAuthorityError("order reference uses another contract type")
    if type(session_date) is not date:
        raise EconomicSessionAuthorityError("economic session date is invalid")
    store = _validate_head_store(head_store)
    checkpoint = authority_repository._load_economic_checkpoint(
        prior_checkpoint_reference,
        expected_tenant_id=expected_tenant_id,
        expected_backtest_id=expected_backtest_id,
        expected_backtest_spec_digest=expected_backtest_spec_digest,
        expected_ledger_id=expected_ledger_id,
    )
    scope = _scope(
        checkpoint.tenant_id,
        checkpoint.backtest_id,
        checkpoint.ledger_id,
        checkpoint.backtest_spec_digest,
    )
    current_head = store.read_head(scope)
    if current_head is None or current_head.generation < checkpoint.generation:
        raise SimulationCheckpointHeadConflictError(
            "economic checkpoint is not part of the visible custody history"
        )
    if current_head.generation == checkpoint.generation and (
        current_head.checkpoint_id != checkpoint.checkpoint_id
        or current_head.checkpoint_digest != checkpoint.checkpoint_digest
        or current_head.authenticated_reference_digest
        != _head_candidate(prior_checkpoint_reference).reference.digest
    ):
        raise SimulationCheckpointHeadConflictError(
            "economic checkpoint differs from the visible custody head"
        )
    authorities = _open_economic_authorities(checkpoint, dataset_repository)
    pre_open = tuple(
        authorities[instrument_id].resolve_event(session_date, EconomicPhase.PRE_OPEN)
        for instrument_id in checkpoint.economic_policy.instrument_ids
    )
    post_close = tuple(
        authorities[instrument_id].resolve_event(session_date, EconomicPhase.POST_CLOSE)
        for instrument_id in checkpoint.economic_policy.instrument_ids
    )
    session_id, fills, fill_evidence, trusted_request_ref = _load_request_and_derive_fills(
        checkpoint,
        dataset_repository,
        authority_repository,
        order_request_reference,
        session_date,
        pre_open,
    )
    bases = _project_cash_bases(checkpoint, pre_open, fills, post_close)
    if len(bases) > 1_000 or (
        len(fills)
        + sum(len(item.view.actions) for item in pre_open + post_close)
        > 10_000
    ):
        raise EconomicSessionAuthorityError(
            "economic session exceeds the bounded atomic workload"
        )
    conversions = []
    for basis in bases:
        authority = authorities[basis.instrument_id]
        verified = authority._bind_corporate_cash_ledger_basis(
            basis,
            expected_basis_digest=basis.basis_digest,
            expected_checkpoint_digest=checkpoint.checkpoint_digest,
            expected_prior_session_receipt_digest=checkpoint.state.last_session_receipt_hash,
        )
        conversion = authority._convert_corporate_cash_from_ledger_basis(verified)
        conversions.append(
            authority._verify_corporate_cash_conversion(conversion, verified)
        )
    transition_payload: dict[str, Any] = {
        "transition_version": "economic-session-transition/2",
        "session_id": session_id,
        "session_date": session_date,
        "pre_open_views": pre_open,
        "trade_fills": fills,
        "post_close_views": post_close,
        "cash_conversions": tuple(sorted(conversions, key=lambda item: (
            str(item.instrument_id), str(item.action_id), item.application.value
        ))),
    }
    transition = EconomicSessionTransitionV2(
        **transition_payload,
        request_digest=_digest(transition_payload),
    )
    ledger_result = _commit_simulation_economic_session_v2(
        checkpoint.state,
        transition,
        expected_state_hash=checkpoint.state.state_hash,
        expected_policy_digest=checkpoint.economic_policy.digest,
        expected_request_digest=transition.request_digest,
    )
    if ledger_result.replay_status != "applied":
        raise EconomicSessionAuthorityError(
            "a prior checkpoint cannot replay a session without advancing custody"
        )
    evidence_payload = {
        "evidence_version": "economic-session-authority-evidence/2",
        "session_id": session_id,
        "session_date": session_date,
        "order_request_reference": trusted_request_ref,
        "fills": fill_evidence,
        "pre_open_authority_digests": tuple(item.authority_digest for item in pre_open),
        "post_close_authority_digests": tuple(item.authority_digest for item in post_close),
        "transition_request_digest": transition.request_digest,
    }
    evidence = EconomicSessionAuthorityEvidenceV2(
        **evidence_payload, evidence_digest=_digest(evidence_payload)
    )
    created_at = max(item.view.as_of for item in post_close).astimezone(UTC)
    checkpoint_id = uuid5(
        NAMESPACE_URL,
        "tradingagents/economic-checkpoint/2:"
        f"{checkpoint.checkpoint_digest}:{transition.request_digest}:"
        f"{ledger_result.state.state_hash}",
    )
    next_checkpoint = EconomicLedgerCheckpointV2(
        checkpoint_id=checkpoint_id,
        generation=checkpoint.generation + 1,
        tenant_id=checkpoint.tenant_id,
        backtest_id=checkpoint.backtest_id,
        ledger_id=checkpoint.ledger_id,
        backtest_spec_digest=checkpoint.backtest_spec_digest,
        created_at=created_at,
        previous_checkpoint_digest=checkpoint.checkpoint_digest,
        backtest_spec=checkpoint.backtest_spec,
        trade_policy=checkpoint.trade_policy,
        economics_policy=checkpoint.economics_policy,
        economic_policy=checkpoint.economic_policy,
        state=ledger_result.state,
        session_authorities=checkpoint.session_authorities + (evidence,),
    )
    committed_reference = _publish_or_load(authority_repository, next_checkpoint)
    committed_head = _compare_append_and_verify(
        store,
        scope,
        SimulationCheckpointExpectation(
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_digest=checkpoint.checkpoint_digest,
            generation=checkpoint.generation,
        ),
        _head_candidate(committed_reference),
        visible_head_before=current_head,
    )
    payload = {
        "result_version": "authorized-economic-session-commit/2",
        "prior_checkpoint": prior_checkpoint_reference,
        "committed_checkpoint": committed_reference,
        "committed_head": committed_head,
        "evidence": evidence,
        "ledger_result": ledger_result,
    }
    return AuthorizedEconomicSessionCommitV2(
        **payload, result_digest=_digest(payload)
    )


__all__ = [
    "AuthorizedEconomicGenesisV2",
    "AuthorizedEconomicSessionCommitV2",
    "EconomicCheckpointReferenceV2",
    "EconomicSessionAuthorityError",
    "commit_authorized_economic_session_v2",
    "provision_authenticated_economic_genesis_v2",
]
