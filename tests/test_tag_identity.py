"""Run SDK6's real OTP address callback with simulated chip manufacturing data.

SDK source remains local and is never copied into this repository.
"""

import ctypes
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def identity(tmp_path_factory):
    path = ROOT / ".local/sdk6/DA145xx_SDK/6.0.20.1338"
    source = path / "sdk/app_modules/src/app_default_hnd/app_default_handlers.c"
    if not source.exists():
        pytest.skip("Identity tests need the locally licensed Renesas SDK6")
    text = source.read_text()
    first = text.index("static void create_bd_addr(")
    last = text.index("\n#if (BLE_APP_SEC)", first)
    definitions = "\n".join(re.findall(
        r"^#define OTP_(?:TIMESTAMP|SITE_ID)_ADDR.*$", text, re.MULTILINE,
    ))
    work = tmp_path_factory.mktemp("tag-identity")
    harness = work / "identity.c"
    harness.write_text(
        text[:text.index('#include "rwip_config.h"')]
        + """
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#define GAP_STATIC_ADDR 0xC0
struct bd_addr { uint8_t addr[6]; };
static uint32_t timestamp, tester;
static unsigned reads;
static void hw_otpc_init(void) {}
static void hw_otpc_manual_read_on(int unused) { (void)unused; }
static void hw_otpc_disable(void) {}
""" + definitions + """
static uint32_t GetWord32(uint32_t address) {
    ++reads; return address == OTP_TIMESTAMP_ADDR ? timestamp : tester;
}
""" + text[first:last] + """
void generate(uint32_t time, uint32_t site, uint8_t *out) {
    struct bd_addr value; memset(&value, 0, sizeof(value));
    timestamp = time; tester = site; reads = 0;
    default_app_generate_unique_static_random_addr(&value);
    memcpy(out, value.addr, sizeof(value.addr));
}
unsigned read_count(void) { return reads; }
"""
    )
    lib_path = work / "identity.so"
    subprocess.run([
        "cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
        str(harness), "-o", str(lib_path),
    ], check=True)
    lib = ctypes.CDLL(str(lib_path))
    lib.generate.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]

    def generate(timestamp, site):
        result = ctypes.create_string_buffer(6)
        lib.generate(timestamp, site, result)
        assert lib.read_count() == 2
        return result.raw

    return generate


def test_address_is_stable_across_restarts_and_application_versions(identity):
    expected = identity(0x5F306751, 0x237)
    for _ in range(10):
        assert identity(0x5F306751, 0x237) == expected
    assert expected[5] & 0xC0 == 0xC0
    assert expected != bytes.fromhex("5326e41d5ff5")


def test_same_firmware_on_distinct_chips_has_distinct_addresses(identity):
    first = identity(0x5F306751, 0x237)
    assert identity(0x5F306752, 0x237) != first
    assert identity(0x5F306751, 0x238) != first
    # Verify the full 46-bit factory-identifier input used by this SDK callback.
    addresses = {identity(0x5F300000 + n, 0x237) for n in range(1000)}
    assert len(addresses) == 1000


def test_build_uses_unset_address_and_unique_callback():
    work = ROOT / ".local/tag-unique-address/src/config"
    if not work.exists():
        pytest.skip("Identity configuration test needs the local tag build")
    config = (work / "user_config.h").read_text()
    callback = (work / "user_callback_config.h").read_text()
    assert re.search(r"#define USER_CFG_ADDRESS_MODE\s+APP_CFG_ADDR_STATIC", config)
    assert ".addr = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00}" in config
    assert "default_app_generate_unique_static_random_addr," in callback
    assert "= default_app_generate_static_random_addr," not in callback
