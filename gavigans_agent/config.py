"""
Gavigans Agent Configuration
=============================
Central configuration for memory, summarization, and session management.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Google API Key for Gemini
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# ============================================================================
# MEMORY & SUMMARIZATION SETTINGS
# ============================================================================

# Summarize older events when total event count exceeds this
MEMORY_SUMMARIZATION_THRESHOLD = 40

# Keep this many recent events as raw context (not summarized)
MEMORY_KEEP_RECENT_EVENTS = 10

# Model used for summarization calls (fast + cheap)
SUMMARIZATION_MODEL = "gemini-2.0-flash"

# Rough chars-per-token for token estimation
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4

# Maximum tokens worth of history to inject as context
MAX_CONTEXT_TOKENS = 8000

# ============================================================================
# 90-DAY TTL POLICY
# ============================================================================

# Sessions older than this (in days) get summarized and deleted
SESSION_TTL_DAYS = 90

# How often the TTL cleanup task runs (in seconds) — every 24 hours
TTL_CLEANUP_INTERVAL_SECONDS = 86400

# ============================================================================
# CROSS-SESSION MEMORY
# ============================================================================

# State key for conversation summary within a session
STATE_KEY_CONVERSATION_SUMMARY = "conversation_summary"

# State key for summary event count (how many events the summary covers)
STATE_KEY_SUMMARY_EVENT_COUNT = "summary_event_count"

# State key for user-scoped summary (persists across sessions via user: prefix)
STATE_KEY_USER_SUMMARY = "user:conversation_summary"

# State key for timestamp of last summarization
STATE_KEY_LAST_SUMMARIZED_AT = "last_summarized_at"

# ADK app name (must match what main.py uses)
ADK_APP_NAME = "gavigans_agent"
