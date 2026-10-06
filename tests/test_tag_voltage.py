"""Exercise the real tag status encoder/core with a controllable ADC input."""

import ctypes
import struct
import subprocess
from pathlib import Path

import pytest
from epapers.protocol import Command, Packet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def tag(tmp_path_factory):
    source = (ROOT / "firmware/tag/src/user_peripheral.c").read_text()
    first = source.index("static void ack_encode(")
    last = source.index("static void ack_set(", first)
    work = tmp_path_factory.mktemp("tag-voltage")
    harness = work / "voltage.c"
    harness.write_text("""
#include "panel.h"
#include <string.h>
#define ADC_INPUT_MODE_SINGLE_ENDED 0
static ep_core core;
volatile panel_diagnostic panel_diag;
static uint16_t supply_mv, raw_sample;
static uint64_t supply_sample_ms;
static uint8_t ack_data[80], ack_len;
static unsigned reads, calibrations;
static void adc_offset_calibrate(int mode) { (void)mode; ++calibrations; }
static unsigned adc_get_vbat_sample(bool mode) { (void)mode; ++reads; return raw_sample; }
""" + source[first:last] + """
void reset(void) {
    ep_init(&core, NULL, NULL); panel_diag=(panel_diagnostic){0};
    panel_diag.temperature_centi=INT16_MIN;
    supply_mv=65535; supply_sample_ms=0; reads=calibrations=0;
}
void set_adc(uint16_t sample) { raw_sample=sample; }
void tick(uint32_t ms) { ep_clock_tick(&core.clock, ms); }
unsigned read_count(void) { return reads; }
unsigned calibration_count(void) { return calibrations; }
unsigned respond(const uint8_t *packet, unsigned size, uint8_t *out) {
    ack_encode(ep_receive(&core, packet, size));
    memcpy(out, ack_data, ack_len); return ack_len;
}
""")
    library = work / "voltage.so"
    subprocess.run([
        "cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "firmware/common"), "-I", str(ROOT / "firmware/tag/src"),
        str(harness), str(ROOT / "firmware/common/ep_core.c"), "-o", str(library),
    ], check=True)
    lib = ctypes.CDLL(str(library))
    lib.respond.argtypes = [ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p]
    lib.set_adc.argtypes = [ctypes.c_uint16]
    lib.tick.argtypes = [ctypes.c_uint32]
    return lib


def response(tag, command, transaction, payload=b"", corrupt=False):
    packet = Packet(command, transaction, 0, payload).encode()
    if corrupt:
        packet = packet[:-1] + bytes([packet[-1] ^ 1])
    out = ctypes.create_string_buffer(80)
    size = tag.respond(packet, len(packet), out)
    ack = Packet.decode(out.raw[:size])
    voltage = struct.unpack_from("<H", ack.payload, 20)[0]
    age = struct.unpack_from("<H", ack.payload, 46)[0]
    return ack.payload[0], voltage, age


def test_status_samples_new_voltage_inside_cache_without_refreshing_panel(tag):
    tag.reset()
    tag.set_adc(1870)
    initial = response(tag, Command.INFO, 1)
    assert initial[0] == 0 and initial[2] == 0
    assert tag.read_count() == 8 and tag.calibration_count() == 1
    tag.tick(30000)
    tag.set_adc(2130)
    fresh = response(tag, Command.STATUS, 2)
    assert fresh[0] == 0 and fresh[1] > initial[1] and fresh[2] == 0
    assert tag.read_count() == 16 and tag.calibration_count() == 2
    # A second explicit read is fresh even one second after the first.
    tag.tick(1000)
    tag.set_adc(2000)
    again = response(tag, Command.STATUS, 3)
    assert again[0] == 0 and initial[1] < again[1] < fresh[1] and again[2] == 0
    assert tag.read_count() == 24 and tag.calibration_count() == 3
    # No display callback or installed image is needed for a voltage read.


def test_transfer_acks_cache_voltage_and_invalid_status_does_not_force_adc(tag):
    tag.reset()
    tag.set_adc(1870)
    initial = response(tag, Command.INFO, 1)
    tag.tick(599000)
    tag.set_adc(2130)
    cached = response(tag, Command.BEGIN, 2, struct.pack("<II", 16, 0))
    assert cached == (0, initial[1], 599) and tag.read_count() == 8
    invalid = response(tag, Command.STATUS, 3, corrupt=True)
    assert invalid == (2, initial[1], 599) and tag.read_count() == 8
    malformed = response(tag, Command.STATUS, 4, b"unexpected")
    assert malformed == (1, initial[1], 599) and tag.read_count() == 8
    tag.tick(1000)
    expired = response(tag, Command.INFO, 5)
    assert expired[0] == 0 and expired[1] > initial[1] and expired[2] == 0
    assert tag.read_count() == 16 and tag.calibration_count() == 2
