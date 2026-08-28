import ipaddress

import pytest
from starlette.requests import Request

import lingjing_harness.api as api_module
import lingjing_harness.proxy_trust as proxy_trust
from lingjing_harness.proxy_trust import (
    parse_trusted_proxy_networks,
    resolve_client_ip,
)


def _networks(*values: str):
    return tuple(ipaddress.ip_network(value) for value in values)


def _request(peer: str, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345),
            "server": ("testserver", 80),
        }
    )


def test_direct_client_cannot_spoof_x_forwarded_for() -> None:
    trusted = _networks("10.0.0.0/8")

    assert resolve_client_ip("203.0.113.9", "198.51.100.4", trusted) == "203.0.113.9"


def test_trusted_proxy_uses_first_untrusted_address_from_the_right() -> None:
    trusted = _networks("10.0.0.0/8", "192.168.0.0/16")

    # An attacker-supplied leftmost value survives an append-style proxy, but the
    # actual client is the first untrusted hop when the chain is walked backward.
    forwarded = "198.51.100.77, 203.0.113.8, 192.168.10.4"
    assert resolve_client_ip("10.0.0.5", forwarded, trusted) == "203.0.113.8"


def test_trusted_proxy_with_overwritten_header_resolves_client() -> None:
    trusted = _networks("10.0.0.0/8")

    assert resolve_client_ip("10.1.2.3", "203.0.113.44", trusted) == "203.0.113.44"


def test_malformed_forwarded_chain_fails_closed_to_peer() -> None:
    trusted = _networks("10.0.0.0/8")

    assert resolve_client_ip("10.1.2.3", "203.0.113.44, not-an-ip", trusted) == "10.1.2.3"


def test_all_trusted_chain_collapses_to_direct_peer() -> None:
    trusted = _networks("10.0.0.0/8")

    assert resolve_client_ip("10.1.2.3", "10.2.3.4, 10.3.4.5", trusted) == "10.1.2.3"


def test_ipv6_proxy_chain_is_supported() -> None:
    trusted = _networks("fd00::/8")

    assert resolve_client_ip("fd00::10", "2001:db8::8, fd00::20", trusted) == "2001:db8::8"


def test_legacy_proxy_switch_only_enables_loopback_compatibility() -> None:
    networks = parse_trusted_proxy_networks(None, legacy_loopback=True)

    assert any(ipaddress.ip_address("127.0.0.1") in network for network in networks)
    assert any(ipaddress.ip_address("::1") in network for network in networks)
    assert not any(
        network.version == 4 and ipaddress.ip_address("172.18.0.2") in network
        for network in networks
    )


def test_invalid_proxy_cidr_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="invalid network"):
        parse_trusted_proxy_networks("10.0.0.0/8, definitely-not-a-cidr")


def test_api_rate_limit_identity_uses_the_hardened_resolver(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_trust,
        "TRUSTED_PROXY_NETWORKS",
        _networks("10.0.0.0/8"),
    )

    request = _request(
        "10.0.0.5",
        "198.51.100.77, 203.0.113.8, 10.0.0.4",
    )
    assert api_module._client_key(request) == "203.0.113.8"


def test_api_ignores_xff_when_direct_peer_is_not_trusted(monkeypatch) -> None:
    monkeypatch.setattr(
        proxy_trust,
        "TRUSTED_PROXY_NETWORKS",
        _networks("10.0.0.0/8"),
    )

    request = _request("203.0.113.9", "198.51.100.77")
    assert api_module._client_key(request) == "203.0.113.9"
