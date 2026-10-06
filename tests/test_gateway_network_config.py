"""Exercise the actual gateway configuration parser with the local IDF dependencies."""

import ctypes
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Config(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("ssid", ctypes.c_char * 33),
        ("password", ctypes.c_char * 65),
        ("url", ctypes.c_char * 256),
        ("token", ctypes.c_char * 128),
    ]


@pytest.fixture(scope="module")
def parser(tmp_path_factory):
    cjson = ROOT / "firmware/gateway/managed_components/espressif__cjson/cJSON"
    http = ROOT / ".local/esp-idf/components/http_parser"
    if not (cjson / "cJSON.c").exists() or not (http / "http_parser.c").exists():
        pytest.skip("Gateway parser tests need the local ESP-IDF / cJSON build dependencies")
    source = ROOT / "firmware/gateway/main"
    path = tmp_path_factory.mktemp("gateway-network") / "network.so"
    subprocess.run(
        [
            "cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
            "-I", str(source), "-I", str(cjson), "-I", str(http),
            str(source / "network_config.c"), str(cjson / "cJSON.c"),
            str(http / "http_parser.c"), "-lm", "-o", str(path),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(path))
    lib.cJSON_Parse.argtypes = [ctypes.c_char_p]
    lib.cJSON_Parse.restype = ctypes.c_void_p
    lib.cJSON_Delete.argtypes = [ctypes.c_void_p]
    lib.ep_network_config_update.argtypes = [
        ctypes.POINTER(Config), ctypes.c_void_p, ctypes.POINTER(Config),
        ctypes.c_char_p, ctypes.c_size_t,
    ]
    lib.ep_network_config_update.restype = ctypes.c_bool

    def update(data, existing=None):
        previous = existing or Config()
        out = Config()
        error = ctypes.create_string_buffer(192)
        obj = lib.cJSON_Parse(json.dumps(data, ensure_ascii=False).encode())
        try:
            ok = lib.ep_network_config_update(
                ctypes.byref(previous), obj, ctypes.byref(out), error, len(error)
            )
        finally:
            lib.cJSON_Delete(obj)
        return ok, out, error.value.decode()

    return update


def new_config(**changes):
    return {"ssid": "客厅 WiFi", "password": "test-pass-123", "url": "http://10.0.0.10:8099", "token": "test-token-123", **changes}


@pytest.mark.parametrize("url, expected", [
    ("http://10.0.0.10:8099/", "ws://10.0.0.10:8099/ws/gateway"),
    ("10.0.0.10:8099", "ws://10.0.0.10:8099/ws/gateway"),
    ("https://epapers.example.org", "wss://epapers.example.org/ws/gateway"),
    ("ws://10.0.0.10:8099/ws/gateway", "ws://10.0.0.10:8099/ws/gateway"),
    ("https://example.org/epapers/", "wss://example.org/epapers/ws/gateway"),
    ("http://[fd00::1]:8099", "ws://[fd00::1]:8099/ws/gateway"),
])
def test_server_address_becomes_gateway_socket(parser, url, expected):
    ok, result, error = parser(new_config(url=url))
    assert ok, error
    assert result.url.decode() == expected
    assert result.ssid.decode() == "客厅 WiFi"


def test_blank_secrets_preserve_only_for_same_wifi(parser):
    ok, current, _ = parser(new_config())
    assert ok
    ok, result, error = parser({"ssid": "客厅 WiFi", "password": "", "token": "", "url": "10.0.0.20:8099"}, current)
    assert ok, error
    assert result.password == current.password and result.token == current.token
    assert current.url == b"ws://10.0.0.10:8099/ws/gateway"
    ok, _, error = parser({"ssid": "other-network", "password": "", "token": ""}, current)
    assert not ok and "密码" in error


def test_explicit_open_network_clears_old_password(parser):
    _, current, _ = parser(new_config())
    ok, result, error = parser({"ssid": "open-network", "password": "", "open_wifi": True}, current)
    assert ok, error
    assert result.password == b"" and result.token == current.token


@pytest.mark.parametrize("changes", [
    {"ssid": ""}, {"ssid": "中" * 11}, {"ssid": "a" * 33},
    {"password": "short"}, {"password": ""}, {"password": "g" * 64},
    {"token": ""}, {"token": "x" * 128}, {"token": "bad\r\nheader"},
    {"token": "has space"}, {"url": "ftp://10.0.0.1"},
    {"url": "http://127.0.0.1:8099"}, {"url": "http://localhost:8099"},
    {"url": "http://[::1]:8099"}, {"url": "http://user:pass@10.0.0.1:8099"},
    {"url": "http://10.0.0.1:8099/?token=secret"}, {"url": "http://10.0.0.1:0"},
    {"url": "http://10.0.0.1:8099\r\nheader"}, {"open_wifi": "yes"},
])
def test_invalid_configuration_rejected_without_secret_in_error(parser, changes):
    ok, _, error = parser(new_config(**changes))
    assert not ok and error
    assert "test-pass-123" not in error and "test-token-123" not in error


def test_maximum_valid_lengths(parser):
    ok, result, error = parser(new_config(ssid="a" * 32, password="a" * 64, token="t" * 127))
    assert ok, error
    assert len(result.ssid) == 32 and len(result.password) == 64 and len(result.token) == 127
