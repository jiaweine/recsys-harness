"""Fail-closed reverse-proxy trust for client identity and rate limiting."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable

from fastapi import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_LOOPBACK_PROXY_CIDRS = ("127.0.0.0/8", "::1/128")


def parse_trusted_proxy_networks(
    value: str | None,
    *,
    legacy_loopback: bool = False,
) -> tuple[IPNetwork, ...]:
    """Parse explicit proxy CIDRs and fail fast on unsafe configuration typos.

    The historical ``LINGJING_TRUST_PROXY_IP=1`` switch is retained only as a
    loopback-proxy compatibility mode.  Remote/container proxies must be named
    explicitly through ``LINGJING_TRUSTED_PROXY_CIDRS``.
    """

    raw = str(value or "").strip()
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if not tokens and legacy_loopback:
        tokens = list(_LOOPBACK_PROXY_CIDRS)

    networks: list[IPNetwork] = []
    for token in tokens:
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as exc:
            raise RuntimeError(
                f"LINGJING_TRUSTED_PROXY_CIDRS contains invalid network: {token}"
            ) from exc
    return tuple(networks)


def _parse_ip(value: str | None) -> IPAddress | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _is_trusted(address: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def resolve_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    networks: Iterable[IPNetwork],
) -> str:
    """Resolve a rate-limit identity from a trusted proxy chain.

    ``X-Forwarded-For`` is considered only when the immediate TCP peer belongs
    to a configured trusted proxy network.  The chain is then walked from right
    to left, removing trusted hops until the first untrusted client address is
    found.  A malformed chain fails closed to the direct peer rather than using
    attacker-controlled text as an identity.
    """

    peer_text = str(peer or "unknown").strip() or "unknown"
    peer_ip = _parse_ip(peer_text)
    if peer_ip is None:
        return peer_text[:80]

    trusted = tuple(networks)
    peer_key = peer_ip.compressed
    if not trusted or not _is_trusted(peer_ip, trusted):
        return peer_key

    forwarded = str(forwarded_for or "").strip()
    if not forwarded:
        return peer_key

    chain: list[IPAddress] = []
    for token in forwarded.split(","):
        address = _parse_ip(token)
        if address is None:
            return peer_key
        chain.append(address)

    # The direct peer is not part of XFF, but participates in the trust walk.
    chain.append(peer_ip)
    for address in reversed(chain):
        if not _is_trusted(address, trusted):
            return address.compressed

    # An all-trusted chain has no independently attributable client.  Collapsing
    # to the direct peer is stricter than accepting a spoofable leftmost value.
    return peer_key


_LEGACY_LOOPBACK = os.environ.get("LINGJING_TRUST_PROXY_IP", "0") in {
    "1",
    "true",
    "True",
}
TRUSTED_PROXY_NETWORKS = parse_trusted_proxy_networks(
    os.environ.get("LINGJING_TRUSTED_PROXY_CIDRS"),
    legacy_loopback=_LEGACY_LOOPBACK,
)


def client_key(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return resolve_client_ip(
        peer,
        request.headers.get("x-forwarded-for"),
        TRUSTED_PROXY_NETWORKS,
    )[:80]
