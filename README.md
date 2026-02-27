# Gavigans — Multi-Agent AI Platform

A multi-agent AI assistant platform built with **Google ADK** (Agent Development Kit), **FastAPI**, **Prisma**, and **PostgreSQL**. Authenticated users can create and manage AI agents that are orchestrated by a root routing agent.

## Architecture

```
User Message
    |
Root Agent (gemini-2.0-flash)
    |--- FAQ Agent
    |--- Product Fetching Agent  (webhook tool)
    |--- Ticketing Agent         (REST API tool)
    |--- ... (user-created agents)
```

Every message starts from the root agent, which delegates to the most appropriate sub-agent. Sub-agents can have tools (webhook or structured REST API calls) that let them interact with external services.

## Prerequisites

- **Python 3.11+**
- **PostgreSQL** database
- **Google API Key** (Gemini)

## Setup

### 1. Clone and create virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
DATABASE_URL=postgresql://user:password@localhost:5432/gavigans
JWT_SECRET=your-secret-key-change-this
GOOGLE_API_KEY=your-google-api-key
```

### 4. Set up the database (local dev only)

**Production:** Schema is managed externally. No migrations or seed run on deploy.

**Local dev (manual, when authorized):**
```bash
python -m prisma generate
# prisma db push and seed.py are manual only - run when you need them
```

### 5. Seed the database (local dev, manual only)

Creates a default admin user and three starter agents. Run manually when needed:

```bash
python seed.py
```

**Default credentials:**
- Email: `admin@gavigans.com`
- Password: `changeme123`

### 6. Run the server

```bash
python -m uvicorn app.main:app --port 8000
```

The app will be available at `http://localhost:8000`.

## Usage

### Web Interface

- **`/`** — Sign in / create account
- **`/dashboard`** — Manage agents, chat with them, create/edit/delete agents

### API Endpoints

#### Authentication

| Method | Endpoint         | Description      |
|--------|------------------|------------------|
| POST   | `/auth/register` | Create account   |
| POST   | `/auth/login`    | Sign in (get JWT)|

#### Agents (requires Bearer token)

| Method | Endpoint       | Description       |
|--------|----------------|-------------------|
| GET    | `/agents`      | List all agents   |
| GET    | `/agents/{id}` | Get single agent  |
| POST   | `/agents`      | Create agent      |
| PATCH  | `/agents/{id}` | Update agent      |
| DELETE | `/agents/{id}` | Delete agent      |

#### Chat (requires Bearer token)

| Method | Endpoint                          | Description            |
|--------|-----------------------------------|------------------------|
| POST   | `/chat`                           | Send a message         |
| GET    | `/chat/conversations`             | List conversations     |
| GET    | `/chat/conversations/{id}`        | Get conversation       |

**Chat request:**
```json
{
  "message": "Show me black sofas",
  "conversation_id": null
}
```

## Tool Configuration

Agents can have tools configured as JSON. Two types are supported:

### Webhook Tool

Sends the user's message to an external endpoint via a template body:

```json
[
  {
    "type": "webhook",
    "name": "search_products",
    "description": "Search for products based on the user's query",
    "url": "https://example.com/webhook/search",
    "method": "POST",
    "body": {
      "User_message": "{{message}}",
      "chat_history": "na"
    }
  }
]
```

`{{message}}` is replaced with the user's input at runtime.

### REST API Tool

For structured API calls where the LLM extracts individual parameters:

```json
[
  {
    "type": "rest_api",
    "name": "create_ticket",
    "description": "Create a support ticket for a customer issue",
    "url": "https://example.com/api/tickets",
    "method": "POST",
    "headers": {
      "x-business-id": "my-business",
      "Content-Type": "application/json"
    },
    "parameters": [
      { "name": "title", "type": "string", "description": "Issue summary", "required": true },
      { "name": "description", "type": "string", "description": "Detailed description" },
      { "name": "priority", "type": "string", "description": "low, medium, or high", "default": "medium" },
      { "name": "tags", "type": "list", "description": "Comma-separated tags" }
    ]
  }
]
```

The LLM sees each parameter as a function argument and fills them from conversation context. Parameters with `"type": "list"` automatically split comma-separated strings into arrays.

## Project Structure

```
gavigans/
  app/
    main.py              # FastAPI app, routes, static files
    config.py            # Pydantic settings from .env
    db.py                # Prisma client singleton
    auth/
      router.py          # /auth/register, /auth/login
      schemas.py         # Request/response models
      utils.py           # JWT, bcrypt, get_current_user
    agents/
      router.py          # CRUD /agents
      schemas.py         # Agent models
    chat/
      router.py          # /chat, /chat/conversations
      service.py         # ADK agent orchestration
      tools.py           # Webhook & REST API tool builders
  prisma/
    schema.prisma        # Database schema
  static/
    index.html           # Login/register page
    dashboard.html       # Agent management + chat
    css/style.css        # Dark theme styles
    js/api.js            # API client with JWT handling
    js/dashboard.js      # Dashboard logic
  seed.py                # Database seeder
  requirements.txt
  .env.example
```
