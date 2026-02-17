from fastapi import APIRouter, Depends, HTTPException, status
from prisma import Json
from prisma.models import User

from app.agents.schemas import AgentCreate, AgentResponse, AgentUpdate
from app.auth.utils import get_current_user
from app.db import db

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentResponse])
async def list_agents(user: User = Depends(get_current_user)):
    agents = await db.agent.find_many(where={"userId": user.id}, order={"createdAt": "asc"})
    return [_to_response(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: str, user: User = Depends(get_current_user)):
    agent = await db.agent.find_first(where={"id": agent_id, "userId": user.id})
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return _to_response(agent)


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, user: User = Depends(get_current_user)):
    agent = await db.agent.create(
        data={
            "name": body.name,
            "model": body.model,
            "description": body.description,
            "instruction": body.instruction,
            "tools": Json(body.tools),
            "user": {"connect": {"id": user.id}},
        }
    )
    return _to_response(agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: str, body: AgentUpdate, user: User = Depends(get_current_user)):
    agent = await db.agent.find_first(where={"id": agent_id, "userId": user.id})
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    data = body.model_dump(exclude_none=True)
    if not data:
        return _to_response(agent)

    if "tools" in data:
        data["tools"] = Json(data["tools"])

    updated = await db.agent.update(where={"id": agent_id}, data=data)
    return _to_response(updated)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, user: User = Depends(get_current_user)):
    agent = await db.agent.find_first(where={"id": agent_id, "userId": user.id})
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    await db.agent.delete(where={"id": agent_id})


def _to_response(agent) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        model=agent.model,
        description=agent.description,
        instruction=agent.instruction,
        tools=agent.tools if isinstance(agent.tools, list) else [],
        created_at=agent.createdAt,
        updated_at=agent.updatedAt,
    )
