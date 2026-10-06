"""Private, write-only HA credentials. Public responses never contain the token."""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator


class HAConnection(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError:
            raise ValueError("HA 地址格式无效") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError("请填写 http(s) 地址，地址中不能包含凭据或查询参数")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError("HA Key 格式无效")
        return SecretStr(key) if key else None


class PrivateHAConfig:
    def __init__(self, directory: Path):
        self.path = directory / "ha-connection.json"

    def load(self):
        if not self.path.exists():
            return None
        # SecretStr keeps validation errors and repr safe, too.
        try:
            value = HAConnection.model_validate_json(self.path.read_text())
        except (ValueError, OSError):
            raise RuntimeError("私有 HA 配置文件无法读取") from None
        os.chmod(self.path, 0o600)
        return value.base_url, value.api_key.get_secret_value() if value.api_key else ""

    def save(self, base_url, token):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".ha-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump({"base_url": base_url, "api_key": token}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
