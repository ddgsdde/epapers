import ctypes
import subprocess
from pathlib import Path

import pytest
from epapers.fonts import FONT_CATALOG
from epapers.models import DisplayProgram, Layout, Widget, template
from epapers.protocol import Command, Packet, pack_mono, packets_for_blob
from epapers.render import Renderer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    path = tmp_path_factory.mktemp("core") / "core.so"
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-fsanitize=undefined",
            "-I",
            str(ROOT / "firmware/common"),
            str(ROOT / "firmware/common/ep_core.c"),
            str(ROOT / "tests/core_bridge.c"),
            "-o",
            str(path),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(path))
    lib.receive.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.compose.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p]
    lib.sync_clock.argtypes = [ctypes.c_uint64, ctypes.c_int32]
    lib.window.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint8,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    lib.record.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint8,
        ctypes.c_int,
    ]
    lib.epoch.restype = ctypes.c_uint64
    return lib


@pytest.mark.parametrize("temperature", [None, -2500, -2355, 0, 2365, 8500])
def test_local_temperature_widget_matches_preview(core, temperature):
    core.reset()
    core.set_temperature(-32768 if temperature is None else temperature)
    layout = Layout(
        profile="HINK-E0213A41",
        widgets=[
            Widget(
                id="temp",
                kind="tag_temperature",
                x=4,
                y=5,
                width=200,
                height=34,
                font="rounded",
                font_size=24,
            )
        ],
    )
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    static, preview = renderer.render(layout, {}, telemetry={"temperature_centi": temperature})
    blob = renderer.blob(layout, static)
    out = (ctypes.c_uint8 * 4000)()
    length = core.compose(blob, len(blob), out)
    assert bytes(out[:length]) == pack_mono(preview)


def test_offline_carousel_and_malformed_bundle(core):
    core.reset()
    core.sync_clock(1750000000, 28800)
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    pages = [
        Layout(
            profile="HINK-E0213A41",
            widgets=[Widget(id="label", kind="text", x=4, y=4, width=200, height=30, text=text)],
        )
        for text in ["客厅", "功率"]
    ]
    images, blobs = [], []
    for page in pages:
        static, image = renderer.render(page, {})
        images.append(pack_mono(image))
        blobs.append(renderer.blob(page, static))
    bundle = renderer.program_blob(
        DisplayProgram(pages=pages, enabled=True, interval_minutes=2, started_at=1750000000), blobs
    )
    out = (ctypes.c_uint8 * 4000)()
    for seconds, selected in [(0, 0), (119, 0), (1, 1), (120, 0)]:
        core.tick(seconds * 1000)
        length = core.compose(bundle, len(bundle), out)
        assert bytes(out[:length]) == images[selected]
    broken = bytearray(bundle)
    broken[16:18] = b"\xff\xff"
    assert core.compose(bytes(broken), len(broken), out) == 0
    assert core.compose(bundle[:-1], len(bundle) - 1, out) == 0


@pytest.mark.parametrize("profile", ["OPM021B1", "HINK-E0213A41", "HINK-E0213A55"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("inverted", [False, True])
def test_component_carousel_offline_pixels_and_small_refresh(core, profile, rotation, inverted):
    core.reset()
    # An even minute, away from the hourly full refresh boundary.
    epoch = 1750000080
    core.sync_clock(epoch, 28800)
    layout = Layout(
        profile=profile,
        rotation=rotation,
        inverted=inverted,
        widgets=[
            Widget(
                id="rooms",
                kind="entity_carousel",
                x=0,
                y=32,
                width=96,
                height=32,
                font="rounded",
                font_size=20,
                font_weight=700,
                align="center",
                carousel_entries=[
                    {"entity_id": "sensor.living", "label": "客厅温度"},
                    {"entity_id": "sensor.bedroom", "label": "次卧温度"},
                ],
            ),
            Widget(id="fixed", kind="text", x=0, y=0, width=96, height=20, text="固定区域"),
        ],
    )
    states = {
        "sensor.living": {"state": "23.6", "attributes": {"unit_of_measurement": "°C"}},
        "sensor.bedroom": {"state": "26.1", "attributes": {"unit_of_measurement": "°C"}},
    }
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    static, _ = renderer.render(layout, states, epoch=epoch)
    blob = renderer.blob(layout, static, states)
    out, box = (ctypes.c_uint8 * 4000)(), (ctypes.c_uint16 * 4)()
    frames = []
    for seconds in [0, 60, 60]:
        core.tick(seconds * 1000)
        length = core.compose(blob, len(blob), out)
        frame = bytes(out[:length])
        _, preview = renderer.render(layout, states, epoch=core.epoch())
        assert frame == pack_mono(preview)
        if frames:
            assert core.window(frame, *layout.size, rotation // 90, 0, box) == 1
            previous = frames[-1]
            stride = (layout.size[0] + 7) // 8
            assert previous[: 32 * stride] == frame[: 32 * stride]
            assert previous[64 * stride :] == frame[64 * stride :]
        core.record(frame, *layout.size, rotation // 90, not frames)
        frames.append(frame)
    assert frames[0] != frames[1] and frames[0] == frames[2]
    # End-of-hour policy still clears residual images even if the canvas is unchanged.
    core.tick(3600000)
    assert core.window(frames[-1], *layout.size, rotation // 90, 0, box) == 2


def test_component_carousel_interval_and_malformed_regions(core):
    core.reset()
    epoch = 1750000080
    core.sync_clock(epoch, 0)
    layout = Layout(
        profile="HINK-E0213A41",
        widgets=[
            Widget(
                id="cycle",
                kind="entity_carousel",
                x=0,
                y=32,
                width=96,
                height=32,
                carousel_minutes=2,
                carousel_entries=[{"entity_id": "sensor.a"}, {"entity_id": "sensor.b"}],
            )
        ],
    )
    states = {
        "sensor.a": {"state": "on", "attributes": {"friendly_name": "灯光"}},
        "sensor.b": {"state": "unavailable", "attributes": {"friendly_name": "温度"}},
    }
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    static, _ = renderer.render(layout, states)
    blob = renderer.blob(layout, static, states)
    out = (ctypes.c_uint8 * 4000)()
    frames = []
    for seconds in [0, 119, 1, 120]:
        core.tick(seconds * 1000)
        length = core.compose(blob, len(blob), out)
        frames.append(bytes(out[:length]))
    assert frames[0] == frames[1] == frames[3] and frames[0] != frames[2]
    region = 16 + len(pack_mono(static))
    for offset, value in [(10, 0), (10, 1441), (20, 0), (20, 9), (6, 129)]:
        damaged = bytearray(blob)
        damaged[region + offset : region + offset + 2] = value.to_bytes(2, "little")
        assert core.compose(bytes(damaged), len(damaged), out) == 0
    assert core.compose(blob[:-1], len(blob) - 1, out) == 0


@pytest.mark.parametrize("rotation", [0, 1, 2, 3])
def test_partial_window_noop_hourly_and_manual_full(core, rotation):
    core.reset()
    # 01:59 local: a minute later must trigger the full refresh at 02:00.
    core.sync_clock(1750003140, 0)
    width, height = (212, 104) if rotation % 2 == 0 else (104, 212)
    pixels = bytearray(b"\xff" * (((width + 7) // 8) * height))
    box = (ctypes.c_uint16 * 4)()
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 2
    core.record(bytes(pixels), width, height, rotation, 1)
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 0
    pixels[2 * ((width + 7) // 8) + 1] = 0
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 1
    assert box[0] == box[1] and box[3] - box[2] <= 31
    core.record(bytes(pixels), width, height, rotation, 0)
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 0
    # Sync right before a UTC hour boundary, preserving elapsed monotonic time.
    core.sync_clock(1750003199, 0)
    core.record(bytes(pixels), width, height, rotation, 1)
    core.tick(1000)
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 2
    core.record(bytes(pixels), width, height, rotation, 1)
    assert core.window(bytes(pixels), width, height, rotation, 1, box) == 2
    assert core.window(b"\0" * len(pixels), width, height, rotation, 0, box) == 2
    core.tick(3600000)
    assert core.window(bytes(pixels), width, height, rotation, 0, box) == 2


@pytest.mark.parametrize("profile", ["OPM021B1", "HINK-E0213A41", "HINK-E0213A55"])
@pytest.mark.parametrize("inverted", [False, True])
def test_python_c_pixel_identical(core, profile, inverted):
    core.reset()
    assert core.sync_clock(1750000000, 28800)
    layout = template(profile=profile)
    layout.inverted = inverted
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    static, preview = renderer.render(layout, {}, epoch=1750000000)
    blob = renderer.blob(layout, static)
    out = (ctypes.c_uint8 * 4000)()
    length = core.compose(blob, len(blob), out)
    assert length > 0 and bytes(out[:length]) == pack_mono(preview)


def test_24h_timer_and_atomic_crc_rejection(core):
    core.reset()
    assert core.needs_sync()
    assert core.sync_clock(1750000000, 28800)
    for _ in range(86400):
        core.tick(1000)
    assert core.epoch() == 1750086400 and core.needs_sync()
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    layout = Layout()
    static, _ = renderer.render(layout, {})
    blob = renderer.blob(layout, static)
    packets = list(packets_for_blob(blob, 123))
    for p in packets:
        raw = p.encode()
        assert core.receive(raw, len(raw)) == 0
        assert core.receive(raw, len(raw)) == 0  # idempotent ACK retransmission
    core.active_length.restype = ctypes.c_size_t
    assert core.active_length() == len(blob)
    for p in packets[:-1]:
        p.transaction = 124
        raw = p.encode()
        assert core.receive(raw, len(raw)) == 0
    # A CRC-valid packet carrying a bad blob never becomes active.
    broken = bytearray(packets[-1].encode())
    broken[4] ^= 1
    assert core.receive(bytes(broken), len(broken)) != 0
    assert core.active_length() == len(blob)


def test_out_of_order_and_corruption(core):
    core.reset()
    p = Packet(Command.DATA, 1, 9, b"bad").encode()
    assert core.receive(p, len(p)) != 0
    for i in range(len(p)):
        damaged = bytearray(p)
        damaged[i] ^= 0x80
        assert core.receive(bytes(damaged), len(damaged)) != 0


@pytest.mark.parametrize("family", [font["id"] for font in FONT_CATALOG])
@pytest.mark.parametrize("size", [12, 24])
@pytest.mark.parametrize("weight", [400, 700])
def test_all_font_atlases_match_mcu_pixels(core, family, size, weight):
    """The e-paper clock and Chinese date must match the host preview exactly."""
    core.reset()
    core.sync_clock(1750000000, 28800)
    widgets = [
        Widget(
            id=kind,
            kind=kind,
            x=4,
            y=y,
            width=204,
            height=30,
            font=family,
            font_size=size,
            font_weight=weight,
            letter_spacing=2,
        )
        for kind, y in [("clock", 4), ("date", 42)]
    ]
    layout = Layout(profile="HINK-E0213A41", widgets=widgets)
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    static, preview = renderer.render(layout, {}, epoch=1750000000)
    blob = renderer.blob(layout, static)
    out = (ctypes.c_uint8 * 4000)()
    length = core.compose(blob, len(blob), out)
    assert bytes(out[:length]) == pack_mono(preview)
