"""Contrato de transporte do coletor de observabilidade operacional."""

from __future__ import annotations

import pytest

from scripts.verify_observability_window import _validated_base_url


@pytest.mark.parametrize("url", [
    "http://monitor.example",
    "https://user:secret@monitor.example",
    "https://monitor.example/internal",
    "https://monitor.example?token=secret",
    "https://monitor.example/#fragment",
    "not-a-url",
])
def test_collector_refuses_insecure_or_ambiguous_base_url(url):
    with pytest.raises(ValueError, match="origem HTTPS"):
        _validated_base_url(url)


def test_collector_accepts_https_origin_with_port_and_trailing_slash():
    assert _validated_base_url(" https://monitor.example:8443/ ") == (
        "https://monitor.example:8443"
    )
