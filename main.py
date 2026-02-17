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
# =============================================================================
# CRITICAL: Enable nested asyncio BEFORE anything else
# This allows asyncio.run() to work even when uvicorn's event loop is running
# =============================================================================
import nest_asyncio
nest_asyncio.apply()
# =============================================================================

import os
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
# MULTI-AGENT: Build root from DB before ADK loads (all Gavigans, no auth)
# =============================================================================
import asyncio
import gavigans_agent.agent as ga_module

try:
    from multi_agent_builder import build_root_agent
    _root = asyncio.run(build_root_agent(
        before_callback=ga_module.before_agent_callback,
        after_callback=ga_module.after_agent_callback,
    ))
    ga_module.root_agent = _root
    import gavigans_agent
    gavigans_agent.root_agent = _root
    print("✅ Multi-agent root loaded from DB")
except Exception as e:
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
    "https://frontend-production-43b8.up.railway.app",  # Inbox frontend (Railway)
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


@app.get("/debug/multi-agent", include_in_schema=False)
def debug_multi_agent():
    """Verify multi-agent loaded (sub_agents from DB)."""
    import gavigans_agent.agent as ga
    root = getattr(ga, "root_agent", None)
    if not root:
        return {"multi_agent": False, "reason": "no root_agent"}
    sub = getattr(root, "sub_agents", None) or []
    return {"multi_agent": True, "sub_agents": len(sub), "names": [a.name for a in sub]}


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
    
    print("✅ Using DatabaseSessionService (PostgreSQL)")
    print(f"   Pool settings: pre_ping={SESSION_DB_KWARGS['pool_pre_ping']}, recycle={SESSION_DB_KWARGS['pool_recycle']}s")
    print("✅ INBOX API mounted at /api/inbox/*")
else:
    print("⚠️  No DATABASE_URL - InMemorySessionService (non-persistent)")
    print("⚠️  INBOX API disabled (requires database)")

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
                    "X-Frame-Options": "SAMEORIGIN",
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
