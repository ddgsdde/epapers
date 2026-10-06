import struct
from pathlib import Path

import pytest
from epapers.models import PROFILES, Layout, Widget, template
from epapers.protocol import Command, Packet, pack_mono, packets_for_blob
from epapers.render import BLOB_HEADER, Renderer
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")


def test_packet_crc_and_lengths():
    packet = Packet(Command.DATA, 0x12345678, 2, b"payload")
    raw = packet.encode()
    assert Packet.decode(raw) == packet
    for i in range(len(raw)):
        bad = bytearray(raw)
        bad[i] ^= 1
        with pytest.raises(ValueError):
            Packet.decode(bytes(bad))
    with pytest.raises(ValueError):
        Packet.decode(raw + b"x")


def test_row_padding_white_and_msb():
    image = Image.new("1", (250, 2), 1)
    image.putpixel((0, 0), 0)
    image.putpixel((249, 1), 0)
    data = pack_mono(image)
    assert len(data) == 64
    assert data[0] == 0x7F and data[31] == 0xFF
    assert data[63] == 0xBF  # rightmost pixel bit 6; six padding bits remain white


def test_fragmentation_minimum_mtu_and_reassembly():
    blob = bytes(range(256)) * 20
    with pytest.raises(ValueError):
        list(packets_for_blob(blob, 42, mtu=23))
    packets = list(packets_for_blob(blob, 42, mtu=27))
    assert all(len(p.encode()) <= 24 for p in packets)
    assert b"".join(p.payload for p in packets if p.command == Command.DATA) == blob
    assert struct.unpack("<II", packets[0].payload)[0] == len(blob)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_panel_dimensions_rotation_and_blob(profile, rotation):
    layout = Layout(profile=profile, rotation=rotation)
    static, preview = renderer.render(layout, {})
    assert static.size == layout.size and preview.size == layout.size
    blob = renderer.blob(layout, static)
    magic, version, turns, w, h, length, count = BLOB_HEADER.unpack_from(blob)
    assert magic == b"EPD1" and version == 1 and turns == rotation // 90
    assert (w, h) == layout.size and length == ((w + 7) // 8) * h and count == 0


def test_clock_preview_changes_without_static_revision_changes():
    layout = template(mode="climate")
    a, preview_a = renderer.render(layout, {}, epoch=1700000000)
    b, preview_b = renderer.render(layout, {}, epoch=1700000060)
    assert renderer.blob(layout, a) == renderer.blob(layout, b)
    assert preview_a.tobytes() != preview_b.tobytes()


def test_negative_unavailable_attribute_and_units():
    widget = Widget(id="t", kind="entity", x=0, y=0, entity_id="sensor.t", unit="°C")
    assert renderer.entity_text(widget, {"sensor.t": {"state": "-12.34"}}) == "-12.3°C"
    assert renderer.entity_text(widget, {"sensor.t": {"state": "unavailable"}}) == "--"
    widget.attribute = "temperature"
    assert (
        renderer.entity_text(
            widget, {"sensor.t": {"state": "ok", "attributes": {"temperature": 0}}}
        )
        == "0.0°C"
    )


def test_geometry_and_dynamic_overlap_are_rejected():
    with pytest.raises(ValueError):
        Layout(widgets=[Widget(id="x", kind="text", x=240, y=0, width=20)])
    with pytest.raises(ValueError):
        Layout(widgets=[Widget(id=str(i), kind="clock", x=0, y=0) for i in range(2)])


def test_fonts_and_qr_bounds():
    for family in ["sans", "serif", "digital"]:
        assert renderer.font(family, 20).getlength("12:34") > 0
    with pytest.raises(ValueError):
        renderer.render(
            Layout(
                widgets=[
                    Widget(
                        id="qr",
                        kind="qrcode",
                        x=0,
                        y=0,
                        width=5,
                        height=5,
                        text="https://example.com",
                    )
                ]
            ),
            {},
        )
