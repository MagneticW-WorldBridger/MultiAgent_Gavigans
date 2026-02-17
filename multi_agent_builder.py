"""
Build multi-agent root from DB for Gavigans.
All webchat users share the same agent set (no per-user auth).
"""
import logging
from google.adk.agents import Agent
from app.chat.tools import build_tools_from_config
from app.db import db

logger = logging.getLogger(__name__)

FALLBACK_INSTRUCTION = """You are a helpful AI assistant for Gavigans.
Answer questions about the company, products, and support. Be friendly and concise."""


async def build_root_agent(before_callback=None, after_callback=None) -> Agent:
    """
    Build root agent with sub-agents from DB.
    Uses first user's agents (seed puts them under admin). No auth - all Gavigans.
    """
    await db.connect()

    user = await db.user.find_first()
    if not user:
        logger.warning("No user in DB - running seed...")
        try:
            from seed import seed
            await seed()
            await db.connect()  # seed disconnects
            user = await db.user.find_first()
        except Exception as e:
            logger.warning("Seed failed: %s. Using fallback single agent.", e)
            user = None
    if not user:
        return Agent(
            name="gavigans_agent",
            model="gemini-2.0-flash",
            description="Gavigans assistant",
            instruction=FALLBACK_INSTRUCTION,
            tools=[],
            before_agent_callback=before_callback,
            after_agent_callback=after_callback,
        )

    db_agents = await db.agent.find_many(where={"userId": user.id})
    if not db_agents:
        logger.warning("No agents in DB - run seed.py first. Using fallback single agent.")
        return Agent(
            name="gavigans_agent",
            model="gemini-2.0-flash",
            description="Gavigans assistant",
            instruction=FALLBACK_INSTRUCTION,
            tools=[],
            before_agent_callback=before_callback,
            after_agent_callback=after_callback,
        )

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

    agent_list = "\n".join(
        f"- {a.name}: {db_agents[i].description}" for i, a in enumerate(sub_agents)
    )

    root_instruction = (
        "You are the main routing agent for Gavigans. Your job is to understand "
        "the user's question and delegate it to the most appropriate specialist agent.\n\n"
        f"Available agents:\n{agent_list}\n\n"
        "Analyze the user's message and transfer to the most appropriate agent. "
        "If no agent is a good fit, respond directly with a helpful message."
    )

    root = Agent(
        name="gavigans_agent",
        model="gemini-2.0-flash",
        description="Gavigans multi-agent orchestrator",
        instruction=root_instruction,
        sub_agents=sub_agents,
        before_agent_callback=before_callback,
        after_agent_callback=after_callback,
    )
    logger.info("✅ Multi-agent root built: %d sub-agents from DB", len(sub_agents))
    return root
