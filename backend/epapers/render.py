import io
import math
import struct
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .fonts import FONT_CATALOG
from .ha import HomeAssistant
from .models import Layout
from .protocol import pack_mono

BLOB_HEADER = struct.Struct("<4sBBHHIH")
REGION_HEADER = struct.Struct("<BBHHHHHqH")
GLYPH_HEADER = struct.Struct("<IHHHH")
DYNAMIC = {"clock": 1, "date": 2, "countdown": 3, "tag_temperature": 4, "entity_carousel": 5}


class Renderer:
    def __init__(self, assets: Path, fonts: Path):
        self.assets, self.fonts = assets, fonts
        self.font = lru_cache(maxsize=24)(self.font)

    def font(self, family, size, weight=400):
        names = {f["id"]: f["file"] for f in FONT_CATALOG}
        name = names.get(family, family)
        if family == "wenkai" and weight >= 500:
            name = "LXGWWenKai-Medium.ttf"
        if family == "condensed" and weight >= 700:
            name = "BarlowCondensed-Bold.ttf"
        local = self.fonts / Path(name).name
        paths = [
            local,
            self.fonts / "NotoSansSC.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for path in paths:
            if not path.is_file():
                continue
            try:
                font = ImageFont.truetype(str(path), size)
                try:
                    axes = font.get_variation_axes()
                    values = [
                        min(
                            a["maximum"],
                            max(a["minimum"], weight if a["name"] == b"Weight" else a["default"]),
                        )
                        for a in axes
                    ]
                    font.set_variation_by_axes(values)
                    variable = True
                except OSError:
                    variable = False
                font.ep_family = family
                font.ep_weight = weight
                # Families with native weights use them; single-weight families use a half-pixel outline before downsampling.
                font.ep_synthetic = bool(
                    not variable
                    and weight >= 700
                    and family in {"digital", "rounded", "soft", "wenkai"}
                )
                return font
            except OSError:
                continue
        raise ValueError("没有可用字体，请运行 scripts/download_fonts.py")

    def entity_text(self, widget, states):
        state = states.get(widget.entity_id or "")
        if not state or state.get("state") in {"unknown", "unavailable"}:
            return "--"
        value = (
            state.get("attributes", {}).get(widget.attribute)
            if widget.attribute
            else state.get("state")
        )
        if value is None:
            return "--"
        try:
            text = f"{float(value):.{widget.decimals}f}" if math.isfinite(float(value)) else "--"
        except (ValueError, TypeError):
            text = str(value)
            if widget.state_labels:
                text = {
                    "on": "开启",
                    "off": "关闭",
                    "open": "打开",
                    "closed": "关闭",
                    "home": "在家",
                    "not_home": "离家",
                    "locked": "已锁定",
                    "unlocked": "未锁定",
                    "playing": "播放中",
                    "paused": "已暂停",
                    "idle": "空闲",
                    "heat": "制热",
                    "cool": "制冷",
                    "auto": "自动",
                }.get(text, text)
        unit = (
            widget.unit
            if widget.unit is not None
            else state.get("attributes", {}).get("unit_of_measurement", "")
        )
        return f"{text}{unit}" if unit and text != "--" else text

    def dynamic_text(self, widget, local, telemetry=None):
        if widget.kind == "tag_temperature":
            value = (telemetry or {}).get("temperature_centi")
            if value is None:
                return "--.-°C"
            tenths = (abs(value) + 5) // 10
            return f"{'-' if value < 0 else ''}{tenths // 10}.{tenths % 10}°C"
        if widget.kind == "date":
            return local.strftime("%m月%d日")
        if widget.kind == "countdown":
            seconds = max(0, (widget.deadline or 0) - int(local.timestamp()))
            return f"{min(99, seconds // 3600):02d}:{(seconds // 60) % 60:02d}"
        return local.strftime("%H:%M")

    @staticmethod
    def monochrome(image, scale=4):
        # Average coverage without ringing or dithering. Native small text keeps hinting.
        if scale != 1:
            image = image.resize(
                (image.width // scale, image.height // scale), Image.Resampling.BOX
            )
        threshold = 144 if scale == 1 else 128
        return image.point(lambda v: 255 if v >= threshold else 0)

    def text_bitmap(self, font, size, text, spacing=0):
        family = getattr(font, "ep_family", "sans")
        synthetic = getattr(font, "ep_synthetic", False)
        # At small sizes, native hinting preserves one-pixel Chinese strokes.
        scale = 1 if size <= 16 and not synthetic else 4
        face = self.font(family, size * scale, getattr(font, "ep_weight", 400))
        # A full device-pixel outline closes counters; use 1/4 px on small text.
        stroke = (1 if size <= 16 else 2) if synthetic else 0
        width = max(
            1,
            math.ceil(face.getlength(text) / scale)
            + spacing * max(0, len(text) - 1)
            + math.ceil(2 * stroke / scale),
        )
        image = Image.new("L", (width * scale, size * scale), 255)
        draw = ImageDraw.Draw(image)
        alphabet = (
            "0123456789:-" if family in {"inter", "condensed", "digital"} else "0123456789:-月日"
        )
        top = face.getbbox(alphabet, anchor="la")[1]
        if spacing:
            pos = stroke
            for char in text:
                draw.text(
                    (pos, -top),
                    char,
                    font=face,
                    fill=0,
                    anchor="la",
                    stroke_width=stroke,
                    stroke_fill=0,
                )
                pos += face.getlength(char) + spacing * scale
        else:
            draw.text(
                (stroke, -top),
                text,
                font=face,
                fill=0,
                anchor="la",
                stroke_width=stroke,
                stroke_fill=0,
            )
        return self.monochrome(image, scale)

    @staticmethod
    def family_for_text(family, text):
        return (
            "sans"
            if family in {"inter", "condensed", "digital"} and any(ord(c) > 255 for c in text)
            else family
        )

    def text_layer(self, widget, text):
        bg = 0 if widget.inverted else 255
        tile = Image.new("L", (widget.width, widget.height), bg)
        family = self.family_for_text(widget.font, text)
        font = self.font(family, widget.font_size, widget.font_weight)
        for row, line in enumerate(text.split("\n")):
            if widget.kind in DYNAMIC:
                glyphs = [
                    self.glyph(font, widget.font_size, c, widget.letter_spacing) for c in line
                ]
                length = sum(g.width for g in glyphs) - (widget.letter_spacing if glyphs else 0)
                ink = Image.new("L", (max(1, length), widget.font_size), 255)
                pos = 0
                for g in glyphs:
                    ink.paste(g, (pos, 0))
                    pos += g.width
            else:
                ink = self.text_bitmap(font, widget.font_size, line, widget.letter_spacing)
                length = ink.width
            x = (
                0
                if widget.align == "left"
                else max(0, (widget.width - length) // 2)
                if widget.align == "center"
                else max(0, widget.width - length)
            )
            if widget.inverted:
                ink = ink.point(lambda v: 255 - v)
            tile.paste(ink, (x, row * (widget.font_size + 3)))
        return tile

    def glyph(self, font, size, char, spacing=0):
        ink = self.text_bitmap(font, size, char)
        if not spacing:
            return ink
        padded = Image.new("L", (ink.width + spacing, size), 255)
        padded.paste(ink, (0, 0))
        return padded

    def carousel_layer(self, item, states, index):
        entries = item.carousel_entries
        entry = entries[index % len(entries)] if entries else None
        value = item.model_copy(
            update={"kind": "entity", "entity_id": entry.entity_id if entry else None}
        )
        state = states.get(value.entity_id or "", {})
        label = (
            entry.label
            if entry and entry.label
            else state.get("attributes", {}).get("friendly_name", value.entity_id or "选择轮播实体")
        )
        label_size = min(12, max(8, item.height - item.font_size - 2))
        label_item = value.model_copy(
            update={
                "kind": "text",
                "font_size": label_size,
                "height": label_size,
                "font_weight": 500,
            }
        )
        # Truncate the label by pixels; preserve a readable ellipsis rather than clipping Chinese strokes.
        while (
            len(label) > 1
            and self.text_bitmap(
                self.font(self.family_for_text(item.font, label), label_size, 500),
                label_size,
                label,
            ).width
            > item.width
        ):
            label = label[:-2] + "…"
        tile = Image.new("L", (item.width, item.height), 0 if item.inverted else 255)
        tile.paste(self.text_layer(label_item, label), (0, 0))
        tile.paste(
            self.text_layer(
                value.model_copy(update={"height": max(1, item.height - label_size - 2)}),
                self.entity_text(value, states),
            ),
            (0, label_size + 2),
        )
        return tile

    def chart(self, item, states, histories, local):
        tile = Image.new("L", (item.width, item.height), 255)
        draw = ImageDraw.Draw(tile)
        key = HomeAssistant.history_key(item.entity_id, item.history_duration, item.attribute)
        values = histories.get(key, histories.get(item.entity_id, []))
        usable = [
            (float(p[0]), float(p[1]))
            for p in values
            if p[1] is not None and math.isfinite(float(p[1]))
        ]
        label_font = self.font("sans", 10, 400)

        def label(x, y, text, size=10, anchor="lt"):
            ink = self.text_bitmap(self.font("sans", size, 400), size, text)
            if anchor[0] == "r":
                x -= ink.width
            elif anchor[0] == "m":
                x -= ink.width // 2
            tile.paste(ink, (int(x), int(y)))

        header = min(item.font_size + 5, item.height // 3) if item.chart_value else 0
        if item.chart_value:
            value = self.entity_text(item, states)
            value_widget = item.model_copy(
                update={
                    "kind": "text",
                    "width": max(1, item.width - 35),
                    "height": header,
                    "font_size": max(10, header - 4),
                    "inverted": False,
                }
            )
            tile.paste(self.text_layer(value_widget, value), (1, 0))
            minutes = int(item.history_duration * 60)
            period = f"{minutes}m" if minutes < 60 or minutes % 60 else f"{minutes // 60}h"
            label(item.width - 1, 3, period, anchor="rt")
        if not usable:
            label(1, header + 4, "暂无 HA 历史", size=12)
            return tile.point(lambda v: 255 - v) if item.inverted else tile
        ymin, ymax = min(v for _, v in usable), max(v for _, v in usable)
        if ymax == ymin:
            pad = max(1, abs(ymin) * 0.05)
            ymin, ymax = ymin - pad, ymax + pad

        def number(v):
            if abs(v) >= 10000:
                return f"{v / 1000:.0f}k"
            if abs(v) >= 100:
                return f"{v:.0f}"
            return f"{v:.1f}".rstrip("0").rstrip(".")

        labels = [number(ymax), number(ymin)]
        margin = (
            min(item.width // 3, math.ceil(max(label_font.getlength(t) for t in labels)) + 5)
            if item.chart_labels
            else 2
        )
        left, right, top, bottom = (
            margin,
            item.width - 2,
            header + 4,
            item.height - (15 if item.chart_labels else 3),
        )
        if right - left < 8 or bottom - top < 8:
            label(1, header + 2, "请增大图表区域")
            return tile.point(lambda v: 255 - v) if item.inverted else tile
        end = local.timestamp()
        start = end - item.history_duration * 3600

        def point(ts, v):
            return (
                max(left, min(right, round(left + (ts - start) / (end - start) * (right - left)))),
                round(top + (1 - (v - ymin) / (ymax - ymin)) * (bottom - top)),
            )

        if item.chart_grid:
            for y in [top, (top + bottom) // 2, bottom]:
                for x in range(left, right + 1, 4):
                    draw.point((x, y), fill=0)
        if item.chart_labels:
            label(left - 4, top, labels[0], anchor="rt")
            label(left - 4, bottom - 9, labels[1], anchor="rt")
            zone = local.tzinfo
            label(
                left,
                bottom + 4,
                datetime.fromtimestamp(start, zone).strftime(
                    "%d日%H时" if item.history_duration >= 24 else "%H:%M"
                ),
            )
            label(right, bottom + 4, local.strftime("%H:%M"), anchor="rt")
        cells = int(item.history_duration * 4)
        pitch = (right - left) / cells
        for cell in range(1, cells):
            x = round(left + cell * pitch)
            # Quarter-hour ticks remain present; longer windows use major dotted lines.
            draw.line((x, bottom, x, bottom + 2), fill=0)
            if item.chart_grid and pitch >= 5:
                for y in range(top, bottom, 4):
                    draw.point((x, y), fill=0)
        if item.chart_labels and pitch >= 26:
            for cell in range(1, cells):
                tick_label = datetime.fromtimestamp(start + cell * 900, local.tzinfo).strftime(
                    "%H:%M"
                )
                if cell * pitch > 26 and (cells - cell) * pitch > 26:
                    label(round(left + cell * pitch), bottom + 4, tick_label, anchor="mt")
        draw.line((left, top, left, bottom, right, bottom), fill=0)
        last = None
        # Each pixel column retains its extrema, so short power spikes remain visible.
        columns = {}
        for ts, v in values:
            if v is None:
                last = None
                continue
            x, y = point(float(ts), float(v))
            if item.chart_style == "bar":
                lo, hi = columns.get(x, (y, y))
                columns[x] = (min(lo, y), max(hi, y))
            elif last:
                if item.chart_style == "step":
                    draw.line((last, (x, last[1]), (x, y)), fill=0, width=1)
                else:
                    draw.line((last, (x, y)), fill=0, width=1)
            else:
                draw.point((x, y), fill=0)
            last = (x, y)
        for x, (lo, hi) in columns.items():
            draw.line((x, min(bottom, lo), x, bottom), fill=0)
        if last:
            draw.rectangle((last[0] - 1, last[1] - 1, last[0] + 1, last[1] + 1), fill=0)
        return tile.point(lambda v: 255 - v) if item.inverted else tile

    def render(
        self,
        layout: Layout,
        states,
        histories=None,
        epoch=None,
        telemetry=None,
        carousel_preview=None,
    ):
        w, h = layout.size
        image = Image.new("L", (w, h), 255)
        draw = ImageDraw.Draw(image)
        local = (
            datetime.fromtimestamp(epoch, ZoneInfo(layout.timezone))
            if epoch is not None
            else datetime.now(ZoneInfo(layout.timezone))
        )
        for item in layout.widgets:
            if item.kind in DYNAMIC:
                continue
            if item.kind in {"text", "entity"}:
                text = item.text if item.kind == "text" else self.entity_text(item, states)
                image.paste(self.text_layer(item, text), (item.x, item.y))
            elif item.kind == "line":
                draw.line(
                    (item.x, item.y, item.x + item.width - 1, item.y + item.height - 1),
                    fill=0,
                    width=1,
                )
            elif item.kind == "rect":
                draw.rectangle(
                    (item.x, item.y, item.x + item.width - 1, item.y + item.height - 1),
                    outline=0,
                    width=1,
                )
            elif item.kind == "qrcode":
                qr = qrcode.QRCode(border=1, box_size=1)
                qr.add_data(item.text)
                qr.make(fit=True)
                tile = qr.make_image().convert("L")
                scale = min(item.width // tile.width, item.height // tile.height)
                if scale < 1:
                    raise ValueError("二维码区域太小，无法保持可扫描模块")
                tile = tile.resize(
                    (tile.width * scale, tile.height * scale), Image.Resampling.NEAREST
                )
                image.paste(tile, (item.x, item.y))
            elif item.kind == "image" and item.asset:
                path = self.assets / Path(item.asset).name
                if path.is_file():
                    with Image.open(path) as source:
                        source.thumbnail((item.width, item.height))
                        image.paste(
                            source.convert("L").convert("1", dither=Image.Dither.FLOYDSTEINBERG),
                            (item.x, item.y),
                        )
            elif item.kind == "chart":
                image.paste(self.chart(item, states, histories or {}, local), (item.x, item.y))
        # Dynamic widgets are excluded from host static background hashes.
        static = image.point(lambda v: 255 if v >= 160 else 0).convert("1")
        preview = static.convert("L")
        for item in layout.widgets:
            if item.kind in DYNAMIC:
                preview.paste(
                    self.carousel_layer(
                        item,
                        states,
                        (carousel_preview or {}).get(
                            item.id, int(local.timestamp()) // (60 * item.carousel_minutes)
                        ),
                    )
                    if item.kind == "entity_carousel"
                    else self.text_layer(item, self.dynamic_text(item, local, telemetry)),
                    (item.x, item.y),
                )
        if layout.inverted:
            static = static.point(lambda v: 255 - v)
            preview = preview.point(lambda v: 255 - v)
        return static, preview.convert("1", dither=0)

    def blob(self, layout, static, states=None):
        background = pack_mono(static)
        regions = []
        for item in layout.widgets:
            if item.kind not in DYNAMIC:
                continue
            if item.kind == "entity_carousel":
                variants = []
                for index in range(max(1, len(item.carousel_entries))):
                    tile = self.carousel_layer(item, states or {}, index)
                    if layout.inverted:
                        tile = tile.point(lambda v: 255 - v)
                    bits = pack_mono(tile)
                    variants.append(
                        GLYPH_HEADER.pack(index, item.width, item.height, 0, len(bits)) + bits
                    )
                regions.append(
                    REGION_HEADER.pack(
                        5,
                        0,
                        item.x,
                        item.y,
                        item.width,
                        item.height,
                        item.carousel_minutes,
                        0,
                        len(variants),
                    )
                    + b"".join(variants)
                )
                continue
            alphabet = "0123456789:-" + ("月日" if item.kind == "date" else "")
            if item.kind == "tag_temperature":
                alphabet = "0123456789-.°C"
            font = self.font(
                self.family_for_text(item.font, alphabet), item.font_size, item.font_weight
            )
            glyphs = []
            for char in alphabet:
                glyph = self.glyph(font, item.font_size, char, item.letter_spacing)
                width = glyph.width
                bits = pack_mono(glyph.point(lambda v: 255 if v >= 160 else 0))
                glyphs.append(
                    GLYPH_HEADER.pack(ord(char), width, item.font_size, width, len(bits)) + bits
                )
            flags = {"left": 0, "center": 1, "right": 2}[item.align] | (
                4 if item.inverted ^ layout.inverted else 0
            )
            regions.append(
                REGION_HEADER.pack(
                    DYNAMIC[item.kind],
                    flags,
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    item.font_size,
                    item.deadline or 0,
                    len(glyphs),
                )
                + b"".join(glyphs)
            )
        if len(regions) > 4:
            raise ValueError("最多四个本地动态区域")
        result = (
            BLOB_HEADER.pack(
                b"EPD1", 1, layout.rotation // 90, *layout.size, len(background), len(regions)
            )
            + background
            + b"".join(regions)
        )
        if len(result) > 16384:
            raise ValueError("显示包超过 16KB，请缩小本地时钟字体")
        return result

    def program_blob(self, program, pages):
        if not program.enabled or len(pages) == 1:
            return pages[0]
        result = (
            struct.pack(
                "<4sBBHQ", b"EPS1", 1, len(pages), program.interval_minutes, program.started_at
            )
            + b"".join(struct.pack("<H", len(page)) for page in pages)
            + b"".join(pages)
        )
        if len(result) > 16384:
            raise ValueError("轮播总显示包超过 16KB，请减少画布数量或本地字号")
        return result


def png(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
