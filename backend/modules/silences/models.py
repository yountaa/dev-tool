"""Модели: что приходит из веба и что лежит в git. Pydantic проверит типы."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Дни как у strftime("%a").lower() — удобно сравнивать в билдере расписания.
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Matcher(BaseModel):
    """Условие, по которому глушим алерт — пара label=value."""
    name: str            # label, напр. alertname
    value: str           # значение, напр. HighCPUUsage
    isRegex: bool = False


class Window(BaseModel):
    """Окно расписания: дни недели и время с/по."""
    days: list[Weekday]
    start: str           # "ЧЧ:ММ" по локальной таймзоне
    end: str             # "ЧЧ:ММ"; если <= start — окно уходит на следующий день


class OnetimeRequest(BaseModel):
    """Разовый silence на конкретные даты (вкладка «Разовый»)."""
    name: str            # человеческое имя правила (хранится в git)
    matchers: list[Matcher] = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime
    created_by: str
    comment: str = ""


class ScheduleRequest(BaseModel):
    """Silence по расписанию. Ставит его шедулер (вкладка «По расписанию»)."""
    name: str            # человеческое имя правила (хранится в git)
    matchers: list[Matcher] = Field(min_length=1)
    windows: list[Window] = Field(min_length=1)
    created_by: str
    comment: str = ""


class StoredConfig(BaseModel):
    """Что лежит в git: запрос (payload) + служебные поля. kind говорит, чей payload."""
    id: str
    kind: Literal["onetime", "schedule"]
    env: str
    created_at: datetime
    payload: dict
    enabled: bool = True   # выключенное правило не применяется
    am_id: str = ""        # id silence в Alertmanager (для разового — чем гасить/обновлять)
