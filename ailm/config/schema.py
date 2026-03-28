"""Configuration schema with Pydantic validation."""

from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    """Settings for the Ollama-backed LLM client."""

    model: str = "qwen3.5:9b"
    base_url: str = "http://localhost:11434"
    timeout: int = Field(default=30, gt=0)
    enabled: bool = True


class SourcesConfig(BaseModel):
    """Settings for all event sources and monitoring thresholds."""

    journald_enabled: bool = True
    pacman_log_path: str = "/var/log/pacman.log"
    snapshot_path: str = "/.snapshots"
    disk_interval: int = Field(default=300, gt=0)
    disk_warn_pct: int = Field(default=80, ge=1, le=100)
    disk_critical_pct: int = Field(default=95, ge=1, le=100)
    service_interval: int = Field(default=300, gt=0)

    @model_validator(mode="after")
    def warn_less_than_critical(self) -> "SourcesConfig":
        """Ensure the warning threshold stays below the critical threshold."""
        if self.disk_warn_pct >= self.disk_critical_pct:
            msg = f"disk_warn_pct ({self.disk_warn_pct}) must be less than disk_critical_pct ({self.disk_critical_pct})"
            raise ValueError(msg)
        return self


class UIConfig(BaseModel):
    """Dimensions for the desktop popup UI."""

    popup_width: int = Field(default=420, gt=0)
    popup_height: int = Field(default=600, gt=0)


class DedupConfig(BaseModel):
    """Event deduplication settings."""

    window_seconds: int = Field(default=60, gt=0)
    baseline_seconds: int = Field(default=300, gt=0)
    max_per_source_per_minute: int = Field(default=20, gt=0)


class TrendConfig(BaseModel):
    """EMA trend detection settings."""

    alpha: float = Field(default=0.1, gt=0.0, lt=1.0)
    window_size: int = Field(default=60, gt=10)
    cooldown_seconds: int = Field(default=600, gt=0)


class RingLogConfig(BaseModel):
    """Crash-resilient ring buffer log settings."""

    enabled: bool = True
    max_lines: int = Field(default=50000, gt=1000)
    max_archives: int = Field(default=3, ge=1)
    sync_interval: float = Field(default=10.0, gt=1.0)


class SchedulerConfig(BaseModel):
    """Scheduler-related configuration values."""

    briefing_cron: str = "0 6 * * *"


class DBConfig(BaseModel):
    """Database location and retention settings."""

    path: str = ""  # empty = resolved to XDG default at load time
    retention_days: int = Field(default=30, gt=0)


class AilmConfig(BaseModel):
    """Top-level configuration model for the entire application."""

    llm: LLMConfig = LLMConfig()
    sources: SourcesConfig = SourcesConfig()
    ui: UIConfig = UIConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    db: DBConfig = DBConfig()
    dedup: DedupConfig = DedupConfig()
    trend: TrendConfig = TrendConfig()
    ringlog: RingLogConfig = RingLogConfig()

    def __repr__(self) -> str:
        """Return a concise representation of the active configuration."""
        return (
            "AilmConfig("
            f"llm={self.llm!r}, "
            f"sources={self.sources!r}, "
            f"ui={self.ui!r}, "
            f"scheduler={self.scheduler!r}, "
            f"db={self.db!r})"
        )
