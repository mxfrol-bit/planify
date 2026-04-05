from pydantic import BaseModel
from typing import Optional
from datetime import date


class HabitCreate(BaseModel):
    name: str
    emoji: str = "✅"
    frequency: str = "daily"


class HabitToggle(BaseModel):
    date: str  # YYYY-MM-DD


class TaskCreate(BaseModel):
    title: str
    emoji: str = "📌"
    deadline: Optional[str] = None  # YYYY-MM-DD
    priority: str = "medium"
    category: str = "personal"


class TokenAuth(BaseModel):
    token: str
