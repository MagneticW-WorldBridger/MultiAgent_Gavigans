"""
Build multi-agent root for Gavigans.
HARDCODED agents - no DB dependency for reliability.
"""
import os
import logging
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
import httpx

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# HARDCODED AGENT CONFIGURATIONS (no DB needed)
# =============================================================================

AGENTS_CONFIG = [
    {
        "name": "faq_agent",
        "model": "gemini-2.0-flash",
        "description": "Handles frequently asked questions about the company, policies, hours, returns, shipping, and general inquiries.",
        "instruction": """You are the FAQ Agent for Gavigans. You answer frequently asked questions about the company.

You should be helpful, friendly, and concise. If you don't know the answer to a question, say so honestly and suggest the user contact support.

Common topics you handle:
- Store hours: Monday-Saturday 10am-6pm, Sunday 12pm-5pm
- Location: 3640 Boston Street, Baltimore, MD 21224
- Return policy: 30 days with receipt, items must be unused
- Shipping: Free shipping on orders over $100, standard 5-7 business days
- Payment: We accept all major credit cards, PayPal, and financing options
- Contact: info@gavigans.com or (410) 276-1714

Always maintain a professional and friendly tone.""",
        "tools": []
    },
    {
        "name": "product_agent", 
        "model": "gemini-2.0-flash",
        "description": "Helps users find products, check availability, compare items, and get product details and recommendations.",
        "instruction": """You are the Product Agent for Gavigans. You help users find and learn about furniture products.

IMPORTANT: Use the search_products tool to look up products when users ask about specific items.

Your responsibilities:
- Help users search for specific products using the search_products tool
- Present the results in a clear, readable format
- Compare products when asked
- Make product recommendations based on user needs
- Answer questions about product specifications

Always call the tool first when users ask about products, then present the results.""",
        "tools": ["search_products"]
    },
    {
        "name": "ticketing_agent",
        "model": "gemini-2.0-flash", 
        "description": "Manages support tickets — creates new tickets for issues, checks ticket status, and helps resolve customer problems.",
        "instruction": """You are the Ticketing Agent for Gavigans. You help users with support tickets and issue resolution.

IMPORTANT: Use the create_ticket tool to actually create tickets.

Your workflow:
1. Listen to the customer's issue
2. Gather necessary information: what happened, their name, email, and phone (ask if not provided)
3. Determine an appropriate title, description, and priority
4. Confirm the details with the customer before creating
5. Call the create_ticket tool with the collected information
6. Report the result back to the customer

Priority guidelines:
- high: order not received, damaged items, billing errors
- medium: general complaints, returns, exchanges  
- low: questions, feedback, feature requests

Be empathetic and reassuring — users reaching out for support may be frustrated.""",
        "tools": ["create_ticket"]
    }
]


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

async def search_products(user_message: str) -> dict:
    """Search for products based on the user's query."""
    url = "https://client-aiprl-n8n.ltjed0.easypanel.host/webhook/895eb7ee-2a87-4e65-search-for-products"
    payload = {
        "User_message": user_message,
        "chat_history": "na",
        "Contact_ID": "na",
        "customer_email": "na"
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Search failed with status {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


async def create_ticket(
    title: str,
    description: str = "",
    customerName: str = "",
    customerEmail: str = "",
    customerPhone: str = "",
    priority: str = "medium",
    tags: str = ""
) -> dict:
    """Create a support ticket for a customer issue."""
    url = "https://gavigans-inbox.up.railway.app/api/tickets"
    headers = {
        "x-business-id": "gavigans",
        "Content-Type": "application/json"
    }
    payload = {
        "title": title,
        "description": description,
        "customerName": customerName,
        "customerEmail": customerEmail,
        "customerPhone": customerPhone,
        "priority": priority,
    }
    if tags:
        payload["tags"] = [t.strip() for t in tags.split(",")]
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                return {"success": True, "ticket": resp.json()}
            return {"success": False, "error": f"Failed with status {resp.status_code}", "details": resp.text}
    except Exception as e:
        return {"success": False, "error": str(e)}


TOOL_MAP = {
    "search_products": FunctionTool(search_products),
    "create_ticket": FunctionTool(create_ticket),
}


# =============================================================================
# BUILD MULTI-AGENT (no async DB needed)
# =============================================================================

def build_root_agent_sync(before_callback=None, after_callback=None) -> Agent:
    """
    Build multi-agent root with HARDCODED config.
    No database dependency - always works.
    """
    print("🔧 Building multi-agent from hardcoded config...")
    
    sub_agents = []
    for config in AGENTS_CONFIG:
        tools = [TOOL_MAP[t] for t in config["tools"] if t in TOOL_MAP]
        print(f"   → {config['name']}: {len(tools)} tools")
        
        agent = Agent(
            name=config["name"],
            model=config["model"],
            description=config["description"],
            instruction=config["instruction"],
            tools=tools if tools else None,
        )
        sub_agents.append(agent)
    
    agent_list = "\n".join(
        f"- {config['name']}: {config['description']}" 
        for config in AGENTS_CONFIG
    )
    
    root_instruction = f"""You are the main routing agent for Gavigans Furniture. Your job is to understand the user's question and delegate it to the most appropriate specialist agent.

Available agents:
{agent_list}

ROUTING RULES:
- Questions about store hours, location, returns, shipping, payment → faq_agent
- Questions about products, furniture, availability, recommendations → product_agent  
- Issues, complaints, problems, need to create a ticket → ticketing_agent

Analyze the user's message and transfer to the most appropriate agent.
If the question is very general or doesn't fit any agent, respond directly with a helpful message."""

    root = Agent(
        name="gavigans_agent",
        model="gemini-2.0-flash",
        description="Gavigans multi-agent orchestrator",
        instruction=root_instruction,
        sub_agents=sub_agents,
        before_agent_callback=before_callback,
        after_agent_callback=after_callback,
    )
    
    print(f"✅ Multi-agent root built with {len(sub_agents)} sub-agents:")
    for sa in sub_agents:
        print(f"   • {sa.name}")
    
    return root


# Keep async version for compatibility but make it just call sync
async def build_root_agent(before_callback=None, after_callback=None) -> Agent:
    """Async wrapper for compatibility."""
    return build_root_agent_sync(before_callback, after_callback)
