from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from .entity import EPapersEntity, add_dynamic_entities

DESCRIPTIONS = [
    ("temperature_centi", "屏幕温度", "°C", SensorDeviceClass.TEMPERATURE),
    ("battery_percent", "电量", "%", SensorDeviceClass.BATTERY),
    ("battery_mv", "电池电压", "mV", SensorDeviceClass.VOLTAGE),
    ("supply_mv", "供电电压", "mV", SensorDeviceClass.VOLTAGE),
    ("rssi", "信号强度", "dBm", SensorDeviceClass.SIGNAL_STRENGTH),
    ("last_seen", "最近出现", None, SensorDeviceClass.TIMESTAMP),
    ("last_refresh", "最近刷新", None, SensorDeviceClass.TIMESTAMP),
    ("last_sync", "最近对时", None, SensorDeviceClass.TIMESTAMP),
    ("error", "错误", None, None),
    ("job_state", "更新状态", None, None),
    ("full_refreshes", "全刷次数", None, None),
    ("partial_refreshes", "快速刷新次数", None, None),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    add_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda key: [EPapersSensor(coordinator, key, *d) for d in DESCRIPTIONS],
    )


class EPapersSensor(EPapersEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, field, name, unit, device_class):
        super().__init__(coordinator, key, field, name)
        self.field = field
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if field in {"battery_percent", "battery_mv", "supply_mv", "temperature_centi", "rssi"}:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if field == "temperature_centi":
            self._attr_entity_category = None

    @property
    def native_value(self):
        if self.field in {"full_refreshes", "partial_refreshes"}:
            return (self.coordinator.device(self.key) or {}).get("capabilities", {}).get(self.field)
        if self.field == "job_state":
            jobs = [j for j in self.coordinator.data.get("jobs", []) if j["device_id"] == self.key]
            return jobs[-1]["state"] if jobs else "idle"
        value = (self.coordinator.device(self.key) or {}).get(self.field)
        if self.field == "temperature_centi" and value is not None:
            return value / 100
        if self._attr_device_class == SensorDeviceClass.TIMESTAMP and value is not None:
            return datetime.fromtimestamp(value, UTC)
        return value

    @property
    def extra_state_attributes(self):
        device = self.coordinator.device(self.key) or {}
        if self.field in {"battery_percent", "battery_mv", "supply_mv"}:
            return {
                "power_source": device.get("battery_source"),
                "sampled_at": device.get("battery_sampled_at"),
                "method": "voltage_estimate"
                if device.get("battery_source") == "direct"
                else "supply_only",
            }
        if self.field == "temperature_centi":
            return {
                "source": "panel_internal_sensor",
                "sampled_at": device.get("temperature_sampled_at"),
            }
        if self.field in {"full_refreshes", "partial_refreshes"}:
            return {
                "refresh_reason": device.get("capabilities", {}).get("refresh_reason"),
                "since": "MCU restart",
            }
        return None
