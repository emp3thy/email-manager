"""Relay configuration: client id/secret from ~/.config/email-manager/relay.env."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_DIR = Path("~/.config/email-manager").expanduser()


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RelayConfig:
    client_id: str
    client_secret: str
    tenant: str
    config_dir: Path

    @property
    def token_path(self) -> Path:
        return self.config_dir / "outlook-token.json"


def load_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> RelayConfig:
    env_path = Path(config_dir) / "relay.env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {env_path}: {exc}") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    missing = [k for k in ("MS_CLIENT_ID", "MS_CLIENT_SECRET") if not values.get(k)]
    if missing:
        raise ConfigError(f"{env_path} is missing {', '.join(missing)}")
    return RelayConfig(
        client_id=values["MS_CLIENT_ID"],
        client_secret=values["MS_CLIENT_SECRET"],
        tenant=values.get("MS_TENANT") or "consumers",
        config_dir=Path(config_dir),
    )
