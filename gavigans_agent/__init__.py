"""
Gavigans Multi-Agent Platform
=============================
Root agent with Inbox integration callbacks and memory management.
"""

from .agent import root_agent, create_gavigans_agent, set_session_service
from .memory import (
    summarize_conversation,
    maybe_summarize_session,
    build_memory_context,
    load_cross_session_memory,
    get_session_memory_info,
    cleanup_expired_sessions,
)

__all__ = [
    'root_agent',
    'create_gavigans_agent',
    'set_session_service',
    'summarize_conversation',
    'maybe_summarize_session',
    'build_memory_context',
    'load_cross_session_memory',
    'get_session_memory_info',
    'cleanup_expired_sessions',
]
