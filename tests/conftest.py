"""Shared test fixtures."""

import os
from pathlib import Path

import pytest

from ailm.config.schema import AilmConfig

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tmp_config(tmp_path: Path) -> AilmConfig:
    """Config with DB path pointing to tmp_path."""
    return AilmConfig(db={"path": str(tmp_path / "test.db")})


@pytest.fixture
def sample_config_toml(tmp_path: Path) -> Path:
    """A config.toml with partial overrides."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""\
[llm]
model = "test-model"
timeout = 10

[sources]
disk_interval = 60

[db]
retention_days = 7
""")
    return config_file
