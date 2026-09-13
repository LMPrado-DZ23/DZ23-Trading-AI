"""Authenticated, cached authority for M5.2c economic replay inputs."""

from __future__ import annotations

import hmac
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError

from tradingagents.platform.backtest.contracts import (
    BacktestSpecV3,
    DatasetRole,
    canonical_digest,
    canonical_json_bytes,
)
from tradingagents.platform.backtest.errors import DatasetIntegrityError
from tradingagents.platform.backtest.repository import (
    FrozenDatasetRepository,
    FrozenEconomicsDatasetHandle,
)
from tradingagents.platform.backtest.simulation_economics import (
    AuthorizedCorporateCashConversion,
    AuthorizedEconomicEventView,
    AuthorizedFxConversion,
    CorporateActionApplication,
    CorporateCashLedgerBasis,
    EconomicPhase,
    SimulationEconomicsError,
    SimulationEconomicsPolicy,
    _convert_quote_to_functional,
    _derive_corporate_cash_source_amount,
)
from tradingagents.platform.backtest.simulation_ledger import SimulationLedgerPolicy

_AUTHORITY_TOKEN = object()
_CORPORATE_CASH_BASIS_TOKEN = object()
_HEX_DIGITS = frozenset("0123456789abcdef")


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
        character not in _HEX_DIGITS for character in value
    ):
        raise SimulationEconomicsError(f"{label} must be one lowercase SHA-256 digest")
    return value


class _VerifiedCorporateCashLedgerBasis:
    """Opaque, instance-bound proof that the trusted ledger supplied a basis."""

    __slots__ = ("_authority", "_basis", "_sealed")

    def __init__(self, *, token: object, authority: object, basis: CorporateCashLedgerBasis):
        if token is not _CORPORATE_CASH_BASIS_TOKEN:
            raise SimulationEconomicsError("verified ledger basis cannot be constructed directly")
        object.__setattr__(self, "_authority", authority)
        object.__setattr__(self, "_basis", basis)
        object.__setattr__(self, "_sealed", True)

    @property
    def authority(self) -> object:
        return self._authority

    @property
    def basis(self) -> CorporateCashLedgerBasis:
        return self._basis

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("verified corporate-cash basis is immutable")
        object.__setattr__(self, name, value)


class FrozenSimulationEconomicsAuthority:
    """One verified dataset and policy context reused for an offline replay.

    This is a safe application boundary, not a Python sandbox. Untrusted Python,
    plugins and user-provided code must never execute in this process.
    """

    __slots__ = (
        "_economics_policy",
        "_handle",
        "_handle_binding_digest",
        "_ledger_policy",
        "_repository",
        "_sealed",
        "_spec",
        "_spec_digest",
    )

    def __init__(
        self,
        *,
        token: object,
        repository: FrozenDatasetRepository,
        handle: FrozenEconomicsDatasetHandle,
        spec: BacktestSpecV3,
        spec_digest: str,
        ledger_policy: SimulationLedgerPolicy,
        economics_policy: SimulationEconomicsPolicy,
    ) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise SimulationEconomicsError(
                "economics authority must be opened from frozen repositories"
            )
        object.__setattr__(self, "_repository", repository)
        object.__setattr__(self, "_handle", handle)
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_spec_digest", spec_digest)
        object.__setattr__(self, "_ledger_policy", ledger_policy)
        object.__setattr__(self, "_economics_policy", economics_policy)
        object.__setattr__(
            self,
            "_handle_binding_digest",
            canonical_digest(
                {
                    "version": "simulation-economics-handle-binding/1",
                    "tenant_id": handle.tenant_id,
                    "dataset_id": handle.dataset_id,
                    "manifest_digest": handle.manifest_digest,
                    "instrument_id": handle.instrument_id,
                    "quote_currency": handle.quote_currency,
                    "schema_version": handle.schema_version,
                }
            ),
        )
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("frozen economics authority cannot be mutated")
        object.__setattr__(self, name, value)

    @property
    def dataset_id(self) -> UUID:
        return self._handle.dataset_id

    @property
    def instrument_id(self) -> UUID:
        return self._handle.instrument_id

    def _assert_sealed_bindings(self) -> None:
        current_handle_binding_digest = canonical_digest(
            {
                "version": "simulation-economics-handle-binding/1",
                "tenant_id": self._handle.tenant_id,
                "dataset_id": self._handle.dataset_id,
                "manifest_digest": self._handle.manifest_digest,
                "instrument_id": self._handle.instrument_id,
                "quote_currency": self._handle.quote_currency,
                "schema_version": self._handle.schema_version,
            }
        )
        references = tuple(
            item
            for item in self._spec.dataset_references
            if item.role is DatasetRole.TRADEABLE
            and item.dataset_id == self._handle.dataset_id
        )
        instrument_bindings = tuple(
            item
            for item in self._economics_policy.instrument_bindings
            if item.instrument_id == self._handle.instrument_id
        )
        if (
            not hmac.compare_digest(canonical_digest(self._spec), self._spec_digest)
            or not hmac.compare_digest(
                current_handle_binding_digest, self._handle_binding_digest
            )
            or not hmac.compare_digest(
                self._spec.simulation_policy_digest, self._ledger_policy.digest
            )
            or not hmac.compare_digest(
                self._spec.economics_policy_digest, self._economics_policy.digest
            )
            or self._spec.tenant_id != self._economics_policy.tenant_id
            or self._spec.backtest_id != self._economics_policy.backtest_id
            or self._ledger_policy.ledger_id != self._economics_policy.ledger_id
            or self._spec.base_currency != self._ledger_policy.base_currency
            or self._spec.base_currency != self._economics_policy.functional_currency
            or self._ledger_policy.money_quantum != self._economics_policy.money_quantum
            or not hmac.compare_digest(
                self._economics_policy.fx_book_digest, self._handle.fx_book_digest
            )
            or not self._handle.is_owned_by(self._repository)
            or self._handle.tenant_id != self._spec.tenant_id
            or self._handle.schema_version != 2
            or len(references) != 1
            or references[0].instrument_id != self._handle.instrument_id
            or not hmac.compare_digest(
                references[0].manifest_digest, self._handle.manifest_digest
            )
            or len(instrument_bindings) != 1
            or instrument_bindings[0].dataset_id != self._handle.dataset_id
            or not hmac.compare_digest(
                instrument_bindings[0].manifest_digest, self._handle.manifest_digest
            )
            or instrument_bindings[0].quote_currency != self._handle.quote_currency
        ):
            raise SimulationEconomicsError("economics authority bindings changed after open")

    def _event_at(self, session_date: date, phase: EconomicPhase):
        self._assert_sealed_bindings()
        if type(session_date) is not date or type(phase) is not EconomicPhase:
            raise SimulationEconomicsError("economic session and phase are invalid")
        if not self._spec.start_session <= session_date <= self._spec.end_session:
            raise SimulationEconomicsError("economic session is outside the backtest window")
        try:
            return self._handle.event_at(session_date, phase)
        except DatasetIntegrityError as exc:
            raise SimulationEconomicsError(
                "economic session is absent or ambiguous"
            ) from exc

    def resolve_event(
        self, session_date: date, phase: EconomicPhase
    ) -> AuthorizedEconomicEventView:
        event_at = self._event_at(session_date, phase)
        try:
            view = self._repository.load_economic_event_view(
                self.dataset_id,
                expected_tenant_id=self._spec.tenant_id,
                expected_instrument_id=self.instrument_id,
                expected_manifest_digest=self._handle.manifest_digest,
                session_date=session_date,
                phase=phase,
                as_of=event_at,
                _economics_handle=self._handle,
            )
        except DatasetIntegrityError as exc:
            raise SimulationEconomicsError(
                "frozen corporate-action evidence is invalid or unavailable"
            ) from exc
        payload = {
            "authority_version": "authorized-economic-event-view/1",
            "tenant_id": self._spec.tenant_id,
            "backtest_id": self._spec.backtest_id,
            "ledger_id": self._ledger_policy.ledger_id,
            "backtest_spec_digest": self._spec_digest,
            "ledger_policy_digest": self._ledger_policy.digest,
            "economics_policy_digest": self._economics_policy.digest,
            "dataset_id": self.dataset_id,
            "manifest_digest": self._handle.manifest_digest,
            "instrument_id": self.instrument_id,
            "quantitative_claims_status": self._handle.quantitative_claims_status,
            "limitations": self._handle.limitations,
            "view": view.model_dump(mode="python"),
        }
        try:
            return AuthorizedEconomicEventView(
                **payload, authority_digest=canonical_digest(payload)
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError(
                "authorized economic event could not be represented"
            ) from exc

    def convert(
        self,
        source_amount: Decimal,
        session_date: date,
        phase: EconomicPhase,
        *,
        source_reference_digest: str,
    ) -> AuthorizedFxConversion:
        source_reference_digest = _require_digest(
            source_reference_digest, "source_reference_digest"
        )
        event_at = self._event_at(session_date, phase)
        try:
            projection = self._repository.load_fx_rate(
                self.dataset_id,
                expected_tenant_id=self._spec.tenant_id,
                expected_instrument_id=self.instrument_id,
                expected_manifest_digest=self._handle.manifest_digest,
                functional_currency=self._economics_policy.functional_currency,
                source_currency=self._handle.quote_currency,
                event_at=event_at,
                max_staleness_seconds=self._economics_policy.fx_max_staleness_seconds,
                _economics_handle=self._handle,
            )
        except DatasetIntegrityError as exc:
            raise SimulationEconomicsError(
                "frozen FX evidence is invalid or unavailable"
            ) from exc
        try:
            conversion = _convert_quote_to_functional(
                source_amount,
                projection,
                money_quantum=self._economics_policy.money_quantum,
            )
        except SimulationEconomicsError:
            raise
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError(
                "authorized FX conversion could not be calculated"
            ) from exc
        payload = {
            "authority_version": "authorized-fx-conversion/1",
            "tenant_id": self._spec.tenant_id,
            "backtest_id": self._spec.backtest_id,
            "ledger_id": self._ledger_policy.ledger_id,
            "backtest_spec_digest": self._spec_digest,
            "ledger_policy_digest": self._ledger_policy.digest,
            "economics_policy_digest": self._economics_policy.digest,
            "dataset_id": self.dataset_id,
            "manifest_digest": self._handle.manifest_digest,
            "instrument_id": self.instrument_id,
            "quantitative_claims_status": self._handle.quantitative_claims_status,
            "limitations": self._handle.limitations,
            "purpose": "market_valuation",
            "source_reference_digest": source_reference_digest,
            "session_date": session_date,
            "phase": phase,
            "event_at": event_at,
            "projection": projection.model_dump(mode="python"),
            "conversion": conversion.model_dump(mode="python"),
        }
        try:
            return AuthorizedFxConversion(
                **payload, authority_digest=canonical_digest(payload)
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError(
                "authorized FX conversion could not be represented"
            ) from exc

    def verify_event(
        self,
        candidate: AuthorizedEconomicEventView,
        *,
        expected_session_date: date,
        expected_phase: EconomicPhase,
    ) -> AuthorizedEconomicEventView:
        if type(candidate) is not AuthorizedEconomicEventView:
            raise SimulationEconomicsError("event candidate uses another contract type")
        try:
            checked = AuthorizedEconomicEventView.model_validate(
                candidate.model_dump(mode="python")
            )
        except ValidationError as exc:
            raise SimulationEconomicsError("event candidate is structurally invalid") from exc
        authoritative = self.resolve_event(expected_session_date, expected_phase)
        if not hmac.compare_digest(
            canonical_json_bytes(checked), canonical_json_bytes(authoritative)
        ):
            raise SimulationEconomicsError(
                "event candidate does not match frozen economic authority"
            )
        return authoritative

    def verify_conversion(
        self,
        candidate: AuthorizedFxConversion,
        *,
        expected_source_amount: Decimal,
        expected_session_date: date,
        expected_phase: EconomicPhase,
        expected_source_reference_digest: str,
    ) -> AuthorizedFxConversion:
        if type(candidate) is not AuthorizedFxConversion:
            raise SimulationEconomicsError("FX candidate uses another contract type")
        try:
            checked = AuthorizedFxConversion.model_validate(
                candidate.model_dump(mode="python")
            )
        except ValidationError as exc:
            raise SimulationEconomicsError("FX candidate is structurally invalid") from exc
        authoritative = self.convert(
            expected_source_amount,
            expected_session_date,
            expected_phase,
            source_reference_digest=expected_source_reference_digest,
        )
        if not hmac.compare_digest(
            canonical_json_bytes(checked), canonical_json_bytes(authoritative)
        ):
            raise SimulationEconomicsError(
                "FX candidate does not match frozen economic authority"
            )
        return authoritative

    def _bind_corporate_cash_ledger_basis(
        self,
        basis: CorporateCashLedgerBasis,
        *,
        expected_basis_digest: str,
        expected_checkpoint_digest: str,
        expected_prior_session_receipt_digest: str,
    ) -> _VerifiedCorporateCashLedgerBasis:
        """Bind a basis rederived by the integrated ledger authority.

        This private method is not a user-facing authorization boundary.  Its
        three expected digests must come from the authenticated checkpoint and
        session coordinator, never from request payloads.
        """

        if type(basis) is not CorporateCashLedgerBasis:
            raise SimulationEconomicsError("corporate cash basis uses another contract type")
        expected_basis_digest = _require_digest(expected_basis_digest, "expected_basis_digest")
        expected_checkpoint_digest = _require_digest(
            expected_checkpoint_digest, "expected_checkpoint_digest"
        )
        expected_prior_session_receipt_digest = _require_digest(
            expected_prior_session_receipt_digest,
            "expected_prior_session_receipt_digest",
        )
        try:
            checked = CorporateCashLedgerBasis.model_validate(
                basis.model_dump(mode="python")
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError("corporate cash basis is invalid") from exc
        if (
            not hmac.compare_digest(checked.basis_digest, expected_basis_digest)
            or not hmac.compare_digest(
                checked.checkpoint_digest, expected_checkpoint_digest
            )
            or not hmac.compare_digest(
                checked.prior_session_receipt_digest,
                expected_prior_session_receipt_digest,
            )
            or checked.tenant_id != self._spec.tenant_id
            or checked.backtest_id != self._spec.backtest_id
            or checked.ledger_id != self._ledger_policy.ledger_id
            or checked.backtest_spec_digest != self._spec_digest
            or checked.ledger_policy_digest != self._ledger_policy.digest
            or checked.economics_policy_digest != self._economics_policy.digest
            or checked.instrument_id != self.instrument_id
            or checked.phase is not EconomicPhase.POST_CLOSE
        ):
            raise SimulationEconomicsError(
                "corporate cash basis differs from frozen authority or checkpoint"
            )
        return _VerifiedCorporateCashLedgerBasis(
            token=_CORPORATE_CASH_BASIS_TOKEN,
            authority=self,
            basis=checked,
        )

    def _convert_corporate_cash_from_ledger_basis(
        self,
        verified_basis: _VerifiedCorporateCashLedgerBasis,
    ) -> AuthorizedCorporateCashConversion:
        """Convert one payable action using only a verified ledger basis."""

        if (
            type(verified_basis) is not _VerifiedCorporateCashLedgerBasis
            or verified_basis.authority is not self
        ):
            raise SimulationEconomicsError("corporate cash basis is not owned by this authority")
        try:
            basis = CorporateCashLedgerBasis.model_validate(
                verified_basis.basis.model_dump(mode="python")
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError("verified corporate cash basis was corrupted") from exc
        authoritative_event = self.resolve_event(basis.session_date, EconomicPhase.POST_CLOSE)
        matches = tuple(
            action
            for action in authoritative_event.view.actions
            if action.action_id == basis.action_id
            and action.application is basis.application
        )
        if len(matches) != 1:
            raise SimulationEconomicsError(
                "payable corporate action is absent or ambiguous"
            )
        action = matches[0]
        if basis.application is CorporateActionApplication.DIVIDEND_PAYMENT:
            unit_cash_amount = action.cash_amount
            quantity_basis = "entitled_quantity"
        else:
            unit_cash_amount = action.cash_consideration
            quantity_basis = "position_quantity"
        if unit_cash_amount is None or action.currency is None:
            raise SimulationEconomicsError(
                "payable corporate action has no authoritative cash terms"
            )
        try:
            source_amount = _derive_corporate_cash_source_amount(
                unit_cash_amount, basis.quantity
            )
            projection = self._repository.load_fx_rate(
                self.dataset_id,
                expected_tenant_id=self._spec.tenant_id,
                expected_instrument_id=self.instrument_id,
                expected_manifest_digest=self._handle.manifest_digest,
                functional_currency=self._economics_policy.functional_currency,
                source_currency=action.currency,
                event_at=authoritative_event.view.as_of,
                max_staleness_seconds=self._economics_policy.fx_max_staleness_seconds,
                _economics_handle=self._handle,
            )
            conversion = _convert_quote_to_functional(
                source_amount,
                projection,
                money_quantum=self._economics_policy.money_quantum,
            )
        except DatasetIntegrityError as exc:
            raise SimulationEconomicsError(
                "frozen corporate-cash FX evidence is invalid or unavailable"
            ) from exc
        except SimulationEconomicsError:
            raise
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError(
                "authorized corporate cash could not be calculated"
            ) from exc
        payload = {
            "authority_version": "authorized-corporate-cash-conversion/1",
            "tenant_id": self._spec.tenant_id,
            "backtest_id": self._spec.backtest_id,
            "ledger_id": self._ledger_policy.ledger_id,
            "backtest_spec_digest": self._spec_digest,
            "ledger_policy_digest": self._ledger_policy.digest,
            "economics_policy_digest": self._economics_policy.digest,
            "dataset_id": self.dataset_id,
            "manifest_digest": self._handle.manifest_digest,
            "instrument_id": self.instrument_id,
            "quantitative_claims_status": self._handle.quantitative_claims_status,
            "limitations": self._handle.limitations,
            "purpose": "corporate_action_cash",
            "ledger_basis_digest": basis.basis_digest,
            "checkpoint_id": basis.checkpoint_id,
            "checkpoint_digest": basis.checkpoint_digest,
            "checkpoint_generation": basis.checkpoint_generation,
            "prior_session_receipt_digest": basis.prior_session_receipt_digest,
            "quantity_source_digest": basis.quantity_source_digest,
            "ledger_receipt_id": basis.ledger_receipt_id,
            "event_authority_digest": authoritative_event.authority_digest,
            "event_view_digest": authoritative_event.view.view_digest,
            "action_id": action.action_id,
            "action_revision": action.revision,
            "application": action.application,
            "action_digest": action.action_digest,
            "action_projection_digest": action.projection_digest,
            "quantity_basis": quantity_basis,
            "expected_quantity": basis.quantity,
            "unit_cash_amount": unit_cash_amount,
            "source_currency": action.currency,
            "session_date": authoritative_event.view.session_date,
            "phase": authoritative_event.view.phase,
            "event_at": authoritative_event.view.as_of,
            "projection": projection.model_dump(mode="python"),
            "conversion": conversion.model_dump(mode="python"),
        }
        try:
            return AuthorizedCorporateCashConversion(
                **payload, authority_digest=canonical_digest(payload)
            )
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise SimulationEconomicsError(
                "authorized corporate cash could not be represented"
            ) from exc

    def _verify_corporate_cash_conversion(
        self,
        candidate: AuthorizedCorporateCashConversion,
        verified_basis: _VerifiedCorporateCashLedgerBasis,
    ) -> AuthorizedCorporateCashConversion:
        """Recheck a cash result against frozen evidence and one opaque basis."""

        if type(candidate) is not AuthorizedCorporateCashConversion:
            raise SimulationEconomicsError(
                "corporate cash candidate uses another contract type"
            )
        try:
            checked = AuthorizedCorporateCashConversion.model_validate(
                candidate.model_dump(mode="python")
            )
        except ValidationError as exc:
            raise SimulationEconomicsError(
                "corporate cash candidate is structurally invalid"
            ) from exc
        authoritative = self._convert_corporate_cash_from_ledger_basis(verified_basis)
        if not hmac.compare_digest(
            canonical_json_bytes(checked), canonical_json_bytes(authoritative)
        ):
            raise SimulationEconomicsError(
                "corporate cash candidate does not match frozen economic authority"
            )
        return authoritative


def open_simulation_economics_authority(
    repository: FrozenDatasetRepository,
    dataset_id: UUID,
    *,
    backtest_spec: BacktestSpecV3,
    ledger_policy: SimulationLedgerPolicy,
    economics_policy: SimulationEconomicsPolicy,
    expected_tenant_id: UUID,
    expected_backtest_id: UUID,
    expected_backtest_spec_digest: str,
    expected_economics_policy_digest: str,
) -> FrozenSimulationEconomicsAuthority:
    """Open one cached authority from externally anchored identities."""

    if type(repository) is not FrozenDatasetRepository:
        raise SimulationEconomicsError("dataset repository uses another authority type")
    if type(dataset_id) is not UUID:
        raise SimulationEconomicsError("dataset identifier must use the exact UUID type")
    if type(expected_tenant_id) is not UUID or type(expected_backtest_id) is not UUID:
        raise SimulationEconomicsError("expected authority identities must use UUID values")
    expected_backtest_spec_digest = _require_digest(
        expected_backtest_spec_digest, "expected_backtest_spec_digest"
    )
    expected_economics_policy_digest = _require_digest(
        expected_economics_policy_digest, "expected_economics_policy_digest"
    )
    if type(backtest_spec) is not BacktestSpecV3:
        raise SimulationEconomicsError("backtest spec must use schema v3")
    if type(ledger_policy) is not SimulationLedgerPolicy:
        raise SimulationEconomicsError("ledger policy uses another contract type")
    if type(economics_policy) is not SimulationEconomicsPolicy:
        raise SimulationEconomicsError("economics policy uses another contract type")
    try:
        checked_spec = BacktestSpecV3.model_validate(
            backtest_spec.model_dump(mode="python")
        )
        checked_ledger = SimulationLedgerPolicy.model_validate(
            ledger_policy.model_dump(mode="python")
        )
        checked_economics = SimulationEconomicsPolicy.model_validate(
            economics_policy.model_dump(mode="python")
        )
    except ValidationError as exc:
        raise SimulationEconomicsError("economics authority inputs are invalid") from exc
    spec_digest = canonical_digest(checked_spec)
    if (
        checked_spec.tenant_id != expected_tenant_id
        or checked_spec.backtest_id != expected_backtest_id
        or not hmac.compare_digest(spec_digest, expected_backtest_spec_digest)
        or not hmac.compare_digest(
            checked_economics.digest, expected_economics_policy_digest
        )
    ):
        raise SimulationEconomicsError("economics authority identity is not anchored")
    if (
        not hmac.compare_digest(
            checked_spec.simulation_policy_digest, checked_ledger.digest
        )
        or not hmac.compare_digest(
            checked_spec.economics_policy_digest, checked_economics.digest
        )
        or checked_spec.base_currency != checked_ledger.base_currency
        or checked_spec.base_currency != checked_economics.functional_currency
        or checked_ledger.money_quantum != checked_economics.money_quantum
        or checked_economics.tenant_id != checked_spec.tenant_id
        or checked_economics.backtest_id != checked_spec.backtest_id
        or checked_economics.ledger_id != checked_ledger.ledger_id
    ):
        raise SimulationEconomicsError("economics policies do not match the backtest spec")
    references = tuple(
        item
        for item in checked_spec.dataset_references
        if item.dataset_id == dataset_id and item.role is DatasetRole.TRADEABLE
    )
    if len(references) != 1:
        raise SimulationEconomicsError(
            "dataset is not uniquely bound as tradeable in the backtest spec"
        )
    try:
        handle = repository.open_economics_handle(
            dataset_id,
            expected_tenant_id=expected_tenant_id,
            expected_manifest_digest=references[0].manifest_digest,
        )
    except DatasetIntegrityError as exc:
        raise SimulationEconomicsError(
            "frozen economics dataset is invalid or ineligible"
        ) from exc
    if (
        references[0].instrument_id != handle.instrument_id
        or handle.instrument_id not in checked_spec.instrument_ids
        or handle.schema_version != 2
        or not hmac.compare_digest(
            checked_economics.fx_book_digest, handle.fx_book_digest
        )
    ):
        raise SimulationEconomicsError(
            "dataset is not the schema-v2 tradeable reference bound to the spec"
        )
    instrument_bindings = tuple(
        item
        for item in checked_economics.instrument_bindings
        if item.instrument_id == handle.instrument_id
    )
    if (
        len(instrument_bindings) != 1
        or instrument_bindings[0].dataset_id != handle.dataset_id
        or not hmac.compare_digest(
            instrument_bindings[0].manifest_digest, handle.manifest_digest
        )
        or instrument_bindings[0].quote_currency != handle.quote_currency
    ):
        raise SimulationEconomicsError(
            "instrument economics binding does not match the frozen dataset"
        )
    return FrozenSimulationEconomicsAuthority(
        token=_AUTHORITY_TOKEN,
        repository=repository,
        handle=handle,
        spec=checked_spec,
        spec_digest=spec_digest,
        ledger_policy=checked_ledger,
        economics_policy=checked_economics,
    )


__all__ = [
    "FrozenSimulationEconomicsAuthority",
    "open_simulation_economics_authority",
]
