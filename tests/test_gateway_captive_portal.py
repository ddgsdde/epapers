"""Exercise the captive portal packet handling compiled from the gateway C source."""

import ctypes
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    source = ROOT / "firmware/gateway/main"
    path = tmp_path_factory.mktemp("gateway-portal") / "portal.so"
    subprocess.run([
        "cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
        "-I", str(source), str(source / "captive_portal.c"), "-o", str(path),
    ], check=True)
    lib = ctypes.CDLL(str(path))
    lib.ep_portal_is_local_host.argtypes = [ctypes.c_char_p]
    lib.ep_portal_is_local_host.restype = ctypes.c_bool
    lib.ep_portal_is_root.argtypes = [ctypes.c_char_p]
    lib.ep_portal_is_root.restype = ctypes.c_bool
    lib.ep_portal_dns_reply.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    lib.ep_portal_dns_reply.restype = ctypes.c_size_t
    return lib


def query(host="captive.apple.com", kind=1, flags=0x100, additional=b""):
    name = b"".join(bytes([len(label)]) + label.encode() for label in host.split(".")) + b"\0"
    return (struct.pack("!6H", 0x1234, flags, 1, 0, 0, bool(additional))
            + name + struct.pack("!HH", kind, 1) + additional)


def answer(portal, request, capacity=512):
    buffer = ctypes.create_string_buffer(max(len(request), capacity))
    buffer.raw = request + b"\0" * (len(buffer) - len(request))
    count = portal.ep_portal_dns_reply(buffer, len(request), capacity)
    return buffer.raw[:count]


@pytest.mark.parametrize("host", [
    "captive.apple.com", "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
    "www.msftconnecttest.com", "connect.rom.miui.com", "connectivitycheck.platform.hicloud.com",
])
def test_phone_detection_dns_points_to_hotspot(portal, host):
    request = query(host)
    response = answer(portal, request)
    assert response[:12] == struct.pack("!6H", 0x1234, 0x8180, 1, 1, 0, 0)
    assert response[12:len(request)] == request[12:]
    assert response[-16:] == bytes.fromhex("c00c00010001000000000004c0a80401")


def test_edns_additional_section_is_removed(portal):
    request = query(additional=bytes.fromhex("0000291000000000000000"))
    response = answer(portal, request)
    assert response[10:12] == b"\0\0"
    assert len(response) == len(query()) + 16


@pytest.mark.parametrize("kind", [28, 65])
def test_ipv6_and_https_records_get_empty_answer(portal, kind):
    request = query(kind=kind)
    response = answer(portal, request)
    assert len(response) == len(request)
    assert response[6:8] == b"\0\0"


@pytest.mark.parametrize("packet", [
    b"", b"\0" * 12, query()[:-1], query(flags=0x8100), query(flags=0x900),
    bytes.fromhex("123401000001000000000000c00c00010001"),
    bytes.fromhex("12340100000200000000000001610000010001"),
    bytes.fromhex("123401000001000000000000036162630000010002"),
])
def test_malformed_questions_are_ignored(portal, packet):
    assert answer(portal, packet) == b""


def test_reply_never_overflows_capacity(portal):
    request = query()
    assert answer(portal, request, len(request) + 15) == b""
    assert len(answer(portal, request, len(request) + 16)) == len(request) + 16
    assert answer(portal, request, len(request) - 1) == b""


@pytest.mark.parametrize("host, local", [
    (b"192.168.4.1", True), (b"192.168.4.1:80", True),
    (b"captive.apple.com", False), (b"192.168.4.1.evil.test", False),
    (b"192.168.4.1:443", False), (b"", False), (None, False),
])
def test_detection_hosts_redirect_to_canonical_address(portal, host, local):
    assert portal.ep_portal_is_local_host(host) is local


@pytest.mark.parametrize("uri, root", [
    (b"/", True), (b"/?from=wifi", True), (b"/generate_204", False),
    (b"/hotspot-detect.html", False), (b"/connecttest.txt", False),
    (b"/canonical.html", False), (b"/fwlink", False), (b"", False), (None, False),
])
def test_probe_paths_redirect_and_root_does_not_loop(portal, uri, root):
    assert portal.ep_portal_is_root(uri) is root
