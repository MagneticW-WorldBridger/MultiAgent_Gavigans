from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from prisma.models import User

from app.auth.utils import get_current_user
from app.chat.service import run_agent_chat
from app.db import db

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    agent_name: str | None
    created_at: datetime


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
    agent_name: str | None


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime
    messages: list[MessageResponse]


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, user: User = Depends(get_current_user)):
    if body.conversation_id:
        conversation = await db.conversation.find_first(
            where={"id": body.conversation_id, "userId": user.id}
        )
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
    else:
        conversation = await db.conversation.create(data={"userId": user.id})

    result = await run_agent_chat(user.id, conversation.id, body.message)

    return ChatResponse(
        conversation_id=conversation.id,
        response=result["response"],
        agent_name=result["agent_name"],
    )


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(user: User = Depends(get_current_user)):
    conversations = await db.conversation.find_many(
        where={"userId": user.id},
        include={"messages": True},
        order={"createdAt": "desc"},
    )
    return [
        ConversationResponse(
            id=c.id,
            created_at=c.createdAt,
            messages=[
                MessageResponse(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    agent_name=m.agentName,
                    created_at=m.createdAt,
                )
                for m in (c.messages or [])
            ],
        )
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str, user: User = Depends(get_current_user)):
    conversation = await db.conversation.find_first(
        where={"id": conversation_id, "userId": user.id},
        include={"messages": {"order_by": {"created_at": "asc"}}},
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    return ConversationResponse(
        id=conversation.id,
        created_at=conversation.createdAt,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                agent_name=m.agentName,
                created_at=m.createdAt,
            )
            for m in (conversation.messages or [])
        ],
    )
