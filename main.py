"""
Gavigans Agent Platform - Main Entry Point
==========================================
Monolithic deployment: Serves React frontend + FastAPI backend on single domain.

Architecture:
- /                                → React SPA (frontend/dist)
- /apps/gavigans_agent/*           → ADK Agent API
- /api/inbox/*                     → Inbox Integration API
- /assets/*                        → React static files (auto-mounted)

Based on ADK-Woodstock architecture for Chatrace-Inbox integration.
"""
import os
import asyncio
import logging
import uvicorn
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv(override=True)

# Prisma needs postgresql:// (not postgresql+asyncpg://). ADK needs postgresql+asyncpg://.
_raw_db_url = os.environ.get("DATABASE_URL")
if _raw_db_url:
    SESSION_DB_URL = _raw_db_url.replace("postgres://", "postgresql+asyncpg://").replace("sslmode=require", "ssl=require")
    if "postgresql+asyncpg" in _raw_db_url:
        _prisma_url = _raw_db_url.replace("postgresql+asyncpg://", "postgresql://").replace("ssl=require", "sslmode=require")
        os.environ["DATABASE_URL"] = _prisma_url
else:
    SESSION_DB_URL = None

# =============================================================================
# CRITICAL: Monkey-patch ADK's PreciseTimestamp BEFORE any ADK imports
# =============================================================================
# The ADK's PreciseTimestamp type uses DateTime (TIMESTAMP WITHOUT TIME ZONE)
# but passes timezone-aware datetimes (datetime.now(timezone.utc)).
# asyncpg rejects this mismatch. Fix: make PostgreSQL use TIMESTAMP WITH TIME ZONE.
try:
    from google.adk.sessions.schemas.shared import PreciseTimestamp
    from sqlalchemy.types import DateTime

    _original_load_dialect_impl = PreciseTimestamp.load_dialect_impl

    def _patched_load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(DateTime(timezone=True))
        return _original_load_dialect_impl(self, dialect)

    PreciseTimestamp.load_dialect_impl = _patched_load_dialect_impl
    print("🔧 Patched ADK PreciseTimestamp → TIMESTAMP WITH TIME ZONE for PostgreSQL")
except ImportError:
    print("⚠️ ADK not installed yet - skipping timestamp patch")
# =============================================================================

# =============================================================================
# MULTI-AGENT: Build from hardcoded config (no DB dependency)
# =============================================================================
import gavigans_agent.agent as ga_module

_bootstrap_error = None  # Store error for debugging

try:
    from multi_agent_builder import build_root_agent_sync
    _root = build_root_agent_sync(
        before_callback=ga_module.before_agent_callback,
        after_callback=ga_module.after_agent_callback,
    )
    ga_module.root_agent = _root
    import gavigans_agent
    gavigans_agent.root_agent = _root
    print(f"✅ Multi-agent root loaded: {len(getattr(_root, 'sub_agents', []) or [])} sub-agents")
except Exception as e:
    import traceback
    _bootstrap_error = traceback.format_exc()
    print(_bootstrap_error)
    print(f"⚠️ Multi-agent bootstrap failed ({e}) - using single agent from module")
    _root = None  # fallback used

# =============================================================================

from google.adk.cli.fast_api import get_fast_api_app
from google.adk.sessions import DatabaseSessionService
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Get port from environment
PORT = int(os.environ.get("PORT", 8000))

# CORS configuration
ALLOWED_ORIGINS = [
    "https://gavigans.demo.aiprlassist.com",  # Production webchat
    "https://gavigans.inbox.aiprlassist.com",  # Production Inbox
    "https://gaviganshf.inbox.aiprlassist.com",  # Production Inbox (alternate domain)
    "https://splendid-sparkle-production-8673.up.railway.app",  # Gavigans Inbox (Railway)
    "https://frontend-production-43b8.up.railway.app",  # Inbox frontend (Railway)
    "https://www.gaviganshomefurnishings.com",  # Gavigans main website
    "https://gaviganshomefurnishings.com",  # Gavigans (no www)
    "https://dynamiccode-ochre.vercel.app",  # Widget hosting
    "http://localhost:5173",  # Local dev (Vite)
    "http://localhost:3000",  # Local dev
    "http://localhost:8000",  # Local backend
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]

# CRITICAL: Session pool settings to fix "connection is closed" errors!
SESSION_DB_KWARGS = {
    "pool_pre_ping": True,           # Test connection before using
    "pool_recycle": 300,             # Recycle connections every 5 minutes
    "pool_size": 5,                  # Keep 5 connections in pool
    "max_overflow": 10,              # Allow 10 extra during bursts
    "pool_timeout": 30,              # Wait 30s for connection
    "echo": False,                   # Don't log SQL
    "connect_args": {"statement_cache_size": 0},  # Disable for PgBouncer
}

# Create the FastAPI app with persistent sessions
app = get_fast_api_app(
    agents_dir=".",
    web=False,
    allow_origins=ALLOWED_ORIGINS,
    session_service_uri=SESSION_DB_URL,
    session_db_kwargs=SESSION_DB_KWARGS,
)


# =============================================================================
# IFRAME EMBEDDING: Allow this app to be embedded in iframes from any domain
# =============================================================================
from starlette.middleware.base import BaseHTTPMiddleware

class IframeAllowMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Remove X-Frame-Options if present (blocks iframes)
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
        # Set CSP to allow embedding from anywhere
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

app.add_middleware(IframeAllowMiddleware)
print("✅ Iframe embedding enabled (frame-ancestors *)")


def _tool_names(agent) -> list[str]:
    """Get tool names from an agent (works with FunctionTool and similar)."""
    tools = getattr(agent, "tools", None) or []
    names = []
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "_name", None)
        if name:
            names.append(name)
        else:
            # FunctionTool often exposes the underlying function
            func = getattr(t, "func", None) or getattr(t, "_func", None)
            names.append(getattr(func, "__name__", "?") if func else "?")
    return names


@app.get("/debug/multi-agent", include_in_schema=False)
def debug_multi_agent():
    """Verify multi-agent loaded and show tools per agent (root has 0 tools by design; sub-agents have tools)."""
    import gavigans_agent.agent as ga
    root = getattr(ga, "root_agent", None)
    if not root:
        return {"multi_agent": False, "reason": "no root_agent", "bootstrap_ran": _root is not None}
    sub = getattr(root, "sub_agents", None) or []
    # Check if this is the dynamically built root or the default one
    is_default = root.description == "Gavigans multi-agent platform AI assistant"
    root_tools = _tool_names(root)
    sub_agents_with_tools = [
        {"name": a.name, "tools": _tool_names(a)}
        for a in sub
    ]
    return {
        "multi_agent": len(sub) > 0,
        "sub_agents": len(sub),
        "names": [a.name for a in sub],
        "root_description": root.description,
        "root_tools": root_tools,
        "sub_agents_tools": sub_agents_with_tools,
        "is_dynamic": not is_default,
        "bootstrap_result": "success" if _root else "failed",
        "bootstrap_error": _bootstrap_error[:2000] if _bootstrap_error else None
    }


@app.get("/debug/bootstrap-retry", include_in_schema=False)
async def debug_bootstrap_retry():
    """Manually retry the multi-agent bootstrap and return error details."""
    import os
    global _root, _bootstrap_error
    
    try:
        from multi_agent_builder import build_root_agent
        import gavigans_agent.agent as ga_module
        
        # Try to build directly (we're already in an async context)
        new_root = await build_root_agent(
            before_callback=ga_module.before_agent_callback,
            after_callback=ga_module.after_agent_callback,
        )
        
        # If successful, update the global root
        ga_module.root_agent = new_root
        import gavigans_agent
        gavigans_agent.root_agent = new_root
        _root = new_root
        _bootstrap_error = None
        
        sub_agents = getattr(new_root, 'sub_agents', []) or []
        return {
            "success": True,
            "sub_agents": len(sub_agents),
            "names": [a.name for a in sub_agents],
            "db_url_prefix": os.environ.get("DATABASE_URL", "NOT SET")[:50]
        }
    except Exception as e:
        import traceback
        error = traceback.format_exc()
        return {
            "success": False,
            "error": str(e),
            "traceback": error,
            "db_url_prefix": os.environ.get("DATABASE_URL", "NOT SET")[:50]
        }


# --- INBOX Integration ---
# Mount inbox router for /api/inbox/* endpoints
if SESSION_DB_URL:
    from inbox_router import create_inbox_router, router as inbox_router
    
    # Create session service for inbox router with SAME pool settings
    session_service = DatabaseSessionService(
        db_url=SESSION_DB_URL,
        **SESSION_DB_KWARGS
    )
    create_inbox_router(session_service, app_name="gavigans_agent")
    app.include_router(inbox_router)
    
    # 🧠 Wire session_service into agent callbacks for cross-session memory
    try:
        import gavigans_agent.agent as _ga_ref
        _ga_ref.set_session_service(session_service)
        print("✅ Session service wired to agent callbacks (cross-session memory enabled)")
    except Exception as _wire_err:
        print(f"⚠️ Failed to wire session_service to agent: {_wire_err}")
    
    print("✅ Using DatabaseSessionService (PostgreSQL)")
    print(f"   Pool settings: pre_ping={SESSION_DB_KWARGS['pool_pre_ping']}, recycle={SESSION_DB_KWARGS['pool_recycle']}s")
    print("✅ INBOX API mounted at /api/inbox/*")
    
    # ========================================================================
    # 🔍 DEBUG: Memory Introspection Endpoint
    # ========================================================================
    
    @app.get("/debug/memory/{conversation_id}", include_in_schema=False)
    async def debug_memory(conversation_id: str, user_id: str = "default"):
        """
        Inspect what the agent "remembers" for a given conversation.
        
        Returns: state, event count, token estimate, summary, session age.
        Usage: GET /debug/memory/<conversation_id>?user_id=default
        """
        from gavigans_agent.memory import get_session_memory_info
        
        try:
            session = await session_service.get_session(
                app_name="gavigans_agent",
                user_id=user_id,
                session_id=conversation_id,
            )
            if not session:
                return {"error": "Session not found", "conversation_id": conversation_id}
            
            return get_session_memory_info(session)
        except Exception as e:
            return {"error": str(e), "conversation_id": conversation_id}
    
    
    @app.get("/debug/memory", include_in_schema=False)
    async def debug_memory_all():
        """
        List all sessions with their memory status (summary overview).
        Usage: GET /debug/memory
        """
        from gavigans_agent.memory import get_session_memory_info
        
        try:
            response = await session_service.list_sessions(
                app_name="gavigans_agent",
                user_id=None,
            )
            if not response or not response.sessions:
                return {"sessions": [], "total": 0}
            
            summaries = []
            for s in response.sessions:
                state = s.state if hasattr(s, "state") and s.state else {}
                summaries.append({
                    "conversation_id": getattr(s, "id", None),
                    "user_id": getattr(s, "user_id", None),
                    "has_summary": bool(state.get("conversation_summary", "")),
                    "message_count": state.get("message_count", 0),
                    "ai_paused": state.get("ai_paused", False),
                    "last_message": state.get("last_message_preview", None),
                })
            
            return {"sessions": summaries, "total": len(summaries)}
        except Exception as e:
            return {"error": str(e)}
    
    
    # ========================================================================
    # 🧹 90-DAY TTL: Background Cleanup Task
    # ========================================================================
    
    _ttl_task = None
    
    async def _ttl_cleanup_loop():
        """Background loop that runs TTL cleanup every 24 hours."""
        from gavigans_agent.memory import cleanup_expired_sessions
        from gavigans_agent.config import TTL_CLEANUP_INTERVAL_SECONDS
        
        _logger = logging.getLogger("ttl_cleanup")
        _logger.info("🧹 TTL cleanup background task started (interval: %ds)", TTL_CLEANUP_INTERVAL_SECONDS)
        
        # Wait 60 seconds after startup before first run
        await asyncio.sleep(60)
        
        while True:
            try:
                _logger.info("🧹 Running 90-day TTL cleanup...")
                stats = await cleanup_expired_sessions(session_service)
                _logger.info("🧹 TTL cleanup result: %s", stats)
            except asyncio.CancelledError:
                _logger.info("🧹 TTL cleanup task cancelled")
                break
            except Exception as e:
                _logger.error("❌ TTL cleanup error: %s", e)
            
            await asyncio.sleep(TTL_CLEANUP_INTERVAL_SECONDS)
    
    
    @app.on_event("startup")
    async def start_ttl_cleanup():
        """Start the TTL cleanup background task on app startup."""
        global _ttl_task
        _ttl_task = asyncio.create_task(_ttl_cleanup_loop())
        print("✅ 90-day TTL cleanup background task scheduled")
    
    
    @app.on_event("shutdown")
    async def stop_ttl_cleanup():
        """Cancel the TTL cleanup background task on shutdown."""
        global _ttl_task
        if _ttl_task:
            _ttl_task.cancel()
            print("🧹 TTL cleanup background task stopped")
    
    
    @app.get("/debug/ttl-cleanup", include_in_schema=False)
    async def debug_ttl_cleanup():
        """
        Manually trigger the 90-day TTL cleanup (for testing/debugging).
        Usage: GET /debug/ttl-cleanup
        """
        from gavigans_agent.memory import cleanup_expired_sessions
        
        try:
            stats = await cleanup_expired_sessions(session_service)
            return {"status": "completed", **stats}
        except Exception as e:
            return {"status": "error", "error": str(e)}

else:
    session_service = None
    print("⚠️  No DATABASE_URL - InMemorySessionService (non-persistent)")
    print("⚠️  INBOX API disabled (requires database)")
    print("⚠️  Memory features disabled (requires database)")

# ============================================================================
# SERVE REACT FRONTEND (Monolithic Architecture)
# ============================================================================

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
FRONTEND_DIR = FRONTEND_DIR.resolve()

if FRONTEND_DIR.exists():
    print("✅ Frontend build found - Serving React SPA")
    print(f"🔒 Frontend root (absolute): {FRONTEND_DIR}")
    
    # Mount static assets (JS, CSS, images)
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")
        print(f"✅ Mounted /assets → {assets_dir}")
    
    # Catch-all route for React Router (SPA)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        """
        Serve React SPA for all non-API routes.
        """
        from fastapi import HTTPException
        
        # SECURITY: Block path traversal attempts
        if ".." in full_path or full_path.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid path")
        
        # Don't serve SPA for API routes
        if full_path.startswith("apps/") or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        
        # Serve index.html for SPA
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(
                index_file,
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "frame-ancestors *",
                    "Referrer-Policy": "strict-origin-when-cross-origin",
                }
            )
        
        raise HTTPException(status_code=404, detail="Frontend not built")
    
    print("✅ SPA catch-all route configured")
else:
    print("⚠️  Frontend not built yet - Run 'cd frontend && npm install && npm run build'")
    print("⚠️  API-only mode - Frontend will 404")

# ============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
