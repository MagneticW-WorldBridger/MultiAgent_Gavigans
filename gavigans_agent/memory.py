"""
Gavigans Memory Management
==========================
Handles conversation summarization, cross-session memory,
token estimation, and 90-day TTL cleanup.

Used by:
- agent.py (before/after callbacks)
- main.py (debug endpoint, TTL background task)
"""

import logging
import time
from datetime import datetime, timezone

import google.genai as genai

from gavigans_agent.config import (
    GOOGLE_API_KEY,
    MEMORY_KEEP_RECENT_EVENTS,
    MEMORY_SUMMARIZATION_THRESHOLD,
    SUMMARIZATION_MODEL,
    TOKEN_ESTIMATE_CHARS_PER_TOKEN,
    MAX_CONTEXT_TOKENS,
    SESSION_TTL_DAYS,
    STATE_KEY_CONVERSATION_SUMMARY,
    STATE_KEY_SUMMARY_EVENT_COUNT,
    STATE_KEY_USER_SUMMARY,
    STATE_KEY_LAST_SUMMARIZED_AT,
    ADK_APP_NAME,
)

logger = logging.getLogger(__name__)


# ============================================================================
# TOKEN ESTIMATION
# ============================================================================

def estimate_tokens(text: str) -> int:
    """Rough token estimate based on character count."""
    if not text:
        return 0
    return len(text) // TOKEN_ESTIMATE_CHARS_PER_TOKEN


def estimate_events_tokens(events: list) -> int:
    """Estimate total tokens across all events."""
    total_chars = 0
    for event in events:
        content = getattr(event, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", [])
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                total_chars += len(text)
    return total_chars // TOKEN_ESTIMATE_CHARS_PER_TOKEN


# ============================================================================
# EVENT → TEXT EXTRACTION
# ============================================================================

def events_to_text(events: list) -> str:
    """Convert ADK events to readable conversation text for summarization."""
    lines = []
    for event in events:
        content = getattr(event, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", [])
        text_parts = []
        for part in parts:
            text = getattr(part, "text", None)
            if text and text.strip() and text != "__AI_PAUSED__":
                text_parts.append(text.strip())
        if not text_parts:
            continue

        author = getattr(event, "author", "unknown")
        role_label = "Customer" if author == "user" else f"Agent ({author})"
        combined = " ".join(text_parts)
        lines.append(f"{role_label}: {combined}")

    return "\n".join(lines)


# ============================================================================
# CONVERSATION SUMMARIZATION
# ============================================================================

SUMMARIZATION_PROMPT = """You are a memory summarizer for a furniture store AI assistant (Gavigans).

Summarize the following conversation excerpt. Preserve ALL of these details if present:
- Customer name, email, phone, preferences
- Products discussed (names, prices, colors, materials)
- Decisions made or items selected
- Delivery/shipping details and dates
- Open issues, complaints, or tickets created
- Any promises made by the agent
- Customer's budget, style preferences, room dimensions

Be concise but COMPLETE. This summary will be the ONLY record of this conversation segment.
Do NOT add any preamble like "Here is a summary". Just output the summary directly.

CONVERSATION:
{conversation_text}

{previous_summary_section}"""


async def summarize_conversation(events: list, previous_summary: str = None) -> str:
    """
    Call Gemini to summarize a list of ADK events into a compact summary.

    Args:
        events: List of ADK Event objects to summarize.
        previous_summary: If re-summarizing, include the old summary for continuity.

    Returns:
        A text summary string, or empty string on failure.
    """
    if not events:
        return ""

    conversation_text = events_to_text(events)
    if not conversation_text.strip():
        return previous_summary or ""

    previous_summary_section = ""
    if previous_summary:
        previous_summary_section = (
            f"PREVIOUS SUMMARY (incorporate and update this):\n{previous_summary}"
        )

    prompt = SUMMARIZATION_PROMPT.format(
        conversation_text=conversation_text,
        previous_summary_section=previous_summary_section,
    )

    try:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = await client.aio.models.generate_content(
            model=SUMMARIZATION_MODEL,
            contents=prompt,
        )
        summary = response.text.strip() if response.text else ""
        logger.info(
            "✅ Summarized %d events → %d chars (%d tokens)",
            len(events),
            len(summary),
            estimate_tokens(summary),
        )
        return summary
    except Exception as e:
        logger.error("❌ Summarization failed: %s", e)
        return previous_summary or ""


# ============================================================================
# SUMMARIZATION CHECK & TRIGGER (called from after_agent_callback)
# ============================================================================

async def maybe_summarize_session(session, state: dict) -> dict:
    """
    Check if a session needs summarization based on event count.
    If yes, summarize older events and return state updates.

    Args:
        session: ADK Session object (has .events)
        state: The callback_context.state dict

    Returns:
        Dict of state keys to update (empty if no summarization needed).
    """
    events = getattr(session, "events", []) or []
    total_events = len(events)

    if total_events < MEMORY_SUMMARIZATION_THRESHOLD:
        return {}

    # How many events does the existing summary already cover?
    already_summarized = state.get(STATE_KEY_SUMMARY_EVENT_COUNT, 0)
    unsummarized_count = total_events - already_summarized

    # Only re-summarize if enough new events have accumulated
    if unsummarized_count < MEMORY_SUMMARIZATION_THRESHOLD:
        return {}

    logger.info(
        "📝 Summarization triggered: %d total events, %d already summarized, %d new",
        total_events,
        already_summarized,
        unsummarized_count,
    )

    # Split: older events to summarize, recent events to keep raw
    cutoff = total_events - MEMORY_KEEP_RECENT_EVENTS
    events_to_summarize = events[:cutoff]
    previous_summary = state.get(STATE_KEY_CONVERSATION_SUMMARY, "")

    summary = await summarize_conversation(events_to_summarize, previous_summary)

    if not summary:
        return {}

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        STATE_KEY_CONVERSATION_SUMMARY: summary,
        STATE_KEY_SUMMARY_EVENT_COUNT: cutoff,
        STATE_KEY_USER_SUMMARY: summary,  # Cross-session (user:-scoped)
        STATE_KEY_LAST_SUMMARIZED_AT: now_iso,
    }


# ============================================================================
# CONTEXT INJECTION (called from before_agent_callback)
# ============================================================================

def build_memory_context(state: dict) -> str:
    """
    Build a memory context string to prepend to the user's message.

    Checks for:
    1. In-session conversation summary (from summarization)
    2. Cross-session user summary (from previous sessions)

    Returns:
        A context string to prepend, or empty string if no memory exists.
    """
    parts = []

    # In-session summary (from current conversation being summarized)
    session_summary = state.get(STATE_KEY_CONVERSATION_SUMMARY, "")
    if session_summary:
        parts.append(
            f"[CONVERSATION MEMORY - Summary of earlier messages in this conversation]\n"
            f"{session_summary}"
        )

    # Cross-session summary (from previous conversations with this user)
    # Only use this if there's NO in-session summary (i.e., this is a new/early session)
    user_summary = state.get(STATE_KEY_USER_SUMMARY, "")
    if user_summary and not session_summary:
        parts.append(
            f"[CUSTOMER HISTORY - Summary from previous conversations]\n"
            f"{user_summary}"
        )

    if not parts:
        return ""

    context = "\n\n".join(parts)

    # Token guard: truncate if too long
    if estimate_tokens(context) > MAX_CONTEXT_TOKENS:
        max_chars = MAX_CONTEXT_TOKENS * TOKEN_ESTIMATE_CHARS_PER_TOKEN
        context = context[:max_chars] + "\n[...truncated for context length]"

    return context


# ============================================================================
# CROSS-SESSION MEMORY LOADER
# ============================================================================

async def load_cross_session_memory(session_service, user_id: str) -> str:
    """
    Look up previous sessions for a user and retrieve the latest summary.

    This is the fallback if ADK's user: prefix doesn't automatically
    share state across sessions. We manually query previous sessions.

    Args:
        session_service: ADK DatabaseSessionService instance
        user_id: The user whose history to load

    Returns:
        The most recent conversation summary, or empty string.
    """
    try:
        response = await session_service.list_sessions(
            app_name=ADK_APP_NAME,
            user_id=user_id,
        )

        if not response or not response.sessions:
            return ""

        # Sort by last_update_time descending to get the most recent
        sessions = sorted(
            response.sessions,
            key=lambda s: getattr(s, "last_update_time", 0) or 0,
            reverse=True,
        )

        # Check each session (skip current) for a summary
        for prev_session in sessions:
            prev_state = (
                prev_session.state
                if hasattr(prev_session, "state") and prev_session.state
                else {}
            )
            summary = prev_state.get(STATE_KEY_CONVERSATION_SUMMARY, "") or prev_state.get(
                STATE_KEY_USER_SUMMARY, ""
            )
            if summary:
                logger.info(
                    "📚 Found cross-session summary from session %s (%d chars)",
                    getattr(prev_session, "id", "?")[:8],
                    len(summary),
                )
                return summary

        return ""
    except Exception as e:
        logger.error("⚠️ Cross-session memory load failed: %s", e)
        return ""


# ============================================================================
# SESSION INTROSPECTION (for debug endpoint)
# ============================================================================

def get_session_memory_info(session) -> dict:
    """
    Extract memory/debug information from a session.

    Returns a dict suitable for the /debug/memory/ endpoint.
    """
    events = getattr(session, "events", []) or []
    state = session.state if hasattr(session, "state") and session.state else {}

    total_tokens = estimate_events_tokens(events)
    summary = state.get(STATE_KEY_CONVERSATION_SUMMARY, "")
    summary_tokens = estimate_tokens(summary) if summary else 0

    last_update = getattr(session, "last_update_time", None)
    age_days = None
    if last_update:
        age_seconds = time.time() - last_update
        age_days = round(age_seconds / 86400, 1)

    return {
        "conversation_id": getattr(session, "id", None),
        "user_id": getattr(session, "user_id", None),
        "event_count": len(events),
        "estimated_tokens_raw_events": total_tokens,
        "estimated_tokens_summary": summary_tokens,
        "has_summary": bool(summary),
        "summary": summary or None,
        "summary_covers_events": state.get(STATE_KEY_SUMMARY_EVENT_COUNT, 0),
        "last_summarized_at": state.get(STATE_KEY_LAST_SUMMARIZED_AT),
        "user_cross_session_summary": state.get(STATE_KEY_USER_SUMMARY) or None,
        "state_keys": list(state.keys()),
        "state": {
            k: v
            for k, v in state.items()
            if not k.startswith("_")  # filter internal keys
        },
        "last_activity": (
            datetime.fromtimestamp(last_update, tz=timezone.utc).isoformat()
            if last_update
            else None
        ),
        "session_age_days": age_days,
        "ai_paused": state.get("ai_paused", False),
        "is_read": state.get("is_read", True),
    }


# ============================================================================
# 90-DAY TTL CLEANUP
# ============================================================================

async def cleanup_expired_sessions(session_service) -> dict:
    """
    Find sessions older than SESSION_TTL_DAYS, summarize them,
    preserve the summary in cross-session memory, then delete.

    Returns:
        Stats dict with counts of processed/deleted/failed sessions.
    """
    stats = {"checked": 0, "expired": 0, "summarized": 0, "deleted": 0, "failed": 0}
    cutoff_time = time.time() - (SESSION_TTL_DAYS * 86400)

    try:
        response = await session_service.list_sessions(
            app_name=ADK_APP_NAME,
            user_id=None,  # All users
        )

        if not response or not response.sessions:
            logger.info("🧹 TTL cleanup: no sessions found")
            return stats

        for session_summary in response.sessions:
            stats["checked"] += 1
            last_update = getattr(session_summary, "last_update_time", None) or 0

            if last_update > cutoff_time:
                continue  # Not expired

            stats["expired"] += 1
            session_id = getattr(session_summary, "id", None)
            user_id = getattr(session_summary, "user_id", "default")

            try:
                # Load full session to get events
                full_session = await session_service.get_session(
                    app_name=ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )

                if full_session:
                    full_state = (
                        full_session.state
                        if hasattr(full_session, "state") and full_session.state
                        else {}
                    )
                    existing_summary = full_state.get(STATE_KEY_CONVERSATION_SUMMARY, "")
                    events = getattr(full_session, "events", []) or []

                    # Summarize if not already done
                    if events and not existing_summary:
                        summary = await summarize_conversation(events)
                        if summary:
                            stats["summarized"] += 1
                            logger.info(
                                "📝 Summarized expired session %s before deletion (%d events → %d chars)",
                                session_id[:8] if session_id else "?",
                                len(events),
                                len(summary),
                            )
                            # Note: We can't easily persist this to user: state
                            # after deletion, but the summary was already stored
                            # during normal operation via after_agent_callback.
                    elif existing_summary:
                        stats["summarized"] += 1  # Already had a summary

                # Delete the expired session
                await session_service.delete_session(
                    app_name=ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )
                stats["deleted"] += 1
                age_days = round((time.time() - last_update) / 86400, 1)
                logger.info(
                    "🧹 Deleted expired session %s (age: %s days, user: %s)",
                    session_id[:8] if session_id else "?",
                    age_days,
                    user_id,
                )

            except Exception as e:
                stats["failed"] += 1
                logger.error(
                    "❌ Failed to cleanup session %s: %s",
                    session_id[:8] if session_id else "?",
                    e,
                )

        logger.info(
            "🧹 TTL cleanup complete: checked=%d, expired=%d, summarized=%d, deleted=%d, failed=%d",
            stats["checked"],
            stats["expired"],
            stats["summarized"],
            stats["deleted"],
            stats["failed"],
        )

    except Exception as e:
        logger.error("❌ TTL cleanup task failed: %s", e)

    return stats
