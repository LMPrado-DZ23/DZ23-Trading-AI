from __future__ import annotations

from tradingagents.platform import llm_provenance


def test_application_build_digest_changes_when_a_critical_sdk_version_changes(
    monkeypatch,
) -> None:
    llm_provenance.application_build_digest.cache_clear()
    baseline = llm_provenance.application_build_digest()
    real_version = llm_provenance.version

    def changed_version(distribution: str) -> str:
        if distribution == "openai":
            return "999.0.0-adversarial-upgrade"
        return real_version(distribution)

    monkeypatch.setattr(llm_provenance, "version", changed_version)
    llm_provenance.application_build_digest.cache_clear()
    changed = llm_provenance.application_build_digest()
    assert changed != baseline
    llm_provenance.application_build_digest.cache_clear()


def test_optional_adapter_presence_is_explicit_in_environment_manifest() -> None:
    manifest = llm_provenance.dependency_environment_manifest()
    assert set(llm_provenance.DECISION_CRITICAL_DISTRIBUTIONS) <= manifest.keys()
    assert all(value for value in manifest.values())
