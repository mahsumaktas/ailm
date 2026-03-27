"""Config schema and loader tests."""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ailm.config.loader import dump_config, get_config_path, get_data_dir, load_config
from ailm.config.schema import (
    AilmConfig,
    DBConfig,
    LLMConfig,
    SchedulerConfig,
    SourcesConfig,
    UIConfig,
)


# --- Schema validation ---


class TestConfigDefaults:
    def test_all_defaults(self):
        config = AilmConfig()
        assert config.llm.model == "qwen3.5:9b"
        assert config.llm.base_url == "http://localhost:11434"
        assert config.llm.timeout == 30
        assert config.llm.enabled is True
        assert config.sources.journald_enabled is True
        assert config.sources.pacman_log_path == "/var/log/pacman.log"
        assert config.sources.snapshot_path == "/.snapshots"
        assert config.sources.disk_interval == 300
        assert config.sources.disk_warn_pct == 80
        assert config.sources.disk_critical_pct == 95
        assert config.sources.service_interval == 300
        assert config.ui.popup_width == 420
        assert config.ui.popup_height == 600
        assert config.scheduler.briefing_cron == "0 6 * * *"
        assert config.db.path == ""
        assert config.db.retention_days == 30

    def test_each_section_independent(self):
        """Overriding one section does not affect others."""
        config = AilmConfig(llm=LLMConfig(model="gemma3:4b", timeout=60))
        assert config.llm.model == "gemma3:4b"
        assert config.llm.timeout == 60
        assert config.llm.base_url == "http://localhost:11434"
        assert config.sources.disk_warn_pct == 80
        assert config.ui.popup_width == 420
        assert config.db.retention_days == 30


class TestSchemaValidation:
    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            SourcesConfig(disk_interval="not_a_number")

    def test_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            SourcesConfig(disk_warn_pct=101)

    def test_below_range_raises(self):
        with pytest.raises(ValidationError):
            SourcesConfig(disk_warn_pct=0)

    def test_zero_interval_raises(self):
        with pytest.raises(ValidationError):
            SourcesConfig(disk_interval=0)

    def test_negative_timeout_raises(self):
        with pytest.raises(ValidationError):
            LLMConfig(timeout=-1)

    def test_zero_retention_raises(self):
        with pytest.raises(ValidationError):
            DBConfig(retention_days=0)

    def test_zero_popup_size_raises(self):
        with pytest.raises(ValidationError):
            UIConfig(popup_width=0)

    def test_warn_must_be_less_than_critical(self):
        with pytest.raises(ValidationError, match="must be less than"):
            SourcesConfig(disk_warn_pct=95, disk_critical_pct=80)

    def test_warn_equal_to_critical_raises(self):
        with pytest.raises(ValidationError, match="must be less than"):
            SourcesConfig(disk_warn_pct=90, disk_critical_pct=90)

    def test_valid_custom_thresholds(self):
        cfg = SourcesConfig(disk_warn_pct=60, disk_critical_pct=90)
        assert cfg.disk_warn_pct == 60
        assert cfg.disk_critical_pct == 90

    def test_boundary_thresholds(self):
        cfg = SourcesConfig(disk_warn_pct=1, disk_critical_pct=2)
        assert cfg.disk_warn_pct == 1

    def test_pydantic_coercion(self):
        """Pydantic coerces compatible types (str '10' -> int 10)."""
        cfg = LLMConfig(timeout="10")
        assert cfg.timeout == 10

    def test_model_validate_from_dict(self):
        data = {"llm": {"model": "phi3"}, "db": {"retention_days": 14}}
        config = AilmConfig.model_validate(data)
        assert config.llm.model == "phi3"
        assert config.db.retention_days == 14
        assert config.sources.disk_warn_pct == 80  # default preserved


# --- Config loader ---


class TestConfigLoader:
    def test_defaults_when_no_file(self, tmp_path: Path):
        config = load_config(tmp_path / "nonexistent.toml")
        assert config.llm.model == "qwen3.5:9b"
        assert config.db.path != ""  # resolved to XDG default

    def test_load_from_file(self, sample_config_toml: Path):
        config = load_config(sample_config_toml)
        assert config.llm.model == "test-model"
        assert config.llm.timeout == 10
        assert config.sources.disk_interval == 60
        assert config.db.retention_days == 7
        assert config.llm.base_url == "http://localhost:11434"
        assert config.sources.disk_warn_pct == 80

    def test_empty_file_gives_defaults(self, tmp_path: Path):
        empty = tmp_path / "empty.toml"
        empty.write_text("")
        config = load_config(empty)
        assert config.llm.model == "qwen3.5:9b"

    def test_invalid_toml_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.toml"
        bad.write_text("[llm\nbroken")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_config(bad)

    def test_invalid_values_raise(self, tmp_path: Path):
        bad = tmp_path / "bad_values.toml"
        bad.write_text('[sources]\ndisk_warn_pct = 200\n')
        with pytest.raises(ValidationError):
            load_config(bad)

    def test_explicit_db_path_not_overridden(self, tmp_path: Path):
        toml = tmp_path / "config.toml"
        toml.write_text('[db]\npath = "/custom/path.db"\n')
        config = load_config(toml)
        assert config.db.path == "/custom/path.db"

    def test_unknown_keys_ignored_by_default(self, tmp_path: Path):
        """Pydantic ignores extra keys — users can have custom comments/fields."""
        toml = tmp_path / "extra.toml"
        toml.write_text('[llm]\nmodel = "test"\ncustom_key = "ignored"\n')
        config = load_config(toml)
        assert config.llm.model == "test"

    def test_xdg_db_path_resolved(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        config = load_config(tmp_path / "nonexistent.toml")
        assert config.db.path == str(tmp_path / "data" / "ailm" / "ailm.db")

    def test_xdg_config_path(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = get_config_path()
        assert path == tmp_path / "ailm" / "config.toml"

    def test_xdg_data_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = get_data_dir()
        assert path == tmp_path / "ailm"


# --- Config dump ---


class TestDumpConfig:
    def test_all_sections_present(self):
        output = dump_config(AilmConfig())
        for section in ["[llm]", "[sources]", "[ui]", "[scheduler]", "[db]"]:
            assert section in output

    def test_string_values_quoted(self):
        output = dump_config(AilmConfig())
        assert 'model = "qwen3.5:9b"' in output
        assert 'base_url = "http://localhost:11434"' in output

    def test_bool_values_lowercase(self):
        output = dump_config(AilmConfig())
        assert "enabled = true" in output
        assert "journald_enabled = true" in output

    def test_int_values_unquoted(self):
        output = dump_config(AilmConfig())
        assert "timeout = 30" in output
        assert "disk_warn_pct = 80" in output

    def test_custom_values_reflected(self):
        config = AilmConfig(llm=LLMConfig(model="phi3", timeout=5))
        output = dump_config(config)
        assert 'model = "phi3"' in output
        assert "timeout = 5" in output

    def test_toml_value_list(self):
        """_toml_value handles lists correctly."""
        from ailm.config.loader import _toml_value
        assert _toml_value([1, 2, 3]) == "[1, 2, 3]"
        assert _toml_value(["a", "b"]) == '["a", "b"]'

    def test_toml_value_dict(self):
        from ailm.config.loader import _toml_value
        result = _toml_value({"key": "val"})
        assert 'key = "val"' in result
