"""XDG-compliant TOML config loader."""

import os
import tomllib
from pathlib import Path

from ailm.config.schema import AilmConfig


def get_config_path() -> Path:
    """Return XDG-compliant config file path."""
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "ailm" / "config.toml"


def get_data_dir() -> Path:
    """Return XDG-compliant data directory."""
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "ailm"


def load_config(path: Path | None = None) -> AilmConfig:
    """Load config from TOML, falling back to defaults for missing values."""
    config_path = path or get_config_path()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        config = AilmConfig.model_validate(data)
    except FileNotFoundError:
        config = AilmConfig()

    # Resolve empty DB path to XDG default
    if not config.db.path:
        config.db.path = str(get_data_dir() / "ailm.db")

    return config


def dump_config(config: AilmConfig) -> str:
    """Serialize config to TOML-formatted string."""
    lines: list[str] = []
    for section_name, section_data in config.model_dump().items():
        lines.append(f"[{section_name}]")
        for key, value in section_data.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        inner = ", ".join(_toml_value(v) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        inner = ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items())
        return f"{{{inner}}}"
    return str(value)
