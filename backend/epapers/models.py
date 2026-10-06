from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILES = {
    "HINK-E0213A41": {"width": 212, "height": 104, "controller": "IL3897", "qualified": False},
    "HINK-E0213A55": {"width": 212, "height": 104, "controller": "SSD1675B", "qualified": False},
    "OPM021B1": {"width": 250, "height": 122, "controller": "IL3895/SSD1673A", "qualified": False},
}


class CarouselEntry(BaseModel):
    entity_id: str = Field(max_length=255)
    label: str = Field(default="", max_length=60)


class Widget(BaseModel):
    id: str
    kind: Literal[
        "text",
        "entity",
        "clock",
        "date",
        "line",
        "rect",
        "qrcode",
        "image",
        "chart",
        "countdown",
        "tag_temperature",
        "entity_carousel",
    ]
    x: int = Field(ge=0, le=512)
    y: int = Field(ge=0, le=512)
    width: int = Field(default=80, ge=1, le=512)
    height: int = Field(default=24, ge=1, le=512)
    text: str = Field(default="", max_length=2048)
    entity_id: str | None = None
    attribute: str | None = None
    font: str = "sans"
    font_size: int = Field(default=16, ge=6, le=96)
    font_weight: Literal[400, 500, 700, 900] = 400
    letter_spacing: int = Field(default=0, ge=0, le=8)
    align: Literal["left", "center", "right"] = "left"
    decimals: int = Field(default=1, ge=0, le=4)
    unit: str | None = None
    asset: str | None = None
    inverted: bool = False
    deadline: int | None = None
    history_hours: Literal[1, 6, 12, 24, 48, 168] = 24
    history_minutes: int | None = Field(default=None, ge=15, le=10080)
    chart_style: Literal["line", "step", "bar"] = "line"
    chart_labels: bool = True
    chart_value: bool = True
    chart_grid: bool = True
    state_labels: bool = True
    carousel_entries: list[CarouselEntry] = Field(default_factory=list, max_length=8)
    carousel_minutes: int = Field(default=1, ge=1, le=1440)

    @model_validator(mode="after")
    def history_grid(self):
        if self.history_minutes is not None and self.history_minutes % 15:
            raise ValueError("趋势图时长请使用 15 分钟的倍数")
        if self.kind == "entity_carousel" and (self.width > 128 or self.height > 96):
            raise ValueError("组件轮播区域最大为 128×96 像素，建议使用 96×32")
        return self

    @property
    def history_duration(self):
        return (
            (self.history_minutes / 60) if self.history_minutes is not None else self.history_hours
        )


class Layout(BaseModel):
    name: str = Field(default="新页面", max_length=100)
    profile: str = "OPM021B1"
    rotation: Literal[0, 90, 180, 270] = 0
    inverted: bool = False
    widgets: list[Widget] = Field(default_factory=list, max_length=40)
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def geometry(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError("时区无效，请使用 Asia/Shanghai 等 IANA 时区") from err
        if self.profile not in PROFILES:
            raise ValueError("不支持的黑白屏型号")
        w, h = self.size
        dynamic = []
        for item in self.widgets:
            if item.x + item.width > w or item.y + item.height > h:
                raise ValueError(f"组件 {item.id} 超出屏幕范围")
            if item.kind in {"clock", "date", "countdown", "tag_temperature", "entity_carousel"}:
                for old in dynamic:
                    if (
                        item.x < old.x + old.width
                        and old.x < item.x + item.width
                        and item.y < old.y + old.height
                        and old.y < item.y + item.height
                    ):
                        raise ValueError("本地动态区域不能重叠")
                dynamic.append(item)
        if len({i.id for i in self.widgets}) != len(self.widgets):
            raise ValueError("组件 ID 重复")
        return self

    @property
    def size(self):
        p = PROFILES[self.profile]
        return (
            (p["height"], p["width"]) if self.rotation in (90, 270) else (p["width"], p["height"])
        )


class Device(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9:_-]{1,80}$")
    name: str = Field(max_length=100)
    profile: str = "OPM021B1"
    gateway_id: str | None = None
    address: str | None = None
    simulated: bool = False
    last_seen: float | None = None
    battery_mv: int | None = Field(default=None, ge=0, le=5000)
    supply_mv: int | None = Field(default=None, ge=0, le=5000)
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    rssi: int | None = Field(default=None, ge=-127, le=20)
    firmware: str | None = None
    applied_revision: str | None = None
    last_refresh: float | None = None
    last_sync: float | None = None
    drift_ppm: float | None = None
    error: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    temperature_centi: int | None = Field(default=None, ge=-2500, le=8500)
    temperature_sampled_at: float | None = None
    battery_sampled_at: float | None = None
    battery_source: Literal["unknown", "external", "direct", "regulated"] = "unknown"
    refresh_interval: int = Field(default=300, ge=30, le=86400)
    ha_name: str = Field(default="", max_length=100)
    ha_entity_prefix: str = Field(default="", max_length=60, pattern=r"^(?:[a-z][a-z0-9_]{1,59})?$")
    area_name: str = Field(default="", max_length=100)
    ha_sync_status: str | None = None


class DisplayProgram(BaseModel):
    pages: list[Layout] = Field(min_length=1, max_length=6)
    enabled: bool = False
    interval_minutes: int = Field(default=1, ge=1, le=1440)
    started_at: int = Field(default=0, ge=0, le=4102444800)

    @model_validator(mode="after")
    def profiles_match(self):
        if len({p.profile for p in self.pages}) != 1:
            raise ValueError("同一块屏幕的所有画布必须使用相同型号")
        if len({p.timezone for p in self.pages}) != 1:
            raise ValueError("同一块屏幕的所有画布必须使用相同时区")
        return self


class JobState(StrEnum):
    QUEUED = "queued"
    TRANSFERRING = "transferring"
    RECEIVED = "received"
    APPLIED = "applied"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class Job(BaseModel):
    id: str
    device_id: str
    kind: str = "display"
    revision: str | None = None
    state: JobState = JobState.QUEUED
    created: float
    updated: float
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def template(
    profile="OPM021B1",
    mode="climate",
    title="客厅",
    temperature="sensor.demo_temperature",
    humidity="sensor.demo_humidity",
):
    from .templates import build_template

    return build_template(profile, mode, title, temperature, humidity)
