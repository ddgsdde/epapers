from datetime import datetime
from pathlib import Path

import httpx
import pytest
from epapers.config import Settings
from epapers.fonts import FONT_CATALOG
from epapers.ha import HomeAssistant
from epapers.models import Layout, Widget
from epapers.render import Renderer
from epapers.templates import TEMPLATES, build_template

ROOT = Path(__file__).resolve().parents[1]


def test_fonts_have_visible_regular_and_bold_weights():
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    for family in FONT_CATALOG:
        item = Widget(
            id="font", kind="text", x=0, y=0, width=210, height=35, font=family["id"], font_size=24
        )
        a = renderer.text_layer(item, "12:34")
        item.font_weight = 700
        b = renderer.text_layer(item, "12:34")
        assert a.tobytes() != b.tobytes(), family["id"]
        assert sum(v == 0 for v in b.tobytes()) > sum(v == 0 for v in a.tobytes())


@pytest.mark.parametrize("profile", ["HINK-E0213A41", "HINK-E0213A55", "OPM021B1"])
def test_all_templates_fit_and_compile(profile):
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    for mode, _, _ in TEMPLATES:
        layout = build_template(profile, mode, "客厅", "sensor.t", "sensor.h")
        static, _ = renderer.render(layout, {})
        assert len(renderer.blob(layout, static)) <= 16384


@pytest.mark.asyncio
async def test_history_uses_ha_rows_and_preserves_unavailable_gaps(monkeypatch):
    now = datetime.fromisoformat("2026-10-06T12:00:00+00:00").timestamp()
    monkeypatch.setattr("epapers.ha.time.time", lambda: now)

    async def noop(_):
        pass

    ha = HomeAssistant(Settings(_env_file=None, ha_url="http://ha", ha_token="test"), noop)
    ha.connected = True
    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            requests.append((url, kwargs["params"]))
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json=[
                    [
                        {
                            "state": "15",
                            "last_changed": "2026-10-06T01:00:00+00:00",
                            "attributes": {"current_temperature": 22},
                        },
                        {"state": "unavailable", "last_changed": "2026-10-06T02:00:00+00:00"},
                        {"state": "nan", "last_changed": "2026-10-06T03:00:00+00:00"},
                        {
                            "state": "30",
                            "last_updated": "2026-10-06T04:00:00+00:00",
                            "attributes": {"current_temperature": 24},
                        },
                    ]
                ],
            )

    monkeypatch.setattr("epapers.ha.httpx.AsyncClient", Client)
    values = await ha.history("sensor.power", 24)
    assert [v for _, v in values] == [15, None, None, 30, 30]
    assert "minimal_response" in requests[0][1]
    await ha.history("sensor.power", 24)
    assert len(requests) == 1
    attribute = await ha.history("climate.room", 24, "current_temperature")
    assert [v for _, v in attribute] == [22, None, None, 24, 24]
    assert "no_attributes" not in requests[-1][1]


def test_chart_constant_values_and_labels_render_in_black_white():
    renderer = Renderer(ROOT / ".local/test-assets", ROOT / "assets/fonts")
    widget = Widget(
        id="trend",
        kind="chart",
        x=0,
        y=0,
        width=212,
        height=104,
        entity_id="sensor.power",
        font="inter",
        font_weight=700,
    )
    layout = Layout(profile="HINK-E0213A41", widgets=[widget])
    states = {"sensor.power": {"state": "24", "attributes": {"unit_of_measurement": "W"}}}
    key = HomeAssistant.history_key("sensor.power")
    a, _ = renderer.render(layout, states, {key: [[1000, 24], [2000, 24]]}, epoch=3000)
    assert a.mode == "1" and set(a.convert("L").tobytes()) == {0, 255}
    widget.chart_labels = False
    b, _ = renderer.render(layout, states, {key: [[1000, 24], [2000, 24]]}, epoch=3000)
    assert a.tobytes() != b.tobytes()
