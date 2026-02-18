/**
 * SSE Chat Hook - Real-time streaming for ADK events
 * Connects to /run_sse endpoint for live event streaming
 * + Real-time incoming messages from human agents via /api/inbox/listen
 * 
 * 🔐 SESSION PERSISTENCE ARCHITECTURE:
 * =====================================
 * 
 * THE PROBLEM:
 * If we generate a new random userId each time the chat opens,
 * the system thinks it's a completely new person. This breaks:
 * 1. Chat history persistence
 * 2. AI toggle (Inbox has old userId, chat has new one)
 * 3. Customer authentication
 * 
 * THE SOLUTION:
 * 1. userId is PERSISTENT:
 *    - If customer is authenticated → hash of email (same email = same userId always)
 *    - If anonymous → stored in localStorage (persists across page loads)
 * 
 * 2. sessionId is PERSISTENT:
 *    - Stored in localStorage
 *    - On page load, try to recover existing session from API
 *    - If found, load chat history
 *    - If not found (expired), create new session
 * 
 * 3. AI toggle state syncs:
 *    - Session state has ai_paused flag (persisted by ADK DatabaseSessionService)
 *    - When recovering session, we read this flag
 *    - Inbox calls toggle-ai with the SAME userId the chat uses
 */
import { useState, useRef, useCallback, useEffect } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const APP_NAME = import.meta.env.VITE_APP_NAME ?? 'gavigans_agent'

// 🔐 Storage keys for persistence
const STORAGE_KEY_USER_ID = 'gavigans_chat_user_id'
const STORAGE_KEY_SESSION_ID = 'gavigans_chat_session_id'

/**
 * Generate a random ID (only used when no existing ID found)
 */
const randomId = (prefix) => `${prefix}_${Math.random().toString(36).slice(2, 8)}`

/**
 * 🔐 Simple hash function to create consistent userId from email
 * Same email = same hash = same userId ALWAYS
 */
function hashEmail(email) {
  let hash = 0
  for (let i = 0; i < email.length; i++) {
    const char = email.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash // Convert to 32bit integer
  }
  return `user_${Math.abs(hash).toString(36)}`
}

/**
 * 🔐 Get or create a PERSISTENT userId
 * 
 * Priority:
 * 1. If customer is authenticated (email in URL), use hash of email
 *    → This ensures same email = same userId across sessions/devices
 * 2. If stored in localStorage, use that
 *    → This ensures anonymous users keep their identity
 * 3. Generate new and store in localStorage
 *    → First time anonymous visitor
 */
function getPersistentUserId() {
  const params = new URLSearchParams(window.location.search)
  const customerEmail = params.get('customer_email')
  
  // Priority 1: Authenticated customer - use email hash for consistency
  if (customerEmail) {
    const userId = hashEmail(customerEmail)
    localStorage.setItem(STORAGE_KEY_USER_ID, userId)
    console.log('🔐 Using email-based userId:', userId, '(from:', customerEmail, ')')
    return userId
  }
  
  // Priority 2: Check localStorage for existing anonymous ID
  const storedUserId = localStorage.getItem(STORAGE_KEY_USER_ID)
  if (storedUserId) {
    console.log('💾 Using stored userId:', storedUserId)
    return storedUserId
  }
  
  // Priority 3: Generate new and store
  const newUserId = randomId('user')
  localStorage.setItem(STORAGE_KEY_USER_ID, newUserId)
  console.log('🆕 Generated new userId:', newUserId)
  return newUserId
}

/**
 * 🔐 Get stored sessionId (if any)
 */
function getStoredSessionId() {
  return localStorage.getItem(STORAGE_KEY_SESSION_ID)
}

/**
 * 🔐 Store sessionId for recovery
 */
function storeSessionId(sessionId) {
  if (sessionId) {
    localStorage.setItem(STORAGE_KEY_SESSION_ID, sessionId)
    console.log('💾 Stored sessionId for recovery:', sessionId)
  }
}

/**
 * 🔐 Clear stored session (for explicit reset only)
 * NOTE: We keep the userId so the user maintains their identity
 */
function clearStoredSession() {
  localStorage.removeItem(STORAGE_KEY_SESSION_ID)
  console.log('🗑️ Cleared stored sessionId (userId preserved)')
}

/**
 * 🔐 Extract customer data from URL params (passed by widget)
 * Uses `user:` prefix for cross-session persistence per ADK State docs
 */
function getInitialStateFromUrl() {
  const params = new URLSearchParams(window.location.search)
  const state = {}
  
  // Customer authentication data (use user: prefix for cross-session persistence)
  const customerEmail = params.get('customer_email')
  const customerId = params.get('customer_id')
  const loftIds = params.get('loft_ids')
  const widgetSessionId = params.get('widget_session_id')
  
  if (customerEmail) {
    state['user:customer_email'] = customerEmail
    state['user:is_authenticated'] = true
    console.log('🔐 Customer email from URL:', customerEmail)
  }
  
  if (customerId) {
    state['user:magento_customer_id'] = customerId
    console.log('🔐 Magento customer ID from URL:', customerId)
  }
  
  if (loftIds) {
    state['user:loft_customer_ids'] = loftIds.split(',').filter(Boolean)
    console.log('🔐 Loft customer IDs from URL:', loftIds)
  }
  
  if (widgetSessionId) {
    state['widget_session_id'] = widgetSessionId
  }
  
  // Context data (regular session scope)
  const host = params.get('host')
  const page = params.get('page')
  const ref = params.get('ref')
  
  if (host) state['context:host'] = host
  if (page) state['context:page'] = page
  if (ref) state['context:referrer'] = ref
  
  // UTM tracking
  const utmParams = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']
  utmParams.forEach(utm => {
    const value = params.get(utm)
    if (value) state[`context:${utm}`] = value
  })
  
  return state
}

/**
 * 🔐 Parse ADK session events into chat messages
 * Used when recovering an existing session to show chat history
 */
function parseEventsToMessages(events) {
  const messages = []
  
  for (const event of events) {
    if (!event.content?.parts) continue
    
    // Extract text from event parts
    const textParts = event.content.parts
      .filter(p => p.text && p.text.trim())
      .map(p => p.text.trim())
      .join(' ')
    
    // Skip empty or internal messages
    if (!textParts || textParts === '__AI_PAUSED__') continue
    
    // Map author to role for UI
    let role = 'agent'
    if (event.author === 'user') role = 'user'
    else if (event.author === 'human_agent') role = 'agent'
    else if (event.author === 'system') role = 'system'
    
    messages.push({
      role,
      text: textParts,
      author: event.author,
      timestamp: event.timestamp
    })
  }
  
  return messages
}

// Welcome message shown when starting fresh
const WELCOME_MESSAGE = {
  role: 'system',
  text: `Welcome to Gavigans! 🤖\n\nI'm your AI assistant powered by AiPRL Assist.\nHow can I help you today?`
}

// Event types for categorization
export const EventType = {
  FUNCTION_CALL: 'function_call',
  FUNCTION_RESPONSE: 'function_response',
  TEXT: 'text',
  TEXT_PARTIAL: 'text_partial',
  ERROR: 'error',
  THINKING: 'thinking'
}

/**
 * Parse an ADK event and categorize it
 */
function parseEvent(event) {
  const parts = event?.content?.parts ?? []
  
  for (const part of parts) {
    // Check for function call
    if (part.functionCall) {
      return {
        type: EventType.FUNCTION_CALL,
        name: part.functionCall.name,
        args: part.functionCall.args,
        raw: event
      }
    }
    
    // Check for function response
    if (part.functionResponse) {
      return {
        type: EventType.FUNCTION_RESPONSE,
        name: part.functionResponse.name,
        response: part.functionResponse.response,
        raw: event
      }
    }
    
    // Check for text
    if (typeof part.text === 'string' && part.text.trim()) {
      return {
        type: event.partial ? EventType.TEXT_PARTIAL : EventType.TEXT,
        text: part.text.trim(),
        raw: event
      }
    }
  }
  
  return { type: EventType.THINKING, raw: event }
}

/**
 * Extract products from function responses
 */
function extractProducts(events) {
  const products = []
  for (const event of events) {
    if (event.type === EventType.FUNCTION_RESPONSE) {
      const response = event.response
      if (response?.products && Array.isArray(response.products)) {
        products.push(...response.products)
      }
      if (response?.product) {
        products.push(response.product)
      }
    }
  }
  return products.filter(p => p.name && p.price)
}

export function useSSEChat() {
  const [messages, setMessages] = useState([WELCOME_MESSAGE])
  const [liveEvents, setLiveEvents] = useState([])
  const [status, setStatus] = useState('idle') // idle | loading | streaming | human_mode | recovering
  const [streamingText, setStreamingText] = useState('')
  const [aiPaused, setAiPaused] = useState(false) // Track if AI is paused (human takeover)
  const [isRecovered, setIsRecovered] = useState(false) // Track if we recovered a session
  
  // 🔐 PERSISTENT userId - same across page loads and sessions!
  const userIdRef = useRef(getPersistentUserId())
  
  // 🔐 PERSISTENT sessionId - try to recover from localStorage
  const sessionIdRef = useRef(getStoredSessionId())
  const sessionCreatedRef = useRef(false)
  const sessionRecoveryAttempted = useRef(false)
  
  const abortControllerRef = useRef(null)
  const inboxSSERef = useRef(null)  // SSE for receiving human agent messages
  const listenEventSourceRef = useRef(null)

  // 🔥 NEW: Listen for incoming messages from human agents
  const startListening = useCallback((sessionId, userId) => {
    if (listenEventSourceRef.current) {
      listenEventSourceRef.current.close()
    }

    // SSE endpoint is on the backend server, not Vite dev server
    const backendUrl = API_BASE.startsWith('http') ? API_BASE.replace('/api', '') : 'http://localhost:8000'
    const listenUrl = `${backendUrl}/api/inbox/listen/${sessionId}?user_id=${userId}`
    console.log('🎧 Connecting SSE for incoming messages:', listenUrl)
    
    const eventSource = new EventSource(listenUrl)
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        console.log('📥 SSE received:', data)
        
        if (data.type === 'new_message') {
          // Add human agent message to chat
          setMessages(prev => [...prev, {
            role: 'agent',
            text: data.content,
            author: data.author,
            timestamp: data.timestamp
          }])
          console.log('✅ Human agent message added:', data.content)
        }
        
        // 🆕 Handle AI status change from Inbox
        if (data.type === 'ai_status_changed') {
          setAiPaused(data.ai_paused)
          console.log(`🤖 AI status changed: ${data.ai_paused ? 'PAUSED (human mode)' : 'ACTIVE'}`)
          
          // Show status message to user
          if (data.message) {
            setMessages(prev => [...prev, {
              role: 'system',
              text: data.message,
              timestamp: new Date().toISOString()
            }])
          }
        }
      } catch (e) {
        // Ignore keepalive or malformed events
      }
    }
    
    eventSource.onerror = (error) => {
      console.warn('⚠️ SSE connection lost, reconnecting in 3s...', error)
      eventSource.close()
      setTimeout(() => startListening(sessionId, userId), 3000)
    }
    
    listenEventSourceRef.current = eventSource
  }, [])

  /**
   * 🔐 Try to recover an existing session and load chat history
   * This enables session persistence across page loads/browser refreshes
   */
  const tryRecoverSession = useCallback(async (sessionId, userId) => {
    try {
      console.log(`🔄 Attempting to recover session: ${sessionId} for user: ${userId}`)
      
      // Try to get the session from the API
      const getUrl = `${API_BASE}/apps/${APP_NAME}/users/${userId}/sessions/${sessionId}`
      const response = await fetch(getUrl)
      
      if (!response.ok) {
        console.log(`📭 Session ${sessionId} not found or expired (${response.status})`)
        return null
      }
      
      const session = await response.json()
      
      // Check if session has events (chat history)
      if (!session || !session.events || session.events.length === 0) {
        console.log(`📭 Session ${sessionId} exists but has no history`)
        return session
      }
      
      // Parse events into messages for display
      const recoveredMessages = parseEventsToMessages(session.events)
      
      if (recoveredMessages.length > 0) {
        console.log(`📜 Recovered ${recoveredMessages.length} messages from session history`)
        // Prepend welcome message to recovered history
        setMessages([WELCOME_MESSAGE, ...recoveredMessages])
        setIsRecovered(true)
      }
      
      // 🔐 Check AI paused status from session state (this is the key for AI toggle!)
      if (session.state?.ai_paused) {
        setAiPaused(true)
        console.log('🤖 AI is currently paused (human takeover mode active)')
      }
      
      return session
      
    } catch (error) {
      console.log(`❌ Failed to recover session: ${error.message}`)
      return null
    }
  }, [])

  const ensureSession = useCallback(async () => {
    // Already have an active session in this page load
    if (sessionCreatedRef.current && sessionIdRef.current) {
      return sessionIdRef.current
    }

    // 🔐 STEP 1: Try to recover existing session FIRST (only attempt once)
    if (!sessionRecoveryAttempted.current && sessionIdRef.current) {
      sessionRecoveryAttempted.current = true
      setStatus('recovering')
      
      const recovered = await tryRecoverSession(sessionIdRef.current, userIdRef.current)
      
      if (recovered) {
        console.log(`✅ Recovered existing session: ${sessionIdRef.current}`)
        sessionCreatedRef.current = true
        startListening(sessionIdRef.current, userIdRef.current)
        setStatus('idle')
        return sessionIdRef.current
      }
      
      // Session not found or expired - clear it and create new
      console.log('🗑️ Session not found - clearing stored reference')
      sessionIdRef.current = null
      clearStoredSession()
      // 🔧 FIX: Reset status to idle so sendMessage doesn't skip!
      setStatus('idle')
    }
    
    sessionRecoveryAttempted.current = true

    // 🔐 STEP 2: Create new session with initial state
    const initialState = getInitialStateFromUrl()
    const isAuthenticated = Boolean(initialState['user:customer_email'])
    
    if (isAuthenticated) {
      console.log('🔐 Creating authenticated session with state:', initialState)
    } else {
      console.log('🆕 Creating anonymous session')
    }

    const createUrl = `${API_BASE}/apps/${APP_NAME}/users/${userIdRef.current}/sessions`
    const response = await fetch(createUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: initialState })
    })

    if (!response.ok) {
      throw new Error(`Failed to create session: ${response.status}`)
    }

    const session = await response.json()
    sessionIdRef.current = session.id
    sessionCreatedRef.current = true
    
    // 🔐 IMPORTANT: Store sessionId for future recovery!
    storeSessionId(session.id)
    
    // Start listening for incoming messages (human agent, AI toggle)
    startListening(session.id, userIdRef.current)
    
    // Log status
    if (isAuthenticated) {
      console.log(`✅ New authenticated session: ${session.id} (customer: ${initialState['user:customer_email']})`)
    } else {
      console.log(`✅ New anonymous session: ${session.id}`)
    }
    
    return session.id
  }, [startListening, tryRecoverSession])

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || (status !== 'idle' && status !== 'human_mode')) return

    // Add user message immediately
    setMessages(prev => [...prev, { role: 'user', text: text.trim() }])
    
    // 🆕 If AI is paused, don't show "thinking" - message goes to human agent
    if (aiPaused) {
      setStatus('human_mode')
      console.log('📤 Message sent to human agent (AI paused)')
      // Still need to send to backend so it gets stored and forwarded to Inbox
    } else {
      setStatus('loading')
    }
    setLiveEvents([])
    setStreamingText('')

    try {
      const sessionId = await ensureSession()
      
      // Use SSE endpoint for streaming
      const sseUrl = `${API_BASE}/run_sse`
      
      abortControllerRef.current = new AbortController()
      
      const response = await fetch(sseUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          appName: APP_NAME,
          userId: userIdRef.current,
          sessionId: sessionId,
          newMessage: {
            role: 'user',
            parts: [{ text: text.trim() }]
          },
          streaming: true
        }),
        signal: abortControllerRef.current.signal
      })

      if (!response.ok) {
        throw new Error(`API error ${response.status}`)
      }

      setStatus('streaming')
      
      // Handle SSE stream
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const allEvents = []
      let accumulatedText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const eventData = JSON.parse(line.slice(6))
              const parsed = parseEvent(eventData)
              allEvents.push(parsed)
              
              // Update live events feed (for non-text events)
              if (parsed.type === EventType.FUNCTION_CALL || 
                  parsed.type === EventType.FUNCTION_RESPONSE) {
                setLiveEvents(prev => [...prev, { ...parsed, id: Date.now(), timestamp: new Date() }])
              }
              
              // Accumulate streaming text
              if (parsed.type === EventType.TEXT_PARTIAL || parsed.type === EventType.TEXT) {
                accumulatedText = parsed.text // Replace with latest
                setStreamingText(parsed.text)
              }
            } catch (e) {
              // Skip malformed events
              console.debug('SSE parse error:', e)
            }
          }
        }
      }

      // Extract final text and products
      const textEvents = allEvents.filter(e => e.type === EventType.TEXT || e.type === EventType.TEXT_PARTIAL)
      const finalText = textEvents.length > 0 
        ? textEvents[textEvents.length - 1].text 
        : accumulatedText || ''
      
      // 🆕 Check for AI paused marker - don't show any message, human will respond via SSE
      if (finalText === '__AI_PAUSED__' || !finalText.trim()) {
        console.log('🚫 AI is paused - human agent will respond')
        setAiPaused(true) // Remember AI is paused for future messages
        setStatus('human_mode') // Set status to human mode
        // Don't add any message - human agent response will come via SSE listener
        return
      }
      
      const products = extractProducts(allEvents)
      const toolCalls = allEvents.filter(e => e.type === EventType.FUNCTION_CALL)

      // Add agent message
      setMessages(prev => [...prev, {
        role: 'agent',
        text: finalText,
        products,
        toolCalls,
        events: allEvents
      }])

    } catch (error) {
      if (error.name !== 'AbortError') {
        setMessages(prev => [...prev, {
          role: 'agent',
          text: `❌ Error: ${error.message}. Please check if the server is running.`
        }])
      }
    } finally {
      // 🆕 Keep human_mode if AI is paused, otherwise go to idle
      if (!aiPaused) {
        setStatus('idle')
      } else {
        setStatus('human_mode')
      }
      setLiveEvents([])
      setStreamingText('')
    }
  }, [status, ensureSession, aiPaused])

  const reset = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    
    // Close SSE connection
    if (listenEventSourceRef.current) {
      listenEventSourceRef.current.close()
      listenEventSourceRef.current = null
    }
    
    // Reset UI state
    setMessages([WELCOME_MESSAGE])
    setLiveEvents([])
    setStreamingText('')
    setStatus('idle')
    setAiPaused(false)
    setIsRecovered(false)
    
    // 🔐 IMPORTANT: Keep the userId! Only clear the session.
    // This ensures the user maintains their identity across conversations.
    // userIdRef.current stays the same!
    
    // Clear session (but keep userId in localStorage)
    sessionIdRef.current = null
    sessionCreatedRef.current = false
    sessionRecoveryAttempted.current = false
    clearStoredSession()
    
    console.log('🔄 Chat reset - userId preserved:', userIdRef.current)
  }, [])

  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
  }, [])

  // 🔐 Try to recover session on mount (if we have a stored sessionId)
  useEffect(() => {
    const storedSessionId = getStoredSessionId()
    if (storedSessionId && !sessionRecoveryAttempted.current) {
      console.log('🔄 Found stored session on mount, attempting recovery...')
      // ensureSession will handle the recovery
      ensureSession()
        .catch(err => {
          console.error('❌ Session recovery failed on mount:', err.message)
        })
        .finally(() => {
          // 🔧 FIX: Ensure status is reset even if ensureSession fails
          setStatus(prev => prev === 'recovering' ? 'idle' : prev)
        })
    }
  }, [ensureSession])

  // Cleanup SSE connection on unmount
  useEffect(() => {
    return () => {
      if (listenEventSourceRef.current) {
        listenEventSourceRef.current.close()
      }
    }
  }, [])

  return {
    messages,
    liveEvents,
    status,
    streamingText,
    sendMessage,
    reset,
    cancelStream,
    // 🔐 Session info for debugging and Inbox integration
    aiPaused,           // Is AI paused (human takeover)?
    isRecovered,        // Did we recover an existing session?
    userId: userIdRef.current,      // Current userId (for debugging)
    sessionId: sessionIdRef.current  // Current sessionId (for debugging)
  }
}

