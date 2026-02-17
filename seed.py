import asyncio

from prisma import Json

from app.auth.utils import hash_password
from app.db import db


async def seed():
    await db.connect()

    # Create a default admin user
    user = await db.user.upsert(
        where={"email": "admin@gavigans.com"},
        data={
            "create": {
                "email": "admin@gavigans.com",
                "passwordHash": hash_password("changeme123"),
                "name": "Admin",
            },
            "update": {},
        },
    )
    print(f"User: {user.email} (id: {user.id})")

    agents_data = [
        {
            "name": "FAQ Agent",
            "model": "gemini-2.0-flash",
            "description": (
                "Handles frequently asked questions about the company, policies, "
                "hours, returns, shipping, and general inquiries."
            ),
            "instruction": (
                "You are the FAQ Agent for Gavigans. You answer frequently asked questions "
                "about the company.\n\n"
                "You should be helpful, friendly, and concise. If you don't know the answer "
                "to a question, say so honestly and suggest the user contact support.\n\n"
                "Common topics you handle:\n"
                "- Store hours and locations\n"
                "- Return and refund policies\n"
                "- Shipping information and timelines\n"
                "- Payment methods accepted\n"
                "- General company information\n"
                "- Account-related questions\n\n"
                "Always maintain a professional and friendly tone."
            ),
            "tools": Json([]),
            "user": {"connect": {"id": user.id}},
        },
        {
            "name": "Product Fetching Agent",
            "model": "gemini-2.0-flash",
            "description": (
                "Helps users find products, check availability, compare items, "
                "and get product details and recommendations."
            ),
            "instruction": (
                "You are the Product Fetching Agent for Gavigans. You help users find and "
                "learn about products.\n\n"
                "IMPORTANT: Always use the search_products tool to look up products. "
                "Pass the user's request as the user_message parameter.\n\n"
                "Your responsibilities:\n"
                "- Help users search for specific products using the search_products tool\n"
                "- Present the results in a clear, readable format\n"
                "- Compare products when asked\n"
                "- Make product recommendations based on user needs\n"
                "- Answer questions about product specifications\n\n"
                "Always call the tool first, then present the results to the user."
            ),
            "tools": Json([
                {
                    "type": "webhook",
                    "name": "search_products",
                    "description": (
                        "Search for products based on the user's query. "
                        "Call this whenever the user asks about products, wants to browse, "
                        "or search for specific items."
                    ),
                    "url": "https://client-aiprl-n8n.ltjed0.easypanel.host/webhook/895eb7ee-2a87-4e65-search-for-products",
                    "method": "POST",
                    "body": {
                        "User_message": "{{message}}",
                        "chat_history": "na",
                        "Contact_ID": "na",
                        "customer_email": "na",
                    },
                }
            ]),
            "user": {"connect": {"id": user.id}},
        },
        {
            "name": "Ticketing Agent",
            "model": "gemini-2.0-flash",
            "description": (
                "Manages support tickets — creates new tickets for issues, checks ticket "
                "status, and helps resolve customer problems."
            ),
            "instruction": (
                "You are the Ticketing Agent for Gavigans. You help users with support "
                "tickets and issue resolution.\n\n"
                "IMPORTANT: Use the create_ticket tool to actually create tickets.\n\n"
                "Your workflow:\n"
                "1. Listen to the customer's issue\n"
                "2. Gather the necessary information: what happened, their name, "
                "email, and phone (ask if not provided)\n"
                "3. Determine an appropriate title, description, and priority\n"
                "4. Confirm the details with the customer before creating\n"
                "5. Call the create_ticket tool with the collected information\n"
                "6. Report the result back to the customer\n\n"
                "Priority guidelines:\n"
                "- high: order not received, damaged items, billing errors\n"
                "- medium: general complaints, returns, exchanges\n"
                "- low: questions, feedback, feature requests\n\n"
                "Be empathetic and reassuring — users reaching out for support "
                "may be frustrated."
            ),
            "tools": Json([
                {
                    "type": "rest_api",
                    "name": "create_ticket",
                    "description": (
                        "Create a support ticket for a customer issue. "
                        "Call this after collecting the necessary details from the customer."
                    ),
                    "url": "https://gavigans-inbox.up.railway.app/api/tickets",
                    "method": "POST",
                    "headers": {
                        "x-business-id": "gavigans",
                        "Content-Type": "application/json",
                    },
                    "parameters": [
                        {
                            "name": "title",
                            "type": "string",
                            "description": "Short summary of the customer's issue",
                            "required": True,
                        },
                        {
                            "name": "description",
                            "type": "string",
                            "description": "Detailed description of the problem or request",
                        },
                        {
                            "name": "customerName",
                            "type": "string",
                            "description": "The customer's full name",
                        },
                        {
                            "name": "customerEmail",
                            "type": "string",
                            "description": "The customer's email address",
                        },
                        {
                            "name": "customerPhone",
                            "type": "string",
                            "description": "The customer's phone number",
                        },
                        {
                            "name": "priority",
                            "type": "string",
                            "description": "Priority level: 'low', 'medium', or 'high'",
                            "default": "medium",
                        },
                        {
                            "name": "tags",
                            "type": "list",
                            "description": "Comma-separated tags, e.g. 'shipping,damage'",
                        },
                    ],
                }
            ]),
            "user": {"connect": {"id": user.id}},
        },
    ]

    for agent_data in agents_data:
        existing = await db.agent.find_first(
            where={"name": agent_data["name"], "userId": user.id}  # type: ignore
        )
        if existing:
            # Update existing agent with latest config (tools, instruction, etc.)
            update_fields = {
                "instruction": agent_data["instruction"],
                "description": agent_data["description"],
                "tools": agent_data["tools"],
            }
            await db.agent.update(where={"id": existing.id}, data=update_fields)
            print(f"Updated agent: {agent_data['name']}")
            continue

        agent = await db.agent.create(data=agent_data)
        print(f"Created agent: {agent.name} (id: {agent.id})")

    await db.disconnect()
    print("\nSeed completed!")


if __name__ == "__main__":
    asyncio.run(seed())
