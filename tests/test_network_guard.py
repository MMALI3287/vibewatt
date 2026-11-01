import socket

import pytest
from conftest import _ensure_local_network_address, _guard_resolver


def test_socket_creation_is_not_an_outbound_call():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.fileno() >= 0


@pytest.mark.parametrize("address", [("203.0.113.9", 443), ("2001:db8::8", 443)])
def test_external_address_is_blocked_before_connect(address):
    family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    with (
        socket.socket(family, socket.SOCK_STREAM) as sock,
        pytest.raises(AssertionError, match="external network blocked") as err,
    ):
        sock.connect(address)
    assert "test_external_address_is_blocked_before_connect" in str(err.value)


@pytest.mark.parametrize(
    ("resolver_name", "args"),
    [
        ("getaddrinfo", ("example.invalid", 443)),
        ("gethostbyname", ("example.invalid",)),
        ("gethostbyname_ex", ("example.invalid",)),
        ("gethostbyaddr", ("203.0.113.9",)),
    ],
)
def test_dns_entry_points_are_rejected_without_lookup(resolver_name, args):
    with pytest.raises(AssertionError, match="external network blocked"):
        getattr(socket, resolver_name)(*args)


@pytest.mark.parametrize(
    ("args", "result"),
    [
        (("example.invalid", 443), []),
        (("example.invalid",), "203.0.113.9"),
        (("example.invalid",), ("example.invalid", [], ["203.0.113.9"])),
        (("203.0.113.9",), ("example.invalid", [], ["203.0.113.9"])),
    ],
)
def test_resolver_guard_rejects_before_underlying_stub(args, result):
    calls = []

    def resolver(*resolver_args):
        calls.append(resolver_args)
        return result

    guarded = _guard_resolver(resolver, "tests/test_network_guard.py::resolver")
    with pytest.raises(AssertionError, match="external network blocked"):
        guarded(*args)
    assert calls == []


def test_connect_ex_and_udp_are_guarded():
    with (
        socket.socket() as sock,
        pytest.raises(AssertionError, match="external network blocked"),
    ):
        sock.connect_ex(("198.51.100.7", 443))
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(AssertionError, match="external network blocked"),
    ):
        sock.sendto(b"hello", ("198.51.100.7", 53))


def test_ipv4_loopback_connection_works():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(listener.getsockname())
            accepted, _ = listener.accept()
            accepted.close()


def test_ipv6_loopback_resolution_is_permitted():
    # No actual network request: local numeric address lookup only.
    assert socket.getaddrinfo("::1", 0, socket.AF_INET6)


@pytest.mark.parametrize(
    ("args", "result"),
    [
        (("127.0.0.1",), "127.0.0.1"),
        (("localhost",), ("localhost", [], ["127.0.0.1"])),
        (("127.0.0.1",), ("localhost", [], ["127.0.0.1"])),
    ],
)
def test_resolver_guard_allows_loopback_and_calls_underlying_stub(args, result):
    calls = []

    def resolver(*resolver_args):
        calls.append(resolver_args)
        return result

    guarded = _guard_resolver(resolver, "tests/test_network_guard.py::resolver")
    assert guarded(*args) == result
    assert calls == [args]


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("getaddrinfo", ("example.invalid", 443)),
        ("gethostbyname", ("example.invalid",)),
        ("gethostbyname_ex", ("example.invalid",)),
        ("gethostbyaddr", ("203.0.113.9",)),
    ],
)
def test_missing_af_unix_rejects_dns_before_resolver_stub(monkeypatch, name, args):
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    called = []

    def stub(*values):
        called.append(values)
        return "should not be reached"

    guarded = _guard_resolver(stub, f"network-guard/{name}")
    with pytest.raises(AssertionError, match="external network blocked"):
        guarded(*args)
    assert called == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_missing_af_unix_keeps_local_resolver_stub(monkeypatch, host):
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    called = []
    result = object()

    def stub(value):
        called.append(value)
        return result

    guarded = _guard_resolver(stub, "network-guard/local")
    assert guarded(host) is result
    assert called == [host]


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable")
def test_unix_family_is_still_permitted():
    _ensure_local_network_address("/tmp/synthetic-test.sock", "unix", socket.AF_UNIX)
