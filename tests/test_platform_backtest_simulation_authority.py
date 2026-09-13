from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import tempfile
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tradingagents.platform.backtest.calendar import BacktestCalendar
from tradingagents.platform.backtest.contracts import (
    M5_UNVERIFIED_LIMITATIONS,
    AssetType,
    AvailabilityBasis,
    BacktestSpec,
    DailyBar,
    DatasetReference,
    DatasetRole,
    InstrumentSpec,
    MarketCalendar,
    canonical_digest,
    canonical_json_bytes,
)
from tradingagents.platform.backtest.errors import DatasetIntegrityError
from tradingagents.platform.backtest.repository import (
    FrozenDatasetRepository,
    resolve_execution_price_authority,
)
from tradingagents.platform.backtest.simulation_authority import (
    REQUEST_FILENAME,
    SIGNATURE_FILENAME,
    AuthorizedSimulationBatchResult,
    AuthorizedSimulationOrder,
    AuthorizedSimulationRequest,
    FrozenSimulationAuthorityRepository,
    SimulationAuthorityError,
    SimulationCheckpointReference,
    SimulationLedgerCheckpoint,
    SimulationRequestAlreadyExistsError,
    execute_authorized_simulation_batch,
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
DATASET_ID = UUID("50000000-0000-0000-0000-000000000001")
BENCHMARK_INSTRUMENT_ID = UUID("50000000-0000-0000-0000-000000000002")
BENCHMARK_DATASET_ID = UUID("50000000-0000-0000-0000-000000000003")
BATCH_ID = UUID("60000000-0000-0000-0000-000000000001")
SESSION_ID = UUID("70000000-0000-0000-0000-000000000001")
ORDER_ID = UUID("80000000-0000-0000-0000-000000000001")
CHECKPOINT_ID = UUID("90000000-0000-0000-0000-000000000001")
CREATED_AT = datetime(2024, 1, 10, 12, tzinfo=UTC)
SIGNING_KEY = b"simulation-authority-test-key-32-bytes-minimum"


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=INSTRUMENT_ID,
        symbol="AAPL",
        asset_type=AssetType.STOCK,
        venue="nasdaq",
        venue_instrument_id="AAPL",
        quote_currency="USD",
        market_calendar=MarketCalendar.NEW_YORK,
        published_at=datetime(2020, 1, 1, tzinfo=UTC),
        available_at=datetime(2020, 1, 1, tzinfo=UTC),
        listing_date=date(2020, 1, 1),
        source_digest="a" * 64,
    )


def _bar(session_date: date, *, raw_open: str, volume: str = "1000000") -> DailyBar:
    window = BacktestCalendar(MarketCalendar.NEW_YORK).session_window(session_date)
    return DailyBar(
        instrument_id=INSTRUMENT_ID,
        session_date=session_date,
        session_open_at=window.open_at,
        session_close_at=window.close_at,
        fetched_at=CREATED_AT,
        available_at=window.close_at + timedelta(minutes=15),
        availability_basis=AvailabilityBasis.EXCHANGE_SETTLEMENT_POLICY,
        open=raw_open,
        high=str(Decimal(raw_open) + 10),
        low=str(Decimal(raw_open) - 10),
        close=str(Decimal(raw_open) + 5),
        volume=volume,
        source_digest=hashlib.sha256(session_date.isoformat().encode()).hexdigest(),
    )


def _dataset(
    root: Path,
    *,
    next_open: str = "100",
    decision_volume: str = "1000000",
    execution_volume: str = "1000000",
):
    repository = FrozenDatasetRepository(root)
    decision_bar = _bar(
        date(2024, 1, 2), raw_open="90", volume=decision_volume
    )
    execution_bar = _bar(
        date(2024, 1, 3), raw_open=next_open, volume=execution_volume
    )
    manifest = repository.ingest(
        dataset_id=DATASET_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        source="test.vendor",
        source_vintage="simulation-authority-fixture",
        instrument=_instrument(),
        bars=(decision_bar, execution_bar),
        limitations=M5_UNVERIFIED_LIMITATIONS,
    )
    assert decision_bar.available_at is not None
    view = repository.load_eligible(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=decision_bar.available_at,
        decision_session_date=decision_bar.session_date,
    )
    return repository, manifest, view


def _policy() -> SimulationLedgerPolicy:
    return SimulationLedgerPolicy(
        ledger_id=LEDGER_ID,
        opened_at=datetime(2024, 1, 1, tzinfo=UTC),
        base_currency="USD",
        initial_cash="10000.00",
        money_quantum="0.01",
        commission_bps="10",
        slippage_bps="5",
        instrument_rules=(
            InstrumentSimulationRule(
                instrument_id=INSTRUMENT_ID,
                currency="USD",
                lot_size="1",
                price_tick="0.01",
            ),
        ),
    )


def _spec(manifest, policy: SimulationLedgerPolicy | None = None) -> BacktestSpec:
    resolved = policy or _policy()
    return BacktestSpec(
        backtest_id=BACKTEST_ID,
        tenant_id=TENANT_ID,
        created_at=CREATED_AT,
        dataset_references=(
            DatasetReference(
                instrument_id=INSTRUMENT_ID,
                dataset_id=DATASET_ID,
                manifest_digest=manifest.digest,
                role=DatasetRole.TRADEABLE,
            ),
            DatasetReference(
                instrument_id=BENCHMARK_INSTRUMENT_ID,
                dataset_id=BENCHMARK_DATASET_ID,
                manifest_digest="e" * 64,
                role=DatasetRole.BENCHMARK,
            ),
        ),
        instrument_ids=(INSTRUMENT_ID,),
        benchmark_instrument_id=BENCHMARK_INSTRUMENT_ID,
        start_session=date(2024, 1, 2),
        end_session=date(2024, 1, 3),
        base_currency="USD",
        initial_cash="10000",
        commission_bps=resolved.commission_bps,
        slippage_bps=resolved.slippage_bps,
        simulation_policy_digest=resolved.digest,
    )


def _checkpoint(
    manifest,
    policy: SimulationLedgerPolicy | None = None,
) -> SimulationLedgerCheckpoint:
    resolved = policy or _policy()
    spec_digest = canonical_digest(_spec(manifest, resolved))
    return SimulationLedgerCheckpoint(
        checkpoint_id=CHECKPOINT_ID,
        tenant_id=TENANT_ID,
        backtest_id=BACKTEST_ID,
        backtest_spec_digest=spec_digest,
        ledger_id=LEDGER_ID,
        created_at=datetime(2024, 1, 1, 1, tzinfo=UTC),
        policy=resolved,
        state=create_simulation_ledger(resolved),
    )


def _request(manifest, view, **updates) -> AuthorizedSimulationRequest:
    order = AuthorizedSimulationOrder(
        order_id=ORDER_ID,
        instrument_id=INSTRUMENT_ID,
        side=SimulationSide.BUY,
        requested_quantity="2",
        dataset_id=DATASET_ID,
        manifest_digest=manifest.digest,
        decision_at=view.decision_at,
        decision_session_date=view.decision_session_date,
        eligible_view_digest=view.eligible_view_sha256,
    )
    payload = {
        "tenant_id": TENANT_ID,
        "backtest_id": BACKTEST_ID,
        "backtest_spec_digest": canonical_digest(_spec(manifest)),
        "ledger_id": LEDGER_ID,
        "policy_digest": _policy().digest,
        "batch_id": BATCH_ID,
        "session_id": SESSION_ID,
        "created_at": CREATED_AT,
        "orders": (order,),
    }
    payload.update(updates)
    return AuthorizedSimulationRequest(**payload)


def _authorities(tmp_path: Path):
    dataset, manifest, view = _dataset(tmp_path / "datasets")
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority",
        signing_key=SIGNING_KEY,
    )
    request_reference = authority.publish_request(_request(manifest, view))
    checkpoint_reference = authority.publish_checkpoint(_checkpoint(manifest))
    return dataset, authority, request_reference, checkpoint_reference, _spec(manifest)


def _execute(tmp_path: Path):
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)
    result = execute_authorized_simulation_batch(
        authority,
        dataset,
        request_reference,
        checkpoint_reference,
        spec,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=canonical_digest(spec),
    )
    return dataset, authority, request_reference, checkpoint_reference, spec, result


def test_authorized_execution_derives_raw_next_open_and_returns_custody(tmp_path: Path) -> None:
    _, _, request_reference, checkpoint_reference, _, result = _execute(tmp_path)

    assert result.ledger_result.replay_status == "applied"
    assert len(result.ledger_result.appended_events) == 1
    fill = result.ledger_result.appended_events[0].fill
    assert fill is not None
    assert fill.raw_open == Decimal("100")
    assert fill.fill_price == Decimal("100.05")
    assert result.reference == request_reference
    assert result.prior_checkpoint == checkpoint_reference
    assert result.next_checkpoint is not None
    assert result.next_checkpoint.state == result.ledger_result.state
    assert result.execution_evidence[0].order_id == ORDER_ID


def test_successor_checkpoint_allows_exact_replay_without_new_event(tmp_path: Path) -> None:
    dataset, authority, request_reference, _, spec, first = _execute(tmp_path)
    assert first.next_checkpoint is not None
    successor_ref = authority.publish_checkpoint(first.next_checkpoint)

    replay = execute_authorized_simulation_batch(
        authority,
        dataset,
        request_reference,
        successor_ref,
        spec,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=canonical_digest(spec),
    )

    assert replay.ledger_result.replay_status == "replayed"
    assert replay.ledger_result.appended_events == ()
    assert replay.ledger_result.state == first.ledger_result.state
    assert replay.next_checkpoint is None


def test_request_and_checkpoint_are_append_only_and_deeply_revalidated(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(tmp_path / "datasets")
    del dataset
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    request = _request(manifest, view)
    authority.publish_request(request)
    with pytest.raises(SimulationRequestAlreadyExistsError):
        authority.publish_request(request)

    poisoned = request.model_copy(
        update={
            "orders": (
                request.orders[0].model_copy(update={"requested_quantity": Decimal("NaN")}),
            )
        }
    )
    with pytest.raises(SimulationAuthorityError, match="deep contract"):
        FrozenSimulationAuthorityRepository(tmp_path / "other", signing_key=SIGNING_KEY).publish_request(
            poisoned
        )


def test_hmac_rejects_tampered_request_even_when_json_and_digest_are_self_consistent(
    tmp_path: Path,
) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)
    path = authority.root / f"orders-{BATCH_ID}" / REQUEST_FILENAME
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    raw = json.loads(path.read_bytes())
    raw["orders"][0]["requested_quantity"] = "99"
    path.write_bytes(canonical_json_bytes(raw) + b"\n")

    with pytest.raises(SimulationAuthorityError, match="signature mismatch"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )


def test_wrong_hmac_key_cannot_read_an_existing_authority(tmp_path: Path) -> None:
    _, authority, request_reference, _, _ = _authorities(tmp_path)
    attacker = FrozenSimulationAuthorityRepository(
        authority.root,
        signing_key=b"different-simulation-authority-key-at-least-32",
    )
    with pytest.raises(SimulationAuthorityError, match="signature mismatch"):
        attacker.load_request(
            request_reference,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=request_reference.backtest_spec_digest,
            expected_ledger_id=LEDGER_ID,
            expected_policy_digest=_policy().digest,
        )


def test_forged_price_source_fails_before_any_ledger_result(tmp_path: Path) -> None:
    legitimate, manifest, view = _dataset(tmp_path / "legitimate", next_open="100")
    attacker, _, _ = _dataset(tmp_path / "attacker", next_open="999999")
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    request_reference = authority.publish_request(_request(manifest, view))
    spec = _spec(manifest)
    checkpoint_reference = authority.publish_checkpoint(_checkpoint(manifest))

    with pytest.raises(DatasetIntegrityError, match="manifest does not match"):
        execute_authorized_simulation_batch(
            authority,
            attacker,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )
    assert legitimate.load_execution_price(
        DATASET_ID,
        expected_tenant_id=TENANT_ID,
        expected_instrument_id=INSTRUMENT_ID,
        expected_manifest_digest=manifest.digest,
        decision_at=view.decision_at,
        decision_session_date=view.decision_session_date,
        expected_eligible_view_digest=view.eligible_view_sha256,
    ).price.raw_open == Decimal("100")


@pytest.mark.parametrize(
    ("tenant_id", "backtest_id"),
    ((uuid4(), BACKTEST_ID), (TENANT_ID, uuid4())),
)
def test_cross_tenant_and_cross_backtest_references_fail_closed(
    tmp_path: Path,
    tenant_id: UUID,
    backtest_id: UUID,
) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)
    with pytest.raises(SimulationAuthorityError, match="trusted bindings"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=tenant_id,
            expected_backtest_id=backtest_id,
            expected_backtest_spec_digest=canonical_digest(spec),
        )


def test_checkpoint_tampering_and_forged_reference_fail_closed(tmp_path: Path) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)
    forged = checkpoint_reference.model_copy(update={"checkpoint_digest": "0" * 64})
    with pytest.raises(SimulationAuthorityError, match="trusted bindings"):
        authority.load_checkpoint(
            forged,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )

    path = authority.root / f"checkpoints-{CHECKPOINT_ID}" / REQUEST_FILENAME
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    raw = json.loads(path.read_bytes())
    raw["state"]["cash"] = "999999"
    path.write_bytes(canonical_json_bytes(raw) + b"\n")
    with pytest.raises(SimulationAuthorityError, match="signature mismatch"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )

def test_repository_subclasses_cannot_impersonate_either_authority(tmp_path: Path) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)

    class ForgedAuthority(FrozenSimulationAuthorityRepository):
        pass

    class ForgedDataset(FrozenDatasetRepository):
        pass

    forged_authority = ForgedAuthority(tmp_path / "forged-authority", signing_key=SIGNING_KEY)
    with pytest.raises(SimulationAuthorityError, match="exact simulation repository"):
        execute_authorized_simulation_batch(
            forged_authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )
    forged_dataset = ForgedDataset(tmp_path / "forged-dataset")
    with pytest.raises(SimulationAuthorityError, match="exact dataset repository"):
        execute_authorized_simulation_batch(
            authority,
            forged_dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )


def test_result_contract_rejects_mutated_custody_and_checkpoint(tmp_path: Path) -> None:
    *_, result = _execute(tmp_path)
    with pytest.raises(ValueError, match="result digest mismatch"):
        AuthorizedSimulationBatchResult.model_validate(
            result.model_dump(mode="python") | {"result_digest": "0" * 64}
        )
    assert result.next_checkpoint is not None
    with pytest.raises(ValueError, match="does not extend"):
        AuthorizedSimulationBatchResult.model_validate(
            result.model_dump(mode="python")
            | {
                "next_checkpoint": result.next_checkpoint.model_copy(
                    update={"previous_checkpoint_digest": "0" * 64}
                )
            }
        )


def test_authority_files_are_read_only_and_signature_is_not_plain_sha256(tmp_path: Path) -> None:
    _, authority, _, _, _ = _authorities(tmp_path)
    request_dir = authority.root / f"orders-{BATCH_ID}"
    content = (request_dir / REQUEST_FILENAME).read_bytes()
    signature = (request_dir / SIGNATURE_FILENAME).read_bytes()
    assert signature != hashlib.sha256(content).hexdigest().encode() + b"\n"
    assert not ((request_dir / REQUEST_FILENAME).stat().st_mode & stat.S_IWUSR)


def test_production_has_one_price_resolver_consumer_and_no_capital_imports() -> None:
    repository_root = Path(__file__).parents[1]
    authority_path = (
        repository_root / "tradingagents" / "platform" / "backtest" / "simulation_authority.py"
    )
    tree = ast.parse(authority_path.read_text(encoding="utf-8"), filename=str(authority_path))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "resolve_execution_price_authority" in imported_names
    assert "load_execution_price" not in imported_names
    assert "verify_execution_price_authority" not in imported_names
    forbidden = {"ccxt_bridge", "ExecutionIntent", "Approval", "RunMode"}
    assert not (imported_names & forbidden)

    package_root = repository_root / "tradingagents"
    resolver_allowed = {
        package_root / "platform" / "backtest" / "repository.py",
        package_root / "platform" / "backtest" / "simulation_authority.py",
        package_root
        / "platform"
        / "backtest"
        / "simulation_economic_session_authority.py",
        package_root / "platform" / "backtest" / "__init__.py",
    }
    reducer_allowed = {
        package_root / "platform" / "backtest" / "simulation_ledger.py",
        package_root / "platform" / "backtest" / "simulation_authority.py",
    }
    violations: list[str] = []
    production_paths = [
        *package_root.rglob("*.py"),
        *(repository_root / "service").rglob("*.py"),
        *(repository_root / "cli").rglob("*.py"),
        repository_root / "main.py",
    ]
    for path in production_paths:
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
        } | {
            node.value
            for node in ast.walk(parsed)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if "resolve_execution_price_authority" in referenced and path not in resolver_allowed:
            violations.append(f"unauthorized price resolver: {path.relative_to(repository_root)}")
        if referenced & {"execute_simulation_batch", "execute_simulation_run"} and (
            path not in reducer_allowed
        ):
            violations.append(f"direct simulation reducer: {path.relative_to(repository_root)}")
    assert not violations, violations


def test_signing_key_and_reference_contracts_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        FrozenSimulationAuthorityRepository(tmp_path, signing_key=b"short")
    with pytest.raises(ValueError):
        SimulationCheckpointReference(
            tenant_id=TENANT_ID,
            backtest_id=BACKTEST_ID,
            backtest_spec_digest="d" * 64,
            ledger_id=LEDGER_ID,
            checkpoint_id=CHECKPOINT_ID,
            checkpoint_digest="not-a-digest",
        )


def test_order_and_checkpoint_namespaces_allow_the_same_uuid(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(tmp_path / "dataset")
    del dataset
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    request = _request(manifest, view, batch_id=CHECKPOINT_ID)
    request_reference = authority.publish_request(request)
    checkpoint_reference = authority.publish_checkpoint(_checkpoint(manifest))
    assert request_reference.batch_id == checkpoint_reference.checkpoint_id
    assert (authority.root / f"orders-{CHECKPOINT_ID}").is_dir()
    assert (authority.root / f"checkpoints-{CHECKPOINT_ID}").is_dir()


def test_concurrent_publication_has_exactly_one_winner(tmp_path: Path) -> None:
    _, manifest, view = _dataset(tmp_path / "dataset")
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    request = _request(manifest, view)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def publish() -> None:
        barrier.wait()
        try:
            authority.publish_request(request)
        except SimulationRequestAlreadyExistsError:
            outcomes.append("exists")
        else:
            outcomes.append("published")

    threads = [threading.Thread(target=publish) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
    assert outcomes.count("published") == 1
    assert outcomes.count("exists") == 1


def test_request_cannot_predate_the_authoritative_checkpoint(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(tmp_path / "dataset")
    spec = _spec(manifest)
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    request_reference = authority.publish_request(
        _request(manifest, view, created_at=datetime(2024, 1, 2, tzinfo=UTC))
    )
    checkpoint = _checkpoint(manifest).model_copy(
        update={"created_at": datetime(2024, 1, 3, tzinfo=UTC)}
    )
    checkpoint_reference = authority.publish_checkpoint(checkpoint)
    with pytest.raises(SimulationAuthorityError, match="predates"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )


def test_all_price_bindings_validate_before_the_ledger_is_called(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset, manifest, view = _dataset(tmp_path / "dataset")
    spec = _spec(manifest)
    authority = FrozenSimulationAuthorityRepository(tmp_path / "authority", signing_key=SIGNING_KEY)
    invalid = _request(manifest, view).orders[0].model_copy(
        update={"eligible_view_digest": "f" * 64}
    )
    request_reference = authority.publish_request(_request(manifest, view, orders=(invalid,)))
    checkpoint_reference = authority.publish_checkpoint(_checkpoint(manifest))
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("ledger must not run before all prices validate")

    monkeypatch.setattr(
        "tradingagents.platform.backtest.simulation_authority.execute_simulation_batch",
        forbidden,
    )
    with pytest.raises(DatasetIntegrityError, match="authorized eligible view"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )
    assert called is False


def test_request_rejects_duplicate_instrument_orders(tmp_path: Path) -> None:
    _, manifest, view = _dataset(tmp_path / "dataset")
    first = _request(manifest, view).orders[0]
    second = first.model_copy(update={"order_id": uuid4()})
    orders = tuple(sorted((first, second), key=lambda order: str(order.order_id)))
    with pytest.raises(ValueError, match="one net order per instrument"):
        _request(manifest, view, orders=orders)


def test_spec_economics_and_complete_policy_digest_are_enforced(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(tmp_path / "dataset")
    mismatched_policy = _policy().model_copy(update={"initial_cash": Decimal("20000.00")})
    mismatched_policy = SimulationLedgerPolicy.model_validate(
        mismatched_policy.model_dump(mode="python")
    )
    spec = _spec(manifest).model_copy(
        update={"simulation_policy_digest": mismatched_policy.digest}
    )
    spec = BacktestSpec.model_validate(spec.model_dump(mode="python"))
    spec_digest = canonical_digest(spec)
    request = _request(manifest, view).model_copy(
        update={
            "backtest_spec_digest": spec_digest,
            "policy_digest": mismatched_policy.digest,
        }
    )
    request = AuthorizedSimulationRequest.model_validate(request.model_dump(mode="python"))
    checkpoint = _checkpoint(manifest, mismatched_policy).model_copy(
        update={"backtest_spec_digest": spec_digest}
    )
    checkpoint = SimulationLedgerCheckpoint.model_validate(
        checkpoint.model_dump(mode="python")
    )
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    request_reference = authority.publish_request(request)
    checkpoint_reference = authority.publish_checkpoint(checkpoint)
    with pytest.raises(SimulationAuthorityError, match="economics"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec_digest,
        )


def test_pit_decision_volume_caps_fill_without_using_next_day_volume(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(
        tmp_path / "dataset",
        decision_volume="500",
        execution_volume="999999999",
    )
    spec = _spec(manifest)
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    oversized = _request(manifest, view).orders[0].model_copy(
        update={"requested_quantity": Decimal("200")}
    )
    request = _request(manifest, view, orders=(oversized,))
    request_reference = authority.publish_request(request)
    checkpoint_reference = authority.publish_checkpoint(_checkpoint(manifest))
    result = execute_authorized_simulation_batch(
        authority,
        dataset,
        request_reference,
        checkpoint_reference,
        spec,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=canonical_digest(spec),
    )
    fill = result.ledger_result.appended_events[0].fill
    assert fill is not None
    assert fill.executed_quantity == Decimal("50")
    assert result.execution_evidence[0].decision_volume == Decimal("500")
    assert result.execution_evidence[0].liquidity_cap == Decimal("50")
    assert result.execution_evidence[0].authorized_quantity == Decimal("200")


def test_fill_and_decision_must_remain_inside_backtest_window(tmp_path: Path) -> None:
    dataset, manifest, view = _dataset(tmp_path / "dataset")
    spec = _spec(manifest).model_copy(update={"end_session": date(2024, 1, 2)})
    spec = BacktestSpec.model_validate(spec.model_dump(mode="python"))
    spec_digest = canonical_digest(spec)
    request = _request(manifest, view).model_copy(
        update={"backtest_spec_digest": spec_digest}
    )
    request = AuthorizedSimulationRequest.model_validate(request.model_dump(mode="python"))
    checkpoint = _checkpoint(manifest).model_copy(
        update={"backtest_spec_digest": spec_digest}
    )
    checkpoint = SimulationLedgerCheckpoint.model_validate(
        checkpoint.model_dump(mode="python")
    )
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    with pytest.raises(SimulationAuthorityError, match="fill falls outside"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            authority.publish_request(request),
            authority.publish_checkpoint(checkpoint),
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=spec_digest,
        )


def test_oversized_signature_and_non_regular_record_fail_closed(tmp_path: Path) -> None:
    dataset, authority, request_reference, checkpoint_reference, _ = _authorities(tmp_path)
    del dataset, checkpoint_reference
    signature = authority.root / f"orders-{BATCH_ID}" / SIGNATURE_FILENAME
    signature.chmod(stat.S_IWRITE | stat.S_IREAD)
    signature.write_bytes(b"x" * 257)
    with pytest.raises(SimulationAuthorityError, match="byte limit"):
        authority.load_request(
            request_reference,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=request_reference.backtest_spec_digest,
            expected_ledger_id=LEDGER_ID,
            expected_policy_digest=_policy().digest,
        )


def test_signature_file_tampering_is_rejected(tmp_path: Path) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(tmp_path)
    signature = authority.root / f"orders-{BATCH_ID}" / SIGNATURE_FILENAME
    signature.chmod(stat.S_IWRITE | stat.S_IREAD)
    signature.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(SimulationAuthorityError, match="signature mismatch"):
        execute_authorized_simulation_batch(
            authority,
            dataset,
            request_reference,
            checkpoint_reference,
            spec,
            expected_tenant_id=TENANT_ID,
            expected_backtest_id=BACKTEST_ID,
            expected_backtest_spec_digest=canonical_digest(spec),
        )


def test_request_authority_digest_is_canonical_and_price_free(tmp_path: Path) -> None:
    _, manifest, view = _dataset(tmp_path / "dataset")
    request = _request(manifest, view)
    assert request.authority_digest == canonical_digest(request)
    serialized = canonical_json_bytes(request.model_dump(mode="python"))
    assert b"raw_open" not in serialized
    assert b"fill_price" not in serialized


def test_result_uses_authenticated_reference_copies_when_inputs_mutate_mid_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset, authority, request_reference, checkpoint_reference, spec = _authorities(
        tmp_path
    )
    original = resolve_execution_price_authority

    def mutate_then_resolve(*args, **kwargs):
        object.__setattr__(request_reference, "tenant_id", uuid4())
        object.__setattr__(checkpoint_reference, "backtest_id", uuid4())
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "tradingagents.platform.backtest.simulation_authority.resolve_execution_price_authority",
        mutate_then_resolve,
    )
    result = execute_authorized_simulation_batch(
        authority,
        dataset,
        request_reference,
        checkpoint_reference,
        spec,
        expected_tenant_id=TENANT_ID,
        expected_backtest_id=BACKTEST_ID,
        expected_backtest_spec_digest=canonical_digest(spec),
    )
    assert result.reference.tenant_id == TENANT_ID
    assert result.prior_checkpoint.backtest_id == BACKTEST_ID


def test_result_contract_rejects_cross_bound_reference_even_with_recomputed_digest(
    tmp_path: Path,
) -> None:
    *_, result = _execute(tmp_path)
    payload = result.model_dump(mode="python", exclude={"result_digest"})
    payload["reference"]["tenant_id"] = uuid4()
    with pytest.raises(ValueError, match="custody bindings diverge"):
        AuthorizedSimulationBatchResult.model_validate(
            payload | {"result_digest": canonical_digest(payload)}
        )


def test_result_contract_binds_execution_evidence_to_the_ledger_fill(
    tmp_path: Path,
) -> None:
    *_, result = _execute(tmp_path)
    payload = result.model_dump(mode="python", exclude={"result_digest"})
    payload["execution_evidence"][0]["submitted_quantity"] = "1"
    with pytest.raises(ValueError, match="does not match its ledger fill"):
        AuthorizedSimulationBatchResult.model_validate(
            payload | {"result_digest": canonical_digest(payload)}
        )

    payload = result.model_dump(mode="python", exclude={"result_digest"})
    payload["execution_evidence"][0]["fill_digest"] = "f" * 64
    with pytest.raises(ValueError, match="does not match its ledger fill"):
        AuthorizedSimulationBatchResult.model_validate(
            payload | {"result_digest": canonical_digest(payload)}
        )


@pytest.mark.skipif(os.name == "posix", reason="Windows fallback recovery path")
def test_windows_failed_publication_removes_claim_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest, view = _dataset(tmp_path / "dataset")
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    request = _request(manifest, view)
    original_rename = os.rename

    def fail_rename(*_args, **_kwargs):
        raise OSError("injected rename failure")

    monkeypatch.setattr(os, "rename", fail_rename)
    with pytest.raises(OSError, match="injected rename failure"):
        authority.publish_request(request)
    assert not (authority.root / f".orders-{BATCH_ID}.claim").exists()
    assert not tuple(authority.root.glob(f".orders-{BATCH_ID}.*"))

    monkeypatch.setattr(os, "rename", original_rename)
    assert authority.publish_request(request).batch_id == BATCH_ID


@pytest.mark.skipif(os.name == "posix", reason="Windows fallback recovery path")
def test_windows_staging_creation_failure_removes_claim_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest, view = _dataset(tmp_path / "dataset")
    authority = FrozenSimulationAuthorityRepository(
        tmp_path / "authority", signing_key=SIGNING_KEY
    )
    request = _request(manifest, view)
    original_mkdtemp = tempfile.mkdtemp

    def fail_mkdtemp(*_args, **_kwargs):
        raise OSError("injected staging creation failure")

    monkeypatch.setattr(tempfile, "mkdtemp", fail_mkdtemp)
    with pytest.raises(OSError, match="injected staging creation failure"):
        authority.publish_request(request)
    assert not (authority.root / f".orders-{BATCH_ID}.claim").exists()
    assert not tuple(authority.root.glob(f".orders-{BATCH_ID}.*"))

    monkeypatch.setattr(tempfile, "mkdtemp", original_mkdtemp)
    assert authority.publish_request(request).batch_id == BATCH_ID
