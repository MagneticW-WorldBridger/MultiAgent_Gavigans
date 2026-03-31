"""
INBOX Integration Router
========================
REST API for ChatRace Inbox to query Woodstock conversations.

Inbox calls (via their proxy):
- GET  /api/inbox/conversations                    - List all conversations
- GET  /api/inbox/conversations/:id/messages       - Get messages for conversation
- POST /api/inbox/messages                         - Inject human agent message
- POST /api/inbox/conversations/:id/read           - Mark conversation as read
- POST /api/inbox/toggle-ai                        - Toggle AI on/off for conversation

Auth: Bearer token in Authorization header (WOODSTOCK_API_KEY)
Webhook (we send TO Inbox): POST {INBOX_WEBHOOK_URL}/webhook/woodstock

State Keys (persisted in session.state via ADK):
- is_read: bool - Whether conversation has been read by human agent
- ai_paused: bool - Whether AI responses are paused (human takeover)
- escalation_reason: str - Why AI was paused
- escalation_time: float - When escalation happened
"""

import os
import hmac
import hashlib
import httpx
import uuid
import time
import asyncio
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from google.genai import types as genai_types
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

# In-memory pub/sub for broadcasting messages to connected clients
_listeners = {}  # {conversation_id: [queue1, queue2, ...]}

# 🌐 GLOBAL listeners for sidebar updates (new conversations, unread counts)
_global_listeners = []  # [queue1, queue2, ...]

# API Key for authentication (Inbox sends this as Bearer token)
WOODSTOCK_API_KEY = os.environ.get("WOODSTOCK_API_KEY", "woodstock_api_key_2024")


# --- Auth ---

def verify_api_key(authorization: Optional[str] = Header(None)):
    """Verify Bearer token matches our API key."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    
    token = authorization[7:]  # Remove "Bearer "
    if token != WOODSTOCK_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return True


# --- Pydantic Models ---

class ConversationSummary(BaseModel):
    id: str
    user_id: str
    last_update: str
    message_count: int
    source: str = "woodstock"
    # 🆕 Message preview for sidebar
    last_message_text: Optional[str] = None  # Last message content
    last_incoming_message: Optional[str] = None  # Last user message
    last_outgoing_message: Optional[str] = None  # Last agent/bot message
    user_name: Optional[str] = None  # Display name for sidebar
    # 🆕 Read/Unread and AI status (persisted in session.state)
    is_read: bool = True  # Default to read for existing conversations
    ai_paused: bool = False  # AI is active by default
    escalation_reason: Optional[str] = None
    # 🎯 DynamicCode tracking integration
    widget_session_id: Optional[str] = None  # Links to dynamiccode tracking data


class Message(BaseModel):
    id: str
    author: str
    content: str
    timestamp: str
    type: str = "text"


class MessagesResponse(BaseModel):
    status: str = "OK"
    messages: list[Message]
    source: str = "woodstock"


class SendMessageRequest(BaseModel):
    conversation_id: str
    user_id: str
    message: str


class ToggleAIRequest(BaseModel):
    conversation_id: str
    user_id: str
    ai_enabled: bool


class MarkReadRequest(BaseModel):
    user_id: str


# --- Helpers ---

def event_to_message(event) -> Optional[Message]:
    """Extract text content from ADK Event.
    
    Filters out:
    - Empty messages
    - __AI_PAUSED__ markers (internal system messages)
    - System events without user-visible content
    """
    if not event.content or not event.content.parts:
        return None
    
    text_parts = [p.text for p in event.content.parts if hasattr(p, 'text') and p.text]
    if not text_parts:
        return None
    
    content = " ".join(text_parts).strip()
    
    # 🔥 FIX: Filter out __AI_PAUSED__ markers - these are internal system messages
    if content == '__AI_PAUSED__':
        return None
    
    # Filter out empty content
    if not content:
        return None
    
    # 🔥 FIX: Use UTC timestamp with "Z" suffix
    return Message(
        id=event.id,
        author=event.author,
        content=content,
        timestamp=datetime.fromtimestamp(event.timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        type="text"
    )


def _events_to_messages(events) -> list:
    """Convert ADK events to Messages, extracting products as CAROUSEL_DATA."""
    import json as _json

    out = []
    pending_products = []

    for e in events:
        if e.content and e.content.parts:
            for p in e.content.parts:
                fr = getattr(p, 'function_response', None)
                if fr:
                    resp = getattr(fr, 'response', None) or {}
                    if isinstance(resp, dict) and isinstance(resp.get('products'), list):
                        pending_products.extend(resp['products'])

        msg = event_to_message(e)
        if msg:
            if pending_products and msg.author != 'user':
                carousel_items = [{
                    "title": prod.get("name", ""),
                    "subtitle": prod.get("price_label", "") or (f"${prod['price']}" if prod.get("price") else "Contact Store"),
                    "image_url": prod.get("image_url", ""),
                    "url": prod.get("url", ""),
                } for prod in pending_products if prod.get("name")]
                if carousel_items:
                    msg.content += f"\n\n**CAROUSEL_DATA:** {_json.dumps(carousel_items)}"
                pending_products = []
            out.append(msg)
    return out


def session_to_summary(session) -> ConversationSummary:
    """Convert ADK Session to summary for list.
    
    🔥 FIX: Read from session.state (persisted by after_agent_callback).
    list_sessions does NOT load events, so we rely on state.
    """
    # Get state values with safe defaults
    state = session.state if hasattr(session, 'state') and session.state else {}
    is_read = state.get("is_read", True)
    ai_paused = state.get("ai_paused", False)
    escalation_reason = state.get("escalation_reason", None)
    
    # 🎯 Get widget_session_id for dynamiccode tracking integration
    widget_session_id = state.get("widget_session_id", None)
    
    # Format timestamp
    last_update_str = ""
    if session.last_update_time:
        last_update_str = datetime.fromtimestamp(session.last_update_time, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    
    # 🔥 Get last_message_text from state (persisted by after_agent_callback)
    last_message_text = state.get("last_message_preview") or None
    
    # 🔥 Get user_name from state if available
    user_name = state.get("user:full_name") or state.get("full_name") or None
    
    # Get message_count from state (persisted by after_agent_callback)
    message_count = state.get("message_count") or (len(session.events) if hasattr(session, 'events') else 0)
    
    return ConversationSummary(
        id=session.id,
        user_id=session.user_id,
        last_update=last_update_str,
        message_count=message_count,
        source="woodstock",
        last_message_text=last_message_text,
        last_incoming_message=last_message_text,  # User message = incoming
        last_outgoing_message=None,  # Not tracked separately for now
        user_name=user_name or session.user_id,
        is_read=is_read,
        ai_paused=ai_paused,
        escalation_reason=escalation_reason,
        widget_session_id=widget_session_id  # 🎯 Links to dynamiccode tracking
    )


async def broadcast_to_clients(conversation_id: str, message_data: dict):
    """Broadcast message to all connected SSE clients listening to this conversation."""
    if conversation_id not in _listeners or not _listeners[conversation_id]:
        return
    
    # Send to all queues for this conversation
    dead_queues = []
    for queue in _listeners[conversation_id]:
        try:
            await queue.put(message_data)
        except:
            dead_queues.append(queue)
    
    # Clean up dead queues
    for queue in dead_queues:
        _listeners[conversation_id].remove(queue)


async def broadcast_global(event_type: str, data: dict):
    """Broadcast to ALL connected sidebar listeners.
    
    Events:
    - new_conversation: A brand new conversation started
    - conversation_update: Existing conversation has new messages
    - ai_status_change: AI was paused/resumed
    """
    global _global_listeners
    
    listener_count = len(_global_listeners)
    print(f"📡 broadcast_global [{event_type}] - {listener_count} listeners connected")
    
    if not _global_listeners:
        print(f"⚠️ NO GLOBAL LISTENERS! Event {event_type} will be LOST!")
        return
    
    event_data = {
        "type": event_type,
        **data,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    }
    
    dead_queues = []
    sent_count = 0
    for queue in _global_listeners:
        try:
            await queue.put(event_data)
            sent_count += 1
        except Exception as e:
            print(f"⚠️ Failed to put event in queue: {e}")
            dead_queues.append(queue)
    
    for queue in dead_queues:
        _global_listeners.remove(queue)
    
    print(f"✅ Global broadcast [{event_type}]: sent to {sent_count}/{listener_count} listeners - {data.get('conversation_id', 'N/A')[:8]}...")


async def send_webhook_to_inbox(conversation_id: str, message_id: str, message: str, 
                                 sender_name: str, sender_type: str, 
                                 is_new_conversation: bool = False):
    """Send webhook notification TO Inbox when new message arrives.
    
    Uses /webhook/message (universal, no signature required) for simplicity.
    
    Args:
        is_new_conversation: True if this is the first message in a NEW conversation
    """
    # Get base URL from env, default to production
    base_url = os.environ.get("INBOX_WEBHOOK_URL", "https://chatrace-inbox-production-561c.up.railway.app/webhook/woodstock")
    # Use the /webhook/message endpoint (simpler, no signature)
    webhook_url = base_url.replace("/webhook/woodstock", "/webhook/message")
    
    payload = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "message": message,
        "content": message,  # Alias for compatibility
        "author": sender_name,
        "sender": {
            "id": sender_name,
            "name": "Woodstock Bot" if sender_type == "bot" else sender_name,
            "type": sender_type
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "is_new_conversation": is_new_conversation  # 🆕 Now passed correctly!
    }
    
    import json
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook_url, 
                json=payload,  # Use json= for automatic Content-Type
                timeout=5.0
            )
            print(f"{'✅' if resp.status_code == 200 else '⚠️'} Webhook to Inbox ({webhook_url}): {resp.status_code}")
            if resp.status_code != 200:
                print(f"   Response: {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Webhook error: {e}")


# --- Route Factory ---

def create_inbox_router(session_service, app_name: str = "gavigans_agent"):
    """Configure router with session service."""
    
    @router.get("/conversations", response_model=list[ConversationSummary])
    async def list_conversations(authorization: Optional[str] = Header(None)):
        """List all Woodstock conversations."""
        verify_api_key(authorization)
        try:
            response = await session_service.list_sessions(app_name=app_name, user_id=None)
            return [session_to_summary(s) for s in response.sessions]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    
    @router.get("/conversations/{conversation_id}/messages", response_model=MessagesResponse)
    async def get_messages(conversation_id: str, user_id: str = "default", 
                          authorization: Optional[str] = Header(None)):
        """Get messages for a conversation."""
        verify_api_key(authorization)
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            messages = _events_to_messages(session.events)

            return MessagesResponse(status="OK", messages=messages, source="gavigans")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    
    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user_id: str = "default",
                               authorization: Optional[str] = Header(None)):
        """Get conversation detail (alias)."""
        return await get_messages(conversation_id, user_id, authorization)
    
    
    # 🔥 IMPORTANT: /listen/global MUST come BEFORE /listen/{conversation_id}
    # Otherwise FastAPI will match "global" as a conversation_id!
    @router.get("/listen/global")
    async def listen_global():
        """🌐 Global SSE endpoint for Inbox SIDEBAR updates.
        
        Events sent:
        - new_conversation: Brand new conversation started
        - conversation_update: New message in existing conversation
        - ai_status_change: AI paused/resumed
        
        NO AUTH required - this is for the Inbox frontend sidebar.
        """
        import json
        
        async def event_generator():
            queue = asyncio.Queue()
            _global_listeners.append(queue)
            listener_id = id(queue)
            
            print(f"🌐 GLOBAL LISTENER ADDED - ID: {listener_id} - Total now: {len(_global_listeners)}")
            
            try:
                # Send initial connected message
                yield f"data: {json.dumps({'type': 'connected', 'conversation_id': 'global'})}\n\n"
                
                while True:
                    # Wait for new message (with timeout for keepalive)
                    try:
                        event_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        print(f"📤 SENDING TO GLOBAL {listener_id}: {event_data.get('type', 'unknown')}")
                        yield f"data: {json.dumps(event_data)}\n\n"
                    except asyncio.TimeoutError:
                        # Send keepalive ping
                        yield f": keepalive\n\n"
                        
            except asyncio.CancelledError:
                print(f"🌐 GLOBAL LISTENER REMOVED - ID: {listener_id}")
            finally:
                if queue in _global_listeners:
                    _global_listeners.remove(queue)
                print(f"🌐 Global listeners remaining: {len(_global_listeners)}")
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    
    
    @router.get("/listen/{conversation_id}")
    async def listen_for_messages(conversation_id: str, user_id: str = "default"):
        """SSE endpoint - Client keeps connection open to receive real-time messages.
        
        When human agent sends message via POST /messages, this stream pushes it to client.
        NO AUTH required - this is for the end-user chat widget.
        """
        async def event_generator():
            # Create queue for this client
            queue = asyncio.Queue()
            
            # Register queue for this conversation
            if conversation_id not in _listeners:
                _listeners[conversation_id] = []
            _listeners[conversation_id].append(queue)
            
            try:
                # Send initial connection confirmation
                yield f"data: {{'type':'connected','conversation_id':'{conversation_id}'}}\n\n"
                
                # Keep connection alive and send messages as they arrive
                while True:
                    # Wait for new message (with timeout for keepalive)
                    try:
                        message_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        import json
                        yield f"data: {json.dumps(message_data)}\n\n"
                    except asyncio.TimeoutError:
                        # Send keepalive ping
                        yield f": keepalive\n\n"
                    
            except asyncio.CancelledError:
                pass
            finally:
                # Clean up when client disconnects
                if conversation_id in _listeners and queue in _listeners[conversation_id]:
                    _listeners[conversation_id].remove(queue)
        
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Disable nginx buffering
            }
        )
    
    
    @router.post("/messages")
    async def send_message(request: SendMessageRequest, 
                          authorization: Optional[str] = Header(None)):
        """Inject message from human agent (Inbox) into conversation.
        
        This adds a message to the session as if it was sent by a human agent.
        The message will appear in the chat history for both the customer and Inbox.
        """
        verify_api_key(authorization)
        
        try:
            # Get existing session
            session = await session_service.get_session(
                app_name=app_name,
                user_id=request.user_id,
                session_id=request.conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            # Create event for human agent message
            message_id = str(uuid.uuid4())
            event = Event(
                id=message_id,
                author="human_agent",  # Distinguishes from bot
                content=genai_types.Content(
                    role="model",  # Shows as assistant-side message
                    parts=[genai_types.Part(text=request.message)]
                ),
                timestamp=time.time()
            )
            
            # Append event to session (ADK's official method)
            await session_service.append_event(session, event)
            
            # 🔥 BROADCAST to connected clients (per-conversation - for webchat widget)
            await broadcast_to_clients(request.conversation_id, {
                "type": "new_message",
                "message_id": message_id,
                "author": "human_agent",
                "content": request.message,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            })
            
            # 🔥 BROADCAST to global listeners (for inbox sidebar)
            await broadcast_global("conversation_update", {
                "conversation_id": request.conversation_id,
                "user_id": request.user_id,
                "last_message": request.message[:100],
                "author": "human_agent",
                "platform": "webchat",
                "source": "webchat"
            })
            
            print(f"✅ Human agent message injected & broadcast: {request.conversation_id[:8]}...")
            
            return {
                "status": "sent",
                "message_id": message_id,
                "conversation_id": request.conversation_id
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Send message error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    
    # ============================================
    # 🆕 READ/UNREAD STATUS
    # ============================================
    
    @router.post("/conversations/{conversation_id}/read")
    async def mark_as_read(conversation_id: str, request: MarkReadRequest,
                           authorization: Optional[str] = Header(None)):
        """Mark conversation as read by human agent.
        
        Updates session.state['is_read'] = True, which is persisted by ADK.
        """
        verify_api_key(authorization)
        
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=request.user_id,
                session_id=conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            # Create event with state_delta to mark as read
            event = Event(
                id=str(uuid.uuid4()),
                author="system",
                actions=EventActions(state_delta={
                    "is_read": True
                }),
                timestamp=time.time()
            )
            
            await session_service.append_event(session, event)
            print(f"✅ Marked conversation as read: {conversation_id[:8]}...")
            
            return {
                "status": "success",
                "conversation_id": conversation_id,
                "is_read": True
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Mark read error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    
    @router.post("/conversations/{conversation_id}/unread")
    async def mark_as_unread(conversation_id: str, request: MarkReadRequest,
                             authorization: Optional[str] = Header(None)):
        """Mark conversation as unread (new message arrived).
        
        Updates session.state['is_read'] = False, which is persisted by ADK.
        Typically called by webhook when user sends a message.
        """
        verify_api_key(authorization)
        
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=request.user_id,
                session_id=conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            # Create event with state_delta to mark as unread
            event = Event(
                id=str(uuid.uuid4()),
                author="system",
                actions=EventActions(state_delta={
                    "is_read": False
                }),
                timestamp=time.time()
            )
            
            await session_service.append_event(session, event)
            print(f"📬 Marked conversation as unread: {conversation_id[:8]}...")
            
            return {
                "status": "success",
                "conversation_id": conversation_id,
                "is_read": False
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Mark unread error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    
    # ============================================
    # 🆕 AI TOGGLE (HUMAN TAKEOVER)
    # ============================================
    
    @router.post("/toggle-ai")
    async def toggle_ai(request: ToggleAIRequest,
                        authorization: Optional[str] = Header(None)):
        """Toggle AI on/off for a conversation.
        
        When ai_enabled=False:
        - Sets session.state['ai_paused'] = True
        - The before_agent_callback will return a "human is handling" message
        - AI will NOT respond to user messages
        
        When ai_enabled=True:
        - Sets session.state['ai_paused'] = False
        - AI responses resume normally
        """
        verify_api_key(authorization)
        
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=request.user_id,
                session_id=request.conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            ai_paused = not request.ai_enabled
            
            # Build state delta
            state_delta = {
                "ai_paused": ai_paused
            }
            
            # If pausing AI, record when and why
            if ai_paused:
                state_delta["escalation_time"] = time.time()
                state_delta["escalation_reason"] = "Human agent takeover"
            else:
                # Clearing escalation when AI is re-enabled
                state_delta["escalation_reason"] = None
            
            # Create event with state_delta
            event = Event(
                id=str(uuid.uuid4()),
                author="system",
                actions=EventActions(state_delta=state_delta),
                timestamp=time.time()
            )
            
            await session_service.append_event(session, event)
            
            status_msg = "disabled" if ai_paused else "enabled"
            print(f"🤖 AI {status_msg} for conversation: {request.conversation_id[:8]}...")
            
            # 🔥 BROADCAST to webchat so it knows AI status changed!
            await broadcast_to_clients(request.conversation_id, {
                "type": "ai_status_changed",
                "ai_enabled": request.ai_enabled,
                "ai_paused": ai_paused,
                "message": "A human agent is now handling your conversation." if ai_paused else "AI assistant is back online."
            })
            
            # 🔥 BROADCAST to global listeners (for inbox sidebar)
            await broadcast_global("ai_status_change", {
                "conversation_id": request.conversation_id,
                "user_id": request.user_id,
                "ai_enabled": request.ai_enabled,
                "ai_paused": ai_paused,
                "platform": "webchat"
            })
            
            return {
                "status": "success",
                "conversation_id": request.conversation_id,
                "ai_enabled": request.ai_enabled,
                "ai_paused": ai_paused
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Toggle AI error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    
    @router.get("/conversation-status/{conversation_id}")
    async def get_conversation_status(conversation_id: str, user_id: str = "default",
                                       authorization: Optional[str] = Header(None)):
        """Get AI status and read status for a conversation."""
        verify_api_key(authorization)
        
        try:
            session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=conversation_id
            )
            
            if not session:
                raise HTTPException(status_code=404, detail="Conversation not found")
            
            state = session.state if hasattr(session, 'state') and session.state else {}
            
            return {
                "conversation_id": conversation_id,
                "is_read": state.get("is_read", True),
                "ai_paused": state.get("ai_paused", False),
                "escalation_reason": state.get("escalation_reason"),
                "escalation_time": state.get("escalation_time")
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router
