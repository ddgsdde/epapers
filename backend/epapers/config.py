from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EPAPERS_", env_file=".env", extra="ignore")
    data_dir: Path = Path(".local/data")
    resource_dir: Path = Path(".")
    ha_url: str = ""
    ha_token: str = ""
    api_token: str = ""
    gateway_token: str = ""
    timezone: str = "Asia/Shanghai"
    demo: bool = False
    host: str = "127.0.0.1"
    port: int = 8099
    refresh_interval: int = 30

    @property
    def supervisor(self):
        import os

        return bool(os.getenv("SUPERVISOR_TOKEN"))
