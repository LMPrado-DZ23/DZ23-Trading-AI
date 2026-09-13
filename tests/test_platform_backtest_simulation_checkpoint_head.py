from __future__ import annotations

import hashlib
import json
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from tradingagents.platform.backtest.simulation_checkpoint_head import (
    MAX_GENERATION,
    MAX_RECORD_BYTES,
    AuthenticatedSimulationCheckpointReference,
    LocalSimulationCheckpointHeadStore,
    SimulationCheckpointCandidate,
    SimulationCheckpointExpectation,
    SimulationCheckpointHeadCapacityError,
    SimulationCheckpointHeadConflictError,
    SimulationCheckpointHeadIntegrityError,
    SimulationCheckpointScope,
)


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _scope(*, tenant: int = 1, backtest: int = 2, ledger: int = 3):
    return SimulationCheckpointScope(
        tenant_id=_uuid(tenant),
        backtest_id=_uuid(backtest),
        ledger_id=_uuid(ledger),
        backtest_spec_digest=_digest(f"spec-{tenant}-{backtest}-{ledger}"),
    )


def _candidate(
    value: int,
    *,
    scope: SimulationCheckpointScope | None = None,
    previous: str | None = None,
    digest_label: str | None = None,
    authority_label: str | None = None,
):
    bound_scope = scope or _scope()
    return SimulationCheckpointCandidate(
        reference=AuthenticatedSimulationCheckpointReference(
            scope=bound_scope,
            checkpoint_id=_uuid(value),
            checkpoint_digest=_digest(digest_label or f"checkpoint-{value}"),
            previous_checkpoint_digest=previous,
            authority_record_digest=_digest(
                authority_label or f"authority-{bound_scope.digest}-{value}"
            ),
        )
    )


def _record_path(root: Path, scope: SimulationCheckpointScope, generation: int) -> Path:
    return root / scope.digest / "records" / f"{generation:020d}.json"


def _process_append(root, scope_payload, expected_payload, candidate_payload, ready, start, queue):
    try:
        store = LocalSimulationCheckpointHeadStore(root)
        scope = SimulationCheckpointScope.model_validate(scope_payload)
        expected = SimulationCheckpointExpectation.model_validate(expected_payload)
        candidate = SimulationCheckpointCandidate.model_validate(candidate_payload)
        ready.put("ready")
        start.wait(10)
        result = store.compare_and_append(scope, expected, candidate)
        queue.put(("ok", result.model_dump(mode="json")))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


def test_empty_scope_and_monotonic_append_bind_every_cas_coordinate(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()

    assert store.read_head(scope) is None
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    second = store.compare_and_append(
        scope, first.expectation, _candidate(11, previous=first.checkpoint_digest)
    )

    assert first.generation == 1
    assert first.previous_checkpoint_id is None
    assert first.previous_checkpoint_digest is None
    assert second.generation == 2
    assert second.previous_checkpoint_id == first.checkpoint_id
    assert second.previous_checkpoint_digest == first.checkpoint_digest
    assert store.read_head(scope) == second


@pytest.mark.parametrize("coordinate", ["checkpoint_id", "checkpoint_digest", "generation"])
def test_every_stale_expected_coordinate_rejects_without_publishing(tmp_path, coordinate):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    values = first.expectation.model_dump(mode="python")
    if coordinate == "checkpoint_id":
        values[coordinate] = _uuid(999)
    elif coordinate == "checkpoint_digest":
        values[coordinate] = _digest("wrong")
    else:
        values[coordinate] = 2
    stale = SimulationCheckpointExpectation(**values)

    with pytest.raises(SimulationCheckpointHeadConflictError, match="stale append or fork"):
        store.compare_and_append(scope, stale, _candidate(11, previous=stale.checkpoint_digest))

    assert store.read_head(scope) == first
    assert not _record_path(tmp_path, scope, 2).exists()


def test_exact_replay_is_deterministic_before_and_after_later_appends(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    empty = SimulationCheckpointExpectation.empty()
    candidate = _candidate(10)

    first = store.compare_and_append(scope, empty, candidate)
    assert store.compare_and_append(scope, empty, candidate) == first
    second = store.compare_and_append(
        scope, first.expectation, _candidate(11, previous=first.checkpoint_digest)
    )

    assert store.compare_and_append(scope, empty, candidate) == first
    assert store.read_head(scope) == second


def test_candidate_scope_and_actual_predecessor_are_not_relabelable(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    foreign_scope = _scope(tenant=999)
    empty = SimulationCheckpointExpectation.empty()

    with pytest.raises(SimulationCheckpointHeadConflictError, match="another custody scope"):
        store.compare_and_append(scope, empty, _candidate(10, scope=foreign_scope))
    with pytest.raises(SimulationCheckpointHeadConflictError, match="genesis append"):
        store.compare_and_append(
            scope, empty, _candidate(10, previous=_digest("non-genesis-parent"))
        )

    first = store.compare_and_append(scope, empty, _candidate(10))
    with pytest.raises(SimulationCheckpointHeadConflictError, match="expected predecessor"):
        store.compare_and_append(
            scope,
            first.expectation,
            _candidate(11, previous=_digest("stale-or-foreign-parent")),
        )
    assert store.read_head(scope) == first


def test_authenticated_reference_digest_is_part_of_the_immutable_append(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    expected = SimulationCheckpointExpectation.empty()
    first = store.compare_and_append(scope, expected, _candidate(10))

    with pytest.raises(SimulationCheckpointHeadConflictError, match="lineage slot"):
        store.compare_and_append(
            scope,
            expected,
            _candidate(10, authority_label="different-authenticated-authority-record"),
        )
    assert store.read_head(scope) == first


def test_reused_checkpoint_id_cannot_change_digest_or_create_rollback(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    first_bytes = _record_path(tmp_path, scope, first.generation).read_bytes()
    second = store.compare_and_append(
        scope, first.expectation, _candidate(11, previous=first.checkpoint_digest)
    )

    with pytest.raises(SimulationCheckpointHeadConflictError, match="lineage slot"):
        store.compare_and_append(
            scope,
            SimulationCheckpointExpectation.empty(),
            _candidate(10, digest_label="replacement"),
        )
    with pytest.raises(SimulationCheckpointHeadConflictError, match="already bound"):
        store.compare_and_append(
            scope,
            second.expectation,
            _candidate(10, previous=second.checkpoint_digest),
        )

    assert _record_path(tmp_path, scope, first.generation).read_bytes() == first_bytes
    assert store.read_head(scope) == second


def test_sibling_race_has_one_winner_and_one_fail_closed_conflict(tmp_path):
    scope = _scope()
    bootstrap = LocalSimulationCheckpointHeadStore(tmp_path)
    first = bootstrap.compare_and_append(
        scope, SimulationCheckpointExpectation.empty(), _candidate(10)
    )
    barrier = threading.Barrier(2)

    def append(value: int):
        store = LocalSimulationCheckpointHeadStore(tmp_path)
        barrier.wait()
        try:
            return store.compare_and_append(
                scope,
                first.expectation,
                _candidate(value, previous=first.checkpoint_digest),
            )
        except SimulationCheckpointHeadConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(append, (11, 12)))

    winners = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]
    assert len(winners) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], SimulationCheckpointHeadConflictError)
    assert bootstrap.read_head(scope) == winners[0]
    assert winners[0].generation == 2


def test_same_append_race_is_an_exact_replay_not_a_duplicate(tmp_path):
    scope = _scope()
    expected = SimulationCheckpointExpectation.empty()
    candidate = _candidate(10)
    barrier = threading.Barrier(2)

    def append():
        store = LocalSimulationCheckpointHeadStore(tmp_path)
        barrier.wait()
        return store.compare_and_append(scope, expected, candidate)

    with ThreadPoolExecutor(max_workers=2) as executor:
        left, right = tuple(executor.map(lambda _: append(), range(2)))

    assert left == right
    assert left.generation == 1
    assert len(tuple((tmp_path / scope.digest / "records").glob("*.json"))) == 1


def test_cross_process_sibling_race_is_serialized_by_storage_lock(tmp_path):
    context = multiprocessing.get_context("spawn")
    scope = _scope()
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = tuple(
        context.Process(
            target=_process_append,
            args=(
                str(tmp_path),
                scope.model_dump(mode="json"),
                first.expectation.model_dump(mode="json"),
                _candidate(value, previous=first.checkpoint_digest).model_dump(mode="json"),
                ready,
                start,
                results,
            ),
        )
        for value in (11, 12)
    )
    for process in processes:
        process.start()
    assert {ready.get(timeout=15), ready.get(timeout=15)} == {"ready"}
    start.set()
    outcomes = (results.get(timeout=15), results.get(timeout=15))
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
    assert next(outcome[1] for outcome in outcomes if outcome[0] == "error") == (
        "SimulationCheckpointHeadConflictError"
    )
    assert store.read_head(scope).generation == 2


def test_scope_isolation_prevents_cross_tenant_or_cross_ledger_heads(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scopes = (_scope(), _scope(tenant=4), _scope(ledger=5))

    heads = tuple(
        store.compare_and_append(
            scope,
            SimulationCheckpointExpectation.empty(),
            _candidate(10, scope=scope),
        )
        for scope in scopes
    )

    assert tuple(store.read_head(scope) for scope in scopes) == heads
    assert len({scope.digest for scope in scopes}) == 3


def test_crash_after_append_record_is_recovered_only_by_exact_retry(tmp_path, monkeypatch):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    expected = SimulationCheckpointExpectation.empty()
    candidate = _candidate(10)
    real_replace = store._atomic_replace

    def crash_before_head(path, content):
        raise OSError("injected crash before atomic head publication")

    monkeypatch.setattr(store, "_atomic_replace", crash_before_head)
    with pytest.raises(OSError, match="injected crash"):
        store.compare_and_append(scope, expected, candidate)

    monkeypatch.setattr(store, "_atomic_replace", real_replace)
    recovered = store.compare_and_append(scope, expected, candidate)
    assert recovered.generation == 1
    assert store.read_head(scope) == recovered


def test_crash_orphan_claims_lineage_slot_and_rejects_different_sibling(tmp_path, monkeypatch):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    expected = SimulationCheckpointExpectation.empty()
    orphan = _candidate(10)
    real_replace = store._atomic_replace
    monkeypatch.setattr(
        store,
        "_atomic_replace",
        lambda path, content: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError, match="crash"):
        store.compare_and_append(scope, expected, orphan)

    monkeypatch.setattr(store, "_atomic_replace", real_replace)
    with pytest.raises(SimulationCheckpointHeadConflictError, match="sibling append"):
        store.compare_and_append(scope, expected, _candidate(11))
    recovered = store.compare_and_append(scope, expected, orphan)
    assert store.read_head(scope) == recovered
    assert len(tuple((tmp_path / scope.digest / "records").glob("*.json"))) == 1


def test_cross_process_cannot_fill_a_lineage_slot_claimed_before_crash(tmp_path, monkeypatch):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    expected = SimulationCheckpointExpectation.empty()
    real_replace = store._atomic_replace
    monkeypatch.setattr(
        store,
        "_atomic_replace",
        lambda path, content: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError, match="crash"):
        store.compare_and_append(scope, expected, _candidate(10))
    monkeypatch.setattr(store, "_atomic_replace", real_replace)

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_process_append,
        args=(
            str(tmp_path),
            scope.model_dump(mode="json"),
            expected.model_dump(mode="json"),
            _candidate(11).model_dump(mode="json"),
            ready,
            start,
            results,
        ),
    )
    process.start()
    assert ready.get(timeout=15) == "ready"
    start.set()
    assert results.get(timeout=15) == (
        "error",
        "SimulationCheckpointHeadConflictError",
    )
    process.join(timeout=15)
    assert process.exitcode == 0

    assert store.read_head(scope) is None
    assert store.compare_and_append(scope, expected, _candidate(10)).checkpoint_id == _uuid(10)


def test_orphan_record_cannot_be_adopted_from_a_different_predecessor(tmp_path, monkeypatch):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    real_replace = store._atomic_replace
    monkeypatch.setattr(
        store,
        "_atomic_replace",
        lambda path, content: (_ for _ in ()).throw(OSError("crash")),
    )
    with pytest.raises(OSError, match="crash"):
        store.compare_and_append(
            scope,
            first.expectation,
            _candidate(11, previous=first.checkpoint_digest),
        )

    monkeypatch.setattr(store, "_atomic_replace", real_replace)
    wrong_expected = SimulationCheckpointExpectation(
        checkpoint_id=_uuid(999), checkpoint_digest=_digest("wrong"), generation=1
    )
    with pytest.raises(SimulationCheckpointHeadConflictError, match="expected predecessor"):
        store.compare_and_append(
            scope,
            wrong_expected,
            _candidate(11, previous=first.checkpoint_digest),
        )
    assert store.read_head(scope) == first


def test_noncanonical_or_oversized_head_fails_closed(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    head_path = tmp_path / scope.digest / "head.json"
    payload = json.loads(head_path.read_bytes())
    head_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    with pytest.raises(SimulationCheckpointHeadIntegrityError, match="canonical bytes"):
        store.read_head(scope)

    head_path.write_bytes(b"{" + (b" " * MAX_RECORD_BYTES) + b"}")
    with pytest.raises(SimulationCheckpointHeadCapacityError, match="byte limit"):
        store.read_head(scope)


def test_head_cannot_point_at_a_missing_or_different_append_record(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    first = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))
    record_path = _record_path(tmp_path, scope, first.generation)
    record_path.unlink()

    with pytest.raises(SimulationCheckpointHeadIntegrityError, match="missing its immutable"):
        store.read_head(scope)


def test_contracts_bound_generation_identity_timeout_and_exact_runtime_types(tmp_path):
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(checkpoint_id=_uuid(1), generation=1)
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(checkpoint_digest=_digest("x"), generation=1)
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(generation=1)
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(
            checkpoint_id=_uuid(1), checkpoint_digest=_digest("x"), generation=0
        )
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(generation=MAX_GENERATION + 1)
    with pytest.raises(ValidationError):
        SimulationCheckpointExpectation(generation=False)
    for timeout in (True, 0, -1, float("nan"), float("inf"), 61):
        with pytest.raises(ValueError, match="lock timeout"):
            LocalSimulationCheckpointHeadStore(
                tmp_path / str(timeout), lock_timeout_seconds=timeout
            )

    store = LocalSimulationCheckpointHeadStore(tmp_path / "valid")
    with pytest.raises(SimulationCheckpointHeadIntegrityError, match="root is not a directory"):
        file_root = tmp_path / "file-root"
        file_root.write_text("not a directory", encoding="utf-8")
        LocalSimulationCheckpointHeadStore(file_root)

    class ScopeSubclass(SimulationCheckpointScope):
        pass

    valid_scope = _scope()
    forged_scope = ScopeSubclass(**valid_scope.model_dump(mode="python"))
    with pytest.raises(ValueError, match="exact typed contract"):
        store.read_head(forged_scope)


def test_files_are_canonical_json_and_never_pickle_or_caller_selected_paths(tmp_path):
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    head = store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))

    record = _record_path(tmp_path, scope, head.generation).read_bytes()
    assert record.endswith(b"\n")
    assert record == (tmp_path / scope.digest / "head.json").read_bytes()
    assert json.loads(record)["checkpoint_id"] == str(head.checkpoint_id)
    assert not any(path.suffix in {".pickle", ".pkl"} for path in tmp_path.rglob("*"))


def test_publication_requests_directory_metadata_durability(tmp_path, monkeypatch):
    persisted = []
    monkeypatch.setattr(
        LocalSimulationCheckpointHeadStore,
        "_fsync_directory",
        staticmethod(lambda path: persisted.append(Path(path))),
    )
    store = LocalSimulationCheckpointHeadStore(tmp_path)
    scope = _scope()
    store.compare_and_append(scope, SimulationCheckpointExpectation.empty(), _candidate(10))

    scope_directory = tmp_path / scope.digest
    assert tmp_path in persisted
    assert scope_directory in persisted
    assert scope_directory / "records" in persisted
    assert scope_directory / "checkpoint-identities" in persisted
