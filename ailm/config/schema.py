"""Configuration schema with Pydantic validation."""

from pydantic import BaseModel, Field, model_validator


class LLMConfig(BaseModel):
    model: str = "qwen3.5:9b"
    base_url: str = "http://localhost:11434"
    timeout: int = Field(default=30, gt=0)
    enabled: bool = True


class SourcesConfig(BaseModel):
    journald_enabled: bool = True
    pacman_log_path: str = "/var/log/pacman.log"
    snapshot_path: str = "/.snapshots"
    disk_interval: int = Field(default=300, gt=0)
    disk_warn_pct: int = Field(default=80, ge=1, le=100)
    disk_critical_pct: int = Field(default=95, ge=1, le=100)
    service_interval: int = Field(default=300, gt=0)

    @model_validator(mode="after")
    def warn_less_than_critical(self) -> "SourcesConfig":
        if self.disk_warn_pct >= self.disk_critical_pct:
            msg = f"disk_warn_pct ({self.disk_warn_pct}) must be less than disk_critical_pct ({self.disk_critical_pct})"
            raise ValueError(msg)
        return self


class UIConfig(BaseModel):
    popup_width: int = Field(default=420, gt=0)
    popup_height: int = Field(default=600, gt=0)


class SchedulerConfig(BaseModel):
    briefing_cron: str = "0 6 * * *"


class DBConfig(BaseModel):
    path: str = ""  # empty = resolved to XDG default at load time
    retention_days: int = Field(default=30, gt=0)


class AilmConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    sources: SourcesConfig = SourcesConfig()
    ui: UIConfig = UIConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    db: DBConfig = DBConfig()
