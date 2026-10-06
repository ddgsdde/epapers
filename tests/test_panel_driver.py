import ctypes
import subprocess
from pathlib import Path

import pytest
from epapers.models import Layout, Widget
from epapers.render import Renderer

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "profile,model",
    [("HINK-E0213A41", 1), ("HINK-E0213A55", 2), ("OPM021B1", 3)],
)
def test_panel_ram_is_reseeded_for_fast_refresh_and_large_changes_use_full(
    tmp_path, profile, model
):
    library = tmp_path / "panel.so"
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-fsanitize=undefined",
            "-DEP_PANEL_HOST_TEST",
            "-I",
            str(ROOT / "tests/panel_stub"),
            "-I",
            str(ROOT / "firmware/tag/src"),
            "-I",
            str(ROOT / "firmware/common"),
            str(ROOT / "tests/panel_stub/panel_bridge.c"),
            str(ROOT / "firmware/tag/src/panel.c"),
            str(ROOT / "firmware/common/ep_core.c"),
            "-o",
            str(library),
        ],
        check=True,
    )
    panel = ctypes.CDLL(str(library))
    panel.display.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    panel.start_panel_model(model)
    renderer = Renderer(tmp_path, ROOT / "assets/fonts")

    def draw(widgets, elapsed, force=0, inverted=False):
        layout = Layout(profile=profile, widgets=widgets, inverted=inverted)
        static, _ = renderer.render(layout, {})
        blob = renderer.blob(layout, static)
        return panel.display(blob, len(blob), elapsed, force)

    assert draw([], 0) == 0xF7
    assert panel.full_count() == 1 and panel.last_reason() == 2
    small = [Widget(id="value", kind="text", text="25.0", x=8, y=8, width=70, height=22)]
    assert draw(small, 60000) == 0xC7
    assert panel.partial_count() == 1 and panel.last_reason() == 1
    if model == 1:
        # V26 RAM26 is the inverse of RAM24. Compare every driven phase for
        # either possible auxiliary bit after our controller power cycle.
        v26_rows = [0x00, 0x80, 0x40, 0x00]
        for bw in (0, 1):
            for auxiliary in (0, 1):
                actual = panel.lut_byte((auxiliary * 2 + bw) * 7)
                expected = v26_rows[(1 - bw) * 2 + bw]
                assert actual == expected
        frame_count = sum(
            sum(panel.lut_byte(35 + group * 5 + phase) for phase in range(4))
            * (panel.lut_byte(35 + group * 5 + 4) + 1)
            for group in range(7)
        )
        assert frame_count == 10
        assert panel.vcom_value() == 0x28
    assert draw(small, 120000) == 0
    assert draw(small, 180000, inverted=True) == 0xF7
    assert panel.last_reason() == 6
    assert draw(small, 240000, force=1, inverted=True) == 0xF7
    assert panel.last_reason() == 3
    assert draw(small, 3600000, inverted=True) == 0xF7
    assert panel.last_reason() == 5
    assert panel.full_count() == 4 and panel.partial_count() == 1
