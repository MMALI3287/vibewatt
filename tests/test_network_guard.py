import socket

import pytest


def test_socket_creation_is_not_an_outbound_call():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.fileno() >= 0


@pytest.mark.parametrize("address", [("203.0.113.9", 443), ("2001:db8::8", 443)])
def test_external_address_is_blocked_before_connect(address):
    family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError, match="external network blocked") as err:
            sock.connect(address)
    assert "test_external_address_is_blocked_before_connect" in str(err.value)


def test_dns_name_is_rejected_without_lookup():
    with pytest.raises(AssertionError, match="external network blocked"):
        socket.getaddrinfo("example.invalid", 443)


def test_connect_ex_and_udp_are_guarded():
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="external network blocked"):
            sock.connect_ex(("198.51.100.7", 443))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(AssertionError, match="external network blocked"):
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
