import logging

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.chat.tools import build_tools_from_config
from app.db import db

logger = logging.getLogger(__name__)


async def run_agent_chat(user_id: str, conversation_id: str, message: str) -> dict:
    """
    Run a chat message through the root agent, which delegates to sub-agents.

    A fresh session is created for every message so the root agent always
    handles routing — the user never gets "stuck" on a sub-agent.
    """
    db_agents = await db.agent.find_many(where={"userId": user_id})

    if not db_agents:
        return {
            "response": "No agents configured. Please create at least one agent.",
            "agent_name": None,
        }

    # Build ADK sub-agents from DB records
    sub_agents = []
    for db_agent in db_agents:
        tool_configs = db_agent.tools if isinstance(db_agent.tools, list) else []
        agent_tools = build_tools_from_config(tool_configs)
        agent_kwargs = {
            "name": db_agent.name.lower().replace(" ", "_"),
            "model": db_agent.model,
            "description": db_agent.description,
            "instruction": db_agent.instruction,
        }
        if agent_tools:
            agent_kwargs["tools"] = agent_tools
        sub_agents.append(Agent(**agent_kwargs))

    # Build description list for root agent prompt
    agent_list = "\n".join(
        f"- {a.name}: {db_agents[i].description}" for i, a in enumerate(sub_agents)
    )

    root_agent = Agent(
        name="root_agent",
        model="gemini-2.0-flash",
        instruction=(
            "You are the main routing agent for Gavigans. Your job is to understand "
            "the user's question and delegate it to the most appropriate specialist agent.\n\n"
            f"Available agents:\n{agent_list}\n\n"
            "Analyze the user's message and transfer to the most appropriate agent. "
            "If no agent is a good fit, respond directly with a helpful message."
        ),
        sub_agents=sub_agents,
    )

    # Fresh session every time → always starts from root agent
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="gavigans",
        session_service=session_service,
    )

    session = await session_service.create_session(
        app_name="gavigans",
        user_id=user_id,
    )

    # Load conversation history and include as context so multi-turn works
    prev_messages = await db.message.find_many(
        where={"conversationId": conversation_id},
        order={"createdAt": "asc"},
        take=20,  # last 20 messages max
    )

    if prev_messages:
        history_lines = []
        for m in prev_messages:
            role_label = "User" if m.role == "user" else "Assistant"
            history_lines.append(f"{role_label}: {m.content}")
        history_text = "\n".join(history_lines)
        full_message = (
            f"[Conversation history]\n{history_text}\n\n"
            f"[Current message]\n{message}"
        )
    else:
        full_message = message

    content = types.Content(
        role="user",
        parts=[types.Part(text=full_message)],
    )

    final_response = ""
    agent_name = None
    all_texts = []

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    text = getattr(part, "text", None)
                    if text:
                        final_response = text
                        break
            agent_name = event.author
        elif event.content and event.content.parts:
            for part in event.content.parts:
                text = getattr(part, "text", None)
                if text:
                    all_texts.append(text)

    # Fallback: use last collected text if final was empty
    if not final_response and all_texts:
        final_response = all_texts[-1]

    # Persist messages in DB
    await db.message.create(
        data={
            "conversationId": conversation_id,
            "role": "user",
            "content": message,
        }
    )
    await db.message.create(
        data={
            "conversationId": conversation_id,
            "role": "assistant",
            "content": final_response,
            "agentName": agent_name,
        }
    )

    return {"response": final_response, "agent_name": agent_name}
