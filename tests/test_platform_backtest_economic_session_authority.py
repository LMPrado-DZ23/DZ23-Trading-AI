from __future__ import annotations

import ast
import hashlib
import stat
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tradingagents.platform.backtest.calendar import BacktestCalendar
from tradingagents.platform.backtest.contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    AssetType,
    AvailabilityBasis,
    BacktestSpecV3,
    CorporateAction,
    CorporateActionType,
    DailyBar,
    DatasetReference,
    DatasetRole,
    InstrumentSpec,
    MarketCalendar,
)
from tradingagents.platform.backtest.repository import FrozenDatasetRepository
from tradingagents.platform.backtest.simulation_authority import (
    REQUEST_FILENAME,
    AuthorizedSimulationOrder,
    AuthorizedSimulationRequest,
    FrozenSimulationAuthorityRepository,
    SimulationAuthorityError,
    SimulationLedgerCheckpoint,
)
from tradingagents.platform.backtest.simulation_checkpoint_head import (
    LocalSimulationCheckpointHeadStore,
    SimulationCheckpointHeadConflictError,
    SimulationCheckpointScope,
)
from tradingagents.platform.backtest.simulation_economic_session_authority import (
    EconomicCheckpointReferenceV2,
    EconomicSessionAuthorityError,
    commit_authorized_economic_session_v2,
    provision_authenticated_economic_genesis_v2,
)
from tradingagents.platform.backtest.simulation_economics import (
    InstrumentEconomicsBinding,
    SimulationEconomicsPolicy,
    fx_authority_book_digest,
)
from tradingagents.platform.backtest.simulation_ledger import (
    InstrumentSimulationRule,
    SimulationLedgerPolicy,
    SimulationSide,
    create_simulation_ledger,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
BACKTEST_ID = UUID("20000000-0000-0000-0000-000000000001")
LEDGER_ID = UUID("30000000-0000-0000-0000-000000000001")
INSTRUMENT_ID = UUID("40000000-0000-0000-0000-000000000001")
INSTRUMENT_ID_2 = UUID("41000000-0000-0000-0000-000000000001")
DATASET_ID = UUID("50000000-0000-0000-0000-000000000001")
DATASET_ID_2 = UUID("51000000-0000-0000-0000-000000000001")
BENCHMARK_ID = UUID("60000000-0000-0000-0000-000000000001")
BENCHMARK_DATASET_ID = UUID("70000000-0000-0000-0000-000000000001")
TRADE_CHECKPOINT_ID = UUID("80000000-0000-0000-0000-000000000001")
BATCH_ID = UUID("90000000-0000-0000-0000-000000000001")
SESSION_ID = UUID("a0000000-0000-0000-0000-000000000001")
ORDER_ID = UUID("b0000000-0000-0000-0000-000000000001")
ORDER_ID_2 = UUID("a0000000-0000-0000-0000-000000000002")
CREATED_AT = datetime(2024, 1, 10, 12, tzinfo=UTC)
SIGNING_KEY = b"m5.2c3-economic-authority-test-key-minimum"


def _instrument(
    instrument_id: UUID = INSTRUMENT_ID, symbol: str = "AAPL"
) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        symbol=symbol,
        asset_type=AssetType.STOCK,
        venue="nasdaq",
        venue_instrument_id=symbol,
        quote_currency="USD",
        market_calendar=MarketCalendar.NEW_YORK,
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
        available_at=datetime(2020, 1, 1, tzinfo=UTC),
        listing_date=date(2020, 1, 1),
        source_digest="a" * 64,
    )


def _bar(
    session_date: date, raw_open: str, instrument_id: UUID = INSTRUMENT_ID
) -> DailyBar:
    window = BacktestCalendar(MarketCalendar.NEW_YORK).session_window(session_date)
    return DailyBar(
        instrument_id=instrument_id,
        session_date=session_date,
        session_open_at=window.open_at,
        session_close_at=window.close_at,
        fetched_at=CREATED_AT,
        available_at=window.close_at + timedelta(minutes=15),
        availability_basis=AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY,
        open=raw_open,
        high=str(int(raw_open) + 10),
        low=str(int(raw_open) - 10),
        close=str(int(raw_open) + 5),
        volume="1000",
        source_digest=hashlib.sha256(session_date.isoformat().encode()).hexdigest(),
    )


def _environment(
    tmp_path: Path,
    *,
    corporate_actions: tuple[CorporateAction, ...] = (),
    two_instruments: bool = False,
):
    dataset_repository = FrozenDatasetRepository(tmp_path / "datasets")
    decision_bar = _bar(date(2024, 1, 2), "90")
    execution_bar = _bar(date(2024, 1, 3), "100")
    later_bar = _bar(date(2024, 1, 4), "105")
    manifest = dataset_repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="m5.2c3-outer-authority",
        instrument=_instrument(),
        bars=(decision_bar, execution_bar, later_bar),
        corporate_actions=corporate_actions,
        fx_observations=(),
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    view = dataset_repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_bar.available_at,
        decision_session_date=decision_bar.session_date,
    )
    manifests = [(INSTRUMENT_ID, DATASET_ID, manifest, view)]
    if two_instruments:
        bars_2 = tuple(
            _bar(day, raw, INSTRUMENT_ID_2)
            for day, raw in (
                (date(2024, 1, 2), "45"),
                (date(2024, 1, 3), "50"),
                (date(2024, 1, 4), "55"),
            )
        )
        manifest_2 = dataset_repository.ingest(
            dataset_id=DATASET_ID_2,
            tenant_id=TENANT_ID,
            created_at=CREATED_AT,
            source="test.vendor",
            source_vintage="m5.2c3-outer-authority-second",
            instrument=_instrument(INSTRUMENT_ID_2, "MSFT"),
            bars=bars_2,
            corporate_actions=(),
            fx_observations=(),
            limitations=M5_UNVERIFIED_LIMITATIONS,
        )
        assert bars_2[0].available_at is not None
        view_2 = dataset_repository.load_eligible(
            DATASET_ID_2,
            expected_tenant_id=TENANT_ID,
            expected_manifest_digest=manifest_2.digest,
            decision_at=bars_2[0].available_at,
            decision_session_date=bars_2[0].session_date,
        )
        manifests.append((INSTRUMENT_ID_2, DATASET_ID_2, manifest_2, view_2))
    trade_policy = SimulationLedgerPolicy(
        ledger_id=LEDGER_ID,
        opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        base_currency="USD",
        initial_cash="10000.00",
        money_quantum="0.01",
        commission_bps="10",
        slippage_bps="5",
        instrument_rules=tuple(
            InstrumentSimulationRule(
                instrument_id=instrument_id,
                currency="USD",
                lot_size="1",
                price_tick="0.01",
            )
            for instrument_id, _, _, _ in manifests
        ),
    )
    economics_policy = SimulationEconomicsPolicy(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        ledger_id=LEDGER_ID,
        functional_currency="USD",
        fx_max_staleness_seconds=3600,
        fx_book_digest=fx_authority_book_digest(()),
        money_quantum="0.01",
        instrument_bindings=tuple(
            InstrumentEconomicsBinding(
                instrument_id=instrument_id,
                dataset_id=dataset_id,
                manifest_digest=item_manifest.digest,
                quote_currency="USD",
            )
            for instrument_id, dataset_id, item_manifest, _ in manifests
        ),
    )
    spec = BacktestSpecV3(
        backtest_id=BACKTEST_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        dataset_references=tuple(
            DatasetReference(
                instrument_id=instrument_id,
                dataset_id=dataset_id,
                manifest_digest=item_manifest.digest,
                role=DatasetRole.TRADEABLE,
            )
            for instrument_id, dataset_id, item_manifest, _ in manifests
        )
        + (
            DatasetReference(
                instrument_id=BENCHMARK_ID,
                dataset_id=BENCHMARK_DATASET_ID,
                manifest_digest="e" * 64,
                role=DatasetRole.BENCHMARK,
            ),
        ),
        instrument_ids=tuple(item[0] for item in manifests),
        benchmark_instrument_id=BENCHMARK_ID,
        start_session=date(2024, 1, 2),
        end_session=date(2024, 1, 4),
        base_currency="USD",
        initial_cash="10000.00",
        commission_bps=trade_policy.commission_bps,
        slippage_bps=trade_policy.slippage_bps,
        simulation_policy_digest=trade_policy.digest,
        economics_policy_digest=economics_policy.digest,
    )
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    trade_checkpoint = SimulationLedgerCheckpoint(
        checkpoint_id=TRADE_CHECKPOINT_ID,
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        backtest_spec_digest=spec.digest,
        ledger_id=LEDGER_ID,
        created_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        policy=trade_policy,
        state=create_simulation_ledger(trade_policy),
    )
    trade_reference = authority.publish_checkpoint(trade_checkpoint)
    head_store = LocalSimulationCheckpointHeadStore(tmp_path / "heads")
    genesis = provision_authenticated_economic_genesis_v2(
        authority,
        head_store,
        trade_reference,
        spec,
        economics_policy,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
    )
    orders = tuple(
        sorted(
            (
                AuthorizedSimulationOrder(
                    order_id=ORDER_ID if instrument_id == INSTRUMENT_ID else ORDER_ID_2,
                    instrument_id=instrument_id,
                    side=SimulationSide.BUY,
                    requested_quantity="2" if instrument_id == INSTRUMENT_ID else "1",
                    dataset_id=dataset_id,
                    manifest_digest=item_manifest.digest,
                    decision_at=item_view.decision_at,
                    decision_session_date=item_view.decision_session_date,
                    eligible_view_digest=item_view.eligible_view_sha256,
                )
                for instrument_id, dataset_id, item_manifest, item_view in manifests
            ),
            key=lambda item: str(item.order_id),
        )
    )
    request = AuthorizedSimulationRequest(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        backtest_spec_digest=spec.digest,
        ledger_id=LEDGER_ID,
        policy_digest=trade_policy.digest,
        batch_id=BATCH_ID,
        session_id=SESSION_ID,
        created_at=CREATED_AT,
        orders=orders,
    )
    request_reference = authority.publish_request(request)
    return (
        dataset_repository,
        authority,
        head_store,
        genesis,
        request_reference,
        spec,
        trade_reference,
        economics_policy,
    )


def _dividend(
    *, ex_date: date = date(2024, 1, 4), pay_date: date = date(2024, 1, 4)
) -> CorporateAction:
    return CorporateAction(
        action_id=UUID("c0000000-0000-0000-0000-000000000001"),
        instrument_id=INSTRUMENT_ID,
        action_type=CorporateActionType.CASH_DIVIDEND,
        published_at=datetime(2024, 1, 1, 12, tzinfo=UTC),
        available_at=datetime(2024, 1, 1, 12, 1, tzinfo=UTC),
        ex_date=ex_date,
        effective_date=ex_date,
        pay_date=pay_date,
        cash_amount="1.25",
        cash_amount_basis="net_cash_per_entitled_share",
        currency="USD",
        source_digest="d" * 64,
    )


def _commit(environment, request_reference):
    datasets, authority, heads, genesis, _, spec, *_ = environment
    return commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        genesis.checkpoint,
        request_reference,
        date(2024, 1, 3),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )


def test_genesis_is_authenticated_materialized_and_exactly_replayable(tmp_path: Path):
    environment = _environment(tmp_path)
    _, authority, heads, genesis, _, spec, trade_reference, economics_policy = environment
    replay = provision_authenticated_economic_genesis_v2(
        authority,
        heads,
        trade_reference,
        spec,
        economics_policy,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
    )

    assert replay == genesis
    assert genesis.checkpoint.generation == 1
    assert genesis.checkpoint.previous_checkpoint_digest is None
    assert genesis.head.scope.stream_kind == "economic-v2"
    loaded = authority._load_economic_checkpoint(
        genesis.checkpoint,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )
    assert loaded.state.cash == loaded.trade_policy.initial_cash
    assert loaded.state.session_receipts == ()


@pytest.mark.parametrize(
    "invalid_binding",
    ("missing_rule", "missing_binding", "wrong_dataset", "wrong_currency"),
)
def test_genesis_rejects_incomplete_instrument_authority_before_publication(
    tmp_path: Path, invalid_binding: str
):
    environment = _environment(tmp_path / "valid", two_instruments=True)
    _, valid_authority, _, _, _, valid_spec, trade_reference, valid_economics = (
        environment
    )
    valid_trade = valid_authority.load_checkpoint(
        trade_reference,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=valid_spec.digest,
    )
    trade_payload = valid_trade.policy.model_dump(mode="python")
    economics_payload = valid_economics.model_dump(mode="python")
    if invalid_binding == "missing_rule":
        trade_payload["instrument_rules"] = trade_payload["instrument_rules"][:1]
    elif invalid_binding == "missing_binding":
        economics_payload["instrument_bindings"] = economics_payload[
            "instrument_bindings"
        ][:1]
    elif invalid_binding == "wrong_dataset":
        economics_payload["instrument_bindings"][0]["dataset_id"] = DATASET_ID_2
    else:
        economics_payload["instrument_bindings"][0]["quote_currency"] = "EUR"
    trade_policy = SimulationLedgerPolicy.model_validate(trade_payload)
    economics_policy = SimulationEconomicsPolicy.model_validate(economics_payload)
    spec_payload = valid_spec.model_dump(mode="python")
    spec_payload["simulation_policy_digest"] = trade_policy.digest
    spec_payload["economics_policy_digest"] = economics_policy.digest
    spec = BacktestSpecV3.model_validate(spec_payload)
    authority_root = tmp_path / "invalid-authority"
    heads_root = tmp_path / "invalid-heads"
    authority = FrozenSimulationAuthorityRepository(
        authority_root, signing_key=SIGNING_KEY
    )
    checkpoint = SimulationLedgerCheckpoint(
        checkpoint_id=TRADE_CHECKPOINT_ID,
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        backtest_spec_digest=spec.digest,
        ledger_id=LEDGER_ID,
        created_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        policy=trade_policy,
        state=create_simulation_ledger(trade_policy),
    )
    reference = authority.publish_checkpoint(checkpoint)

    with pytest.raises(EconomicSessionAuthorityError, match="genesis"):
        provision_authenticated_economic_genesis_v2(
            authority,
            LocalSimulationCheckpointHeadStore(heads_root),
            reference,
            spec,
            economics_policy,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
        )

    assert not list(authority_root.glob("economic-checkpoints-v2-*"))
    assert not heads_root.exists() or not list(heads_root.rglob("*"))


def test_complete_order_session_derives_fill_and_commits_one_head(tmp_path: Path):
    environment = _environment(tmp_path)
    result = _commit(environment, environment[4])

    assert result.committed_head.generation == 2
    assert result.committed_checkpoint.previous_checkpoint_digest == (
        result.prior_checkpoint.checkpoint_digest
    )
    fill = result.ledger_result.receipt.transition.trade_fills[0]
    assert result.ledger_result.state.cash == fill.cash_delta + 10000
    assert result.ledger_result.state.position(INSTRUMENT_ID).quantity == 2
    assert len(result.evidence.fills) == 1
    assert result.evidence.fills[0].fill_digest == (
        result.ledger_result.receipt.transition.trade_fills[0].digest
    )


def test_duck_typed_cas_cannot_forge_a_committed_economic_head(tmp_path: Path):
    environment = _environment(tmp_path)
    datasets, authority, heads, genesis, request_reference, spec, *_ = environment

    class LyingCAS:
        def read_head(self, scope):
            return heads.read_head(scope)

        def compare_and_append(self, scope, expected, candidate):
            return genesis.head.model_copy(
                update={
                    "generation": expected.generation + 1,
                    "checkpoint_id": candidate.checkpoint_id,
                    "checkpoint_digest": candidate.checkpoint_digest,
                    "authenticated_reference_digest": "f" * 64,
                }
            )

    with pytest.raises(
        EconomicSessionAuthorityError, match="exact trusted local CAS store"
    ):
        commit_authorized_economic_session_v2(
            authority,
            datasets,
            LyingCAS(),  # type: ignore[arg-type]
            genesis.checkpoint,
            request_reference,
            date(2024, 1, 3),
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_ledger_id=LEDGER_ID,
        )

    assert heads.read_head(genesis.head.scope) == genesis.head


def test_multi_order_uses_economic_order_while_evidence_uses_order_id(tmp_path: Path):
    environment = _environment(tmp_path, two_instruments=True)
    result = _commit(environment, environment[4])

    transition = result.ledger_result.receipt.transition
    assert tuple(item.instrument_id for item in transition.trade_fills) == (
        INSTRUMENT_ID,
        INSTRUMENT_ID_2,
    )
    assert tuple(item.order_id for item in result.evidence.fills) == (
        ORDER_ID_2,
        ORDER_ID,
    )
    assert {item.order_id: item.fill_digest for item in result.evidence.fills} == {
        item.order_id: item.digest for item in transition.trade_fills
    }


def test_dividend_uses_authenticated_entitlement_and_identity_fx(tmp_path: Path):
    environment = _environment(
        tmp_path, corporate_actions=(_dividend(),)
    )
    first = _commit(environment, environment[4])
    datasets, authority, heads, _, _, spec, *_ = environment
    paid = commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        first.committed_checkpoint,
        None,
        date(2024, 1, 4),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )

    kinds = tuple(item.kind.value for item in paid.ledger_result.appended_events)
    assert kinds == ("dividend_entitlement", "dividend_cash")
    assert paid.ledger_result.state.cash == first.ledger_result.state.cash + Decimal("2.50")
    assert len(paid.ledger_result.receipt.transition.cash_conversions) == 1
    conversion = paid.ledger_result.receipt.transition.cash_conversions[0]
    assert conversion.expected_quantity == 2
    assert conversion.checkpoint_digest == first.committed_checkpoint.checkpoint_digest
    assert conversion.checkpoint_generation == 2


def test_order_on_pre_open_action_requires_explicit_reauthorization(tmp_path: Path):
    environment = _environment(
        tmp_path,
        corporate_actions=(
            _dividend(ex_date=date(2024, 1, 3), pay_date=date(2024, 1, 4)),
        ),
    )

    with pytest.raises(ValueError, match="PRE_OPEN corporate actions require reauthorization"):
        _commit(environment, environment[4])


def test_empty_session_still_advances_receipt_checkpoint_and_head(tmp_path: Path):
    environment = _environment(tmp_path)
    datasets, authority, heads, genesis, _, spec, *_ = environment
    result = commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        genesis.checkpoint,
        None,
        date(2024, 1, 2),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )

    assert result.committed_head.generation == 2
    assert result.ledger_result.receipt.event_count == 0
    assert result.ledger_result.appended_events == ()
    assert len(result.ledger_result.state.session_receipts) == 1
    assert result.ledger_result.state.cash == 10000


def test_retry_old_session_after_later_head_returns_original_commit(tmp_path: Path):
    environment = _environment(tmp_path)
    datasets, authority, heads, genesis, request_reference, spec, *_ = environment
    first = commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        genesis.checkpoint,
        None,
        date(2024, 1, 2),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )
    later = commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        first.committed_checkpoint,
        request_reference,
        date(2024, 1, 3),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )
    replay = commit_authorized_economic_session_v2(
        authority,
        datasets,
        heads,
        genesis.checkpoint,
        None,
        date(2024, 1, 2),
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=spec.digest,
        expected_ledger_id=LEDGER_ID,
    )

    assert later.committed_head.generation == 3
    assert replay == first
    scope = later.committed_head.scope
    assert heads.read_head(scope) == later.committed_head


def test_exact_retry_reuses_checkpoint_and_sibling_conflicts(tmp_path: Path):
    environment = _environment(tmp_path)
    first = _commit(environment, environment[4])
    replay = _commit(environment, environment[4])
    assert replay == first

    datasets, authority, heads, genesis, _, spec, *_ = environment
    with pytest.raises(SimulationCheckpointHeadConflictError):
        commit_authorized_economic_session_v2(
            authority,
            datasets,
            heads,
            genesis.checkpoint,
            None,
            date(2024, 1, 3),
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_ledger_id=LEDGER_ID,
        )


def test_foreign_or_tampered_checkpoint_reference_fails_before_commit(tmp_path: Path):
    environment = _environment(tmp_path)
    datasets, authority, heads, genesis, request_reference, spec, *_ = environment
    forged = EconomicCheckpointReferenceV2(
        **genesis.checkpoint.model_dump(
            mode="python", exclude={"checkpoint_digest"}
        ),
        checkpoint_digest="f" * 64,
    )
    with pytest.raises(SimulationAuthorityError):
        commit_authorized_economic_session_v2(
            authority,
            datasets,
            heads,
            forged,
            request_reference,
            date(2024, 1, 3),
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_ledger_id=LEDGER_ID,
        )


def test_economic_checkpoint_bytes_are_hmac_authenticated(tmp_path: Path):
    environment = _environment(tmp_path)
    _, authority, _, genesis, _, spec, *_ = environment
    record = (
        authority.root
        / f"economic-checkpoints-v2-{genesis.checkpoint.checkpoint_id}"
        / REQUEST_FILENAME
    )
    record.chmod(stat.S_IREAD | stat.S_IWRITE)
    content = record.read_bytes()
    record.write_bytes(content.replace(b'"generation":1', b'"generation":2'))

    with pytest.raises(SimulationAuthorityError, match="signature mismatch"):
        authority._load_economic_checkpoint(
            genesis.checkpoint,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec.digest,
            expected_ledger_id=LEDGER_ID,
        )


def test_private_financial_primitives_have_one_production_consumer() -> None:
    root = Path(__file__).parents[1]
    package = root / "tradingagents"
    allowed = {
        package / "platform" / "backtest" / "simulation_economic_ledger.py",
        package / "platform" / "backtest" / "simulation_economics_authority.py",
        package / "platform" / "backtest" / "simulation_ledger.py",
        package / "platform" / "backtest" / "simulation_economic_session_authority.py",
    }
    guarded = {
        "_commit_simulation_economic_session_v2",
        "_bind_corporate_cash_ledger_basis",
        "_convert_corporate_cash_from_ledger_basis",
        "_verify_corporate_cash_conversion",
        "_derive_execution_fill_from_balances",
        "_publish_economic_checkpoint",
        "_load_economic_checkpoint",
    }
    violations = []
    for path in package.rglob("*.py"):
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced = {
            node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(parsed) if isinstance(node, ast.Attribute)
        } | {
            alias.name
            for node in ast.walk(parsed)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        if referenced & guarded and path not in allowed:
            violations.append(str(path.relative_to(root)))
    assert not violations


def test_trade_scope_digest_is_backward_compatible_and_economic_is_separate():
    legacy = SimulationCheckpointScope(
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        ledger_id=LEDGER_ID,
        backtest_spec_digest="a" * 64,
    )
    explicit = legacy.model_copy(update={"stream_kind": "trade-v1"})
    economic = legacy.model_copy(update={"stream_kind": "economic-v2"})
    assert legacy.digest == explicit.digest
    assert economic.digest != legacy.digest
