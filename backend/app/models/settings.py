from typing import Any

from pydantic import BaseModel, Field


class SettingGroup(BaseModel):
    id: str
    label: str
    description: str = ""


class SettingField(BaseModel):
    key: str
    env_var: str
    group: str
    label: str
    description: str = ""
    # string | secret | int | float | bool | select | list (comma-separated string)
    kind: str
    # live | ingestion | restart | worker (see app.core.runtime_settings.ApplyMode)
    apply: str
    options: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False
    # Always None for secrets; use is_set.
    value: Any = None
    default: Any = None
    # Value from the environment/.env alone, i.e. what clearing the override returns to.
    env_value: Any = None
    is_set: bool = False
    # A value saved from the Settings page overrides the environment/.env.
    overridden: bool = False
    # Changed since this API process started, but only read at startup/by a worker.
    pending_restart: bool = False


class SettingsResponse(BaseModel):
    groups: list[SettingGroup]
    settings: list[SettingField]
    features: dict[str, bool]
    overrides_file: str
    # Keys whose change was applied on the last save (PUT only).
    changed: list[str] = Field(default_factory=list)
    ingestion_restarted: bool = False


class SettingsUpdate(BaseModel):
    # setting key -> new value; null clears the saved override.
    values: dict[str, Any] = Field(default_factory=dict, max_length=200)
