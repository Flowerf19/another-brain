"""TASK-068: the locked loopback HTTP transport-security policy.

Exercises the real SDK ``TransportSecurityMiddleware`` with the exact
settings :func:`another_brain.mcp.server.transport_security` pins to the
bound authority — no wildcard host, no wildcard port, no ``localhost``
name. Host/Origin rejection happens before any tool dispatch, so the
middleware boundary is where the matrix belongs. Bind-address rejection
(wildcard/hostname/LAN) is config/CLI-level and already covered by
test_config.py / test_cli.py.
"""
from __future__ import annotations

from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from another_brain.mcp.server import transport_security


def _request(host: str | None, origin: str | None = None, content_type: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))
    return Request({"type": "http", "method": "POST", "headers": headers})


def _middleware(host: str = "127.0.0.1", port: int = 1905) -> TransportSecurityMiddleware:
    return TransportSecurityMiddleware(transport_security(host, port))


# -- the settings themselves pin the policy -------------------------------------


def test_settings_are_pinned_to_the_exact_bound_authority():
    settings = transport_security("127.0.0.1", 1905)
    assert settings.enable_dns_rebinding_protection is True
    # Exact authority only — the SDK's loopback default would also accept
    # the localhost *name* and *any* port; both are rejected here.
    assert settings.allowed_hosts == ["127.0.0.1:1905"]
    assert settings.allowed_origins == ["http://127.0.0.1:1905"]
    assert not any(":*" in h or "localhost" in h for h in settings.allowed_hosts)


def test_ipv6_authority_uses_the_bracketed_host_header_form():
    settings = transport_security("::1", 1905)
    assert settings.allowed_hosts == ["[::1]:1905"]
    assert settings.allowed_origins == ["http://[::1]:1905"]


# -- Host validation --------------------------------------------------------------


async def test_correct_host_passes():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", content_type="application/json"), is_post=True
    )
    assert verdict is None


async def test_localhost_name_is_rejected_even_on_the_right_port():
    # A name is what a DNS rebinding attack controls; the policy is numeric-only.
    verdict = await _middleware().validate_request(
        _request("localhost:1905", content_type="application/json"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 421


async def test_evil_host_is_rejected():
    verdict = await _middleware().validate_request(
        _request("evil.com", content_type="application/json"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 421


async def test_right_host_on_the_wrong_port_is_rejected():
    # Another local service's Host header must not pass this server's allowlist.
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:9999", content_type="application/json"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 421


async def test_missing_host_is_rejected():
    verdict = await _middleware().validate_request(
        _request(None, content_type="application/json"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 421


# -- Origin validation ------------------------------------------------------------


async def test_hostile_origin_is_rejected():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", origin="http://evil.com",
                 content_type="application/json"),
        is_post=True,
    )
    assert verdict is not None and verdict.status_code == 403


async def test_localhost_origin_is_rejected():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", origin="http://localhost:1905",
                 content_type="application/json"),
        is_post=True,
    )
    assert verdict is not None and verdict.status_code == 403


async def test_correct_origin_passes():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", origin="http://127.0.0.1:1905",
                 content_type="application/json"),
        is_post=True,
    )
    assert verdict is None


async def test_absent_origin_passes_for_same_origin_clients():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", content_type="application/json"), is_post=True
    )
    assert verdict is None


# -- Content-Type and IPv6 ---------------------------------------------------------


async def test_post_without_json_content_type_is_rejected():
    verdict = await _middleware().validate_request(
        _request("127.0.0.1:1905", content_type="text/plain"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 400


async def test_get_needs_no_content_type_but_still_checks_host():
    middleware = _middleware()
    ok = await middleware.validate_request(_request("127.0.0.1:1905"), is_post=False)
    assert ok is None
    bad = await middleware.validate_request(_request("evil.com"), is_post=False)
    assert bad is not None and bad.status_code == 421


async def test_ipv6_bracketed_host_and_origin_pass():
    verdict = await _middleware("::1").validate_request(
        _request("[::1]:1905", origin="http://[::1]:1905",
                 content_type="application/json"),
        is_post=True,
    )
    assert verdict is None


async def test_ipv6_unbracketed_host_is_rejected():
    verdict = await _middleware("::1").validate_request(
        _request("::1:1905", content_type="application/json"), is_post=True
    )
    assert verdict is not None and verdict.status_code == 421
