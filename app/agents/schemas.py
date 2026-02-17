from datetime import datetime

from pydantic import BaseModel


class AgentCreate(BaseModel):
    name: str
    model: str = "gemini-2.0-flash"
    description: str
    instruction: str
    tools: list = []


class AgentUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    description: str | None = None
    instruction: str | None = None
    tools: list | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    model: str
    description: str
    instruction: str
    tools: list
    created_at: datetime
    updated_at: datetime
