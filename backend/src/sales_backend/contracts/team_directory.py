from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TeamOption(BaseModel):
    id: UUID
    name: str
    parent_id: UUID | None = None


class TeamDirectory(BaseModel):
    data_source: Literal['database'] = 'database'
    teams: list[TeamOption]
