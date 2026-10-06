from .models import PROFILES, Layout, Widget

TEMPLATES = [
    ("climate", "温湿度与时钟", "大字温湿度 · 本地时间"),
    ("rounded_climate", "圆润温湿度", "柔和圆体 · 清晰双栏"),
    ("room_carousel", "房间温度轮播", "只切换温度区域 · 湿度与时钟固定"),
    ("clock", "简约时钟", "大号圆体 · 日期"),
    ("digital_clock", "数码时钟", "七段数字 · 离线计时"),
    ("power_trend", "功率趋势", "实时功率 · 24 小时历史"),
    ("energy_trend", "用电趋势", "当前读数 · 7 天历史"),
    ("temperature_trend", "温度趋势", "当前温度 · 24 小时历史"),
    ("panel_temperature", "屏幕温度", "屏幕内部传感器 · 本地时钟"),
    ("single_value", "醒目读数", "单实体大字 · 本地时间"),
    ("entities", "双实体信息", "名称与数值 · 双栏"),
    ("status", "设备状态", "开关、门窗、锁、人体感应"),
    ("weather", "天气信息", "天气状态 · 温度 · 湿度"),
    ("countdown", "倒计时", "本地计时 · 目标时间"),
    ("blank", "自由画布", "从空白开始"),
]


def adapt_layout(layout, profile):
    """Keep relative geometry when reusing a user template on another panel."""
    old_w, old_h = layout.size
    result = layout.model_copy(deep=True)
    result.profile = profile
    new_w, new_h = result.size
    sx, sy = new_w / old_w, new_h / old_h
    for widget in result.widgets:
        x, y = int(widget.x * sx), int(widget.y * sy)
        right, bottom = int((widget.x + widget.width) * sx), int((widget.y + widget.height) * sy)
        widget.x, widget.y = x, y
        widget.width, widget.height = max(1, right - x), max(1, bottom - y)
        if widget.kind == "entity_carousel":
            widget.width, widget.height = min(128, widget.width), min(96, widget.height)
        widget.font_size = max(6, min(96, int(widget.font_size * min(sx, sy))))
    return Layout.model_validate(result.model_dump())


def build_template(profile, mode, title, temperature, humidity, entities=None):
    w, h = PROFILES[profile]["width"], PROFILES[profile]["height"]
    states = entities or {}

    def find(device_class=None, domains=None, fallback=None):
        for key, state in states.items():
            if state.get("state") in {"unavailable", "unknown"}:
                continue
            if device_class and state.get("attributes", {}).get("device_class") != device_class:
                continue
            if domains and key.split(".")[0] not in domains:
                continue
            return key
        return fallback

    temperature = find("temperature", fallback=temperature)
    humidity = find("humidity", fallback=humidity)
    widgets = []

    def add(id, kind, x, y, width, height, **kw):
        widgets.append(Widget(id=id, kind=kind, x=x, y=y, width=width, height=height, **kw))

    def text(id, value, x, y, width, size=13, **kw):
        add(id, "text", x, y, width, size + 4, text=value, font_size=size, **kw)

    def footer():
        add("divider", "line", 8, h - 27, w - 16, 1)
        add("clock", "clock", 8, h - 21, 76, 18, font="rounded", font_size=16)
        add("date", "date", w - 106, h - 21, 98, 18, font="sans", font_size=14, align="right")

    if mode == "blank":
        return Layout(profile=profile, name=title)
    text("title", title, 8, 4, w - 16, 14, font_weight=500)
    if mode in {"climate", "rounded_climate", "entities"}:
        family = "rounded" if mode == "rounded_climate" else "inter"
        text(
            "temperature-label",
            "温度",
            8,
            27,
            w // 2 - 12,
            12,
            font="rounded" if family == "rounded" else "sans",
        )
        text(
            "humidity-label",
            "湿度",
            w // 2 + 6,
            27,
            w // 2 - 14,
            12,
            font="rounded" if family == "rounded" else "sans",
        )
        add(
            "temperature",
            "entity",
            8,
            44,
            w // 2 - 12,
            30,
            entity_id=temperature,
            font=family,
            font_size=25,
            font_weight=500,
            unit="°C",
        )
        add(
            "humidity",
            "entity",
            w // 2 + 6,
            44,
            w // 2 - 14,
            30,
            entity_id=humidity,
            font=family,
            font_size=27,
            font_weight=500,
            unit="%",
            decimals=0,
        )
        if mode != "entities":
            footer()
    elif mode == "room_carousel":
        options = [
            k
            for k, state in states.items()
            if state.get("attributes", {}).get("device_class") == "temperature"
            and state.get("state") not in {"unknown", "unavailable"}
        ][:3]
        add(
            "rooms",
            "entity_carousel",
            8,
            32,
            96,
            32,
            carousel_entries=[{"entity_id": k, "label": ""} for k in options or [temperature]],
            font="rounded",
            font_size=20,
            font_weight=500,
        )
        text("humidity-label", "湿度", w // 2 + 6, 30, w // 2 - 14, 10)
        add(
            "humidity",
            "entity",
            w // 2 + 6,
            44,
            w // 2 - 14,
            25,
            entity_id=humidity,
            font="rounded",
            font_size=22,
            font_weight=500,
            decimals=0,
            unit="%",
        )
        footer()
    elif mode == "panel_temperature":
        widgets[0].text = "屏幕温度"
        add(
            "panel-temperature",
            "tag_temperature",
            8,
            31,
            w - 16,
            40,
            font="rounded",
            font_size=32,
            font_weight=500,
            align="center",
        )
        footer()
    elif mode in {"clock", "digital_clock", "countdown"}:
        add(
            "clock",
            "countdown" if mode == "countdown" else "clock",
            8,
            29,
            w - 16,
            h - 53,
            font="digital" if mode == "digital_clock" else "rounded",
            font_size=42 if w == 212 else 48,
            font_weight=400 if mode == "digital_clock" else 500,
            align="center",
        )
        if mode == "countdown":
            text("target-label", "设置目标时间", 8, h - 21, w - 16, 13, align="center")
        else:
            add("date", "date", 8, h - 21, w - 16, 18, font_size=14, align="center")
    elif mode in {"power_trend", "energy_trend", "temperature_trend"}:
        dc = {"power_trend": "power", "energy_trend": "energy", "temperature_trend": "temperature"}[
            mode
        ]
        key = find(dc, fallback=temperature if dc == "temperature" else None)
        widgets[0].text = {"power": "用电功率", "energy": "用电读数", "temperature": "室内温度"}[dc]
        add(
            "trend",
            "chart",
            8,
            26,
            w - 16,
            h - 31,
            entity_id=key,
            font="inter",
            font_size=20,
            font_weight=500,
            history_hours=168 if dc == "energy" else 24,
            chart_style="line",
        )
    elif mode == "single_value":
        add(
            "value",
            "entity",
            8,
            31,
            w - 16,
            43,
            entity_id=find(
                domains={"sensor", "input_number", "number", "counter"}, fallback=temperature
            ),
            font="inter",
            font_size=38,
            font_weight=500,
            align="center",
        )
        footer()
    elif mode == "status":
        options = [
            k
            for k in states
            if k.split(".")[0] in {"binary_sensor", "switch", "lock", "cover"}
            and states[k].get("state") not in {"unknown", "unavailable"}
        ][:4]
        for i in range(4):
            x, y = 8 + (i % 2) * (w // 2), 29 + (i // 2) * 37
            key = options[i] if i < len(options) else None
            label = states.get(key, {}).get("attributes", {}).get("friendly_name", f"设备 {i + 1}")
            text(f"label-{i}", label, x, y, w // 2 - 16, 11)
            add(
                f"state-{i}",
                "entity",
                x,
                y + 15,
                w // 2 - 16,
                20,
                entity_id=key,
                font="rounded",
                font_weight=500,
                font_size=16,
                unit="",
            )
    elif mode == "weather":
        key = find(domains={"weather"})
        add(
            "weather",
            "entity",
            8,
            29,
            w - 16,
            22,
            entity_id=key,
            font="rounded",
            font_size=18,
            unit="",
        )
        add(
            "temperature",
            "entity",
            8,
            57,
            w // 2 - 12,
            31,
            entity_id=key,
            attribute="temperature",
            font="inter",
            font_size=25,
            font_weight=500,
            unit="°C",
        )
        add(
            "humidity",
            "entity",
            w // 2 + 6,
            57,
            w // 2 - 14,
            31,
            entity_id=key,
            attribute="humidity",
            font="inter",
            font_size=25,
            font_weight=500,
            unit="%",
            decimals=0,
        )
    else:
        raise ValueError("模板不存在")
    return Layout(name=title, profile=profile, widgets=widgets)
