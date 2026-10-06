from homeassistant.components.button import ButtonEntity

from .entity import EPapersEntity, add_dynamic_entities


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    add_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda key: [
            EPapersButton(coordinator, key, kind, name)
            for kind, name in [
                ("refresh", "更新屏幕"),
                ("sync_time", "立即对时"),
                ("full_refresh", "全屏刷新"),
                ("status", "读取状态"),
            ]
        ],
    )


class EPapersButton(EPapersEntity, ButtonEntity):
    def __init__(self, coordinator, key, kind, name):
        super().__init__(coordinator, key, kind, name)
        self.kind = kind

    async def async_press(self):
        await self.coordinator.command(self.key, self.kind)
