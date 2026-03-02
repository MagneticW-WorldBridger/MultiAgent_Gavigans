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
    return userId
  }
  
  // Priority 2: Check localStorage for existing anonymous ID
  const storedUserId = localStorage.getItem(STORAGE_KEY_USER_ID)
  if (storedUserId) {
    return storedUserId
  }
  
  // Priority 3: Generate new and store
  const newUserId = randomId('user')
  localStorage.setItem(STORAGE_KEY_USER_ID, newUserId)
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
  
  // First pass: check for function calls and responses (higher priority than text).
  // If a transfer_to_agent is present alongside text, the text is spurious routing
  // chatter from the root agent and should be ignored.
  let hasTransfer = false
  for (const part of parts) {
    if (part.functionCall?.name === 'transfer_to_agent') {
      hasTransfer = true
    }
  }

  for (const part of parts) {
    if (part.functionCall) {
      return {
        type: EventType.FUNCTION_CALL,
        name: part.functionCall.name,
        args: part.functionCall.args,
        raw: event
      }
    }
    
    if (part.functionResponse) {
      return {
        type: EventType.FUNCTION_RESPONSE,
        name: part.functionResponse.name,
        response: part.functionResponse.response,
        raw: event
      }
    }
  }

  // Only process text if there's no transfer happening in this event
  if (!hasTransfer) {
    for (const part of parts) {
      if (typeof part.text === 'string' && part.text.trim()) {
        return {
          type: event.partial ? EventType.TEXT_PARTIAL : EventType.TEXT,
          text: part.text.trim(),
          raw: event
        }
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
  const [hasPendingRecovery, setHasPendingRecovery] = useState(false) // Show recovery prompt
  const [pendingSessionData, setPendingSessionData] = useState(null) // Store session data for recovery
  
  // Persistent userId - same across page loads and sessions.
  // Lazy init to avoid calling getPersistentUserId() on every render.
  const userIdRef = useRef(null)
  if (userIdRef.current === null) {
    userIdRef.current = getPersistentUserId()
  }
  
  // Persistent sessionId - try to recover from localStorage (lazy init)
  const sessionIdRef = useRef(null)
  if (sessionIdRef.current === null) {
    sessionIdRef.current = getStoredSessionId()
  }
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

    // SSE endpoint is on the backend server - use current origin in production
    const backendUrl = API_BASE.startsWith('http') ? API_BASE.replace('/api', '') : window.location.origin
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
   * 🔐 Check if there's an existing session with history (but don't auto-load it!)
   * User must explicitly choose to continue or start fresh
   */
  const checkForRecoverableSession = useCallback(async (sessionId, userId) => {
    try {
      console.log(`🔍 Checking for recoverable session: ${sessionId}`)
      
      const getUrl = `${API_BASE}/apps/${APP_NAME}/users/${userId}/sessions/${sessionId}`
      const response = await fetch(getUrl)
      
      if (!response.ok) {
        console.log(`📭 Session ${sessionId} not found or expired (${response.status})`)
        return null
      }
      
      const session = await response.json()
      
      if (!session || !session.events || session.events.length === 0) {
        console.log(`📭 Session ${sessionId} exists but has no history`)
        return null
      }
      
      const recoveredMessages = parseEventsToMessages(session.events)
      
      if (recoveredMessages.length > 0) {
        console.log(`📜 Found ${recoveredMessages.length} messages in session - prompting user`)
        return { session, recoveredMessages }
      }
      
      return null
    } catch (error) {
      console.log(`❌ Failed to check session: ${error.message}`)
      return null
    }
  }, [])

  /**
   * 🔐 Actually recover the session (user chose to continue)
   */
  const confirmRecovery = useCallback(() => {
    if (pendingSessionData) {
      const { recoveredMessages } = pendingSessionData
      setMessages([WELCOME_MESSAGE, ...recoveredMessages])
      setIsRecovered(true)
      sessionCreatedRef.current = true
      startListening(sessionIdRef.current, userIdRef.current)
      console.log(`✅ User confirmed session recovery: ${sessionIdRef.current}`)
    }
    setHasPendingRecovery(false)
    setPendingSessionData(null)
    setStatus('idle')
  }, [pendingSessionData, startListening])

  /**
   * 🔐 Decline recovery and start fresh (user chose new conversation)
   */
  const declineRecovery = useCallback(() => {
    console.log('🆕 User declined recovery - starting fresh')
    sessionIdRef.current = null
    clearStoredSession()
    setHasPendingRecovery(false)
    setPendingSessionData(null)
    setStatus('idle')
  }, [])

  const ensureSession = useCallback(async () => {
    // Already have an active session in this page load
    if (sessionCreatedRef.current && sessionIdRef.current) {
      return sessionIdRef.current
    }

    // 🔐 If there's a pending recovery prompt, don't create new session yet
    if (hasPendingRecovery) {
      console.log('⏳ Waiting for user to choose: continue or start fresh')
      return null
    }
    
    sessionRecoveryAttempted.current = true

    // 🔐 Create new session with initial state (no auto-recovery!)
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
  }, [startListening, hasPendingRecovery])

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || (status !== 'idle' && status !== 'human_mode')) return
    
    // 🔐 Don't allow sending if there's a pending recovery prompt
    if (hasPendingRecovery) {
      console.log('⏳ Please choose to continue or start fresh first')
      return
    }

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
      
      const requestBody = JSON.stringify({
        appName: APP_NAME,
        userId: userIdRef.current,
        sessionId: sessionId,
        newMessage: {
          role: 'user',
          parts: [{ text: text.trim() }]
        },
        streaming: true
      })

      // Retry logic for network errors (up to 2 retries)
      const MAX_RETRIES = 2
      let response = null
      for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        try {
          response = await fetch(sseUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: requestBody,
            signal: abortControllerRef.current.signal
          })
          if (response.ok) break
          if (attempt < MAX_RETRIES) {
            console.warn(`⚠️ SSE request failed (attempt ${attempt + 1}/${MAX_RETRIES + 1}), retrying...`)
            await new Promise(r => setTimeout(r, 1000 * (attempt + 1)))
          }
        } catch (fetchErr) {
          if (fetchErr.name === 'AbortError') throw fetchErr
          if (attempt < MAX_RETRIES) {
            console.warn(`⚠️ Network error (attempt ${attempt + 1}/${MAX_RETRIES + 1}), retrying...`, fetchErr.message)
            await new Promise(r => setTimeout(r, 1000 * (attempt + 1)))
          } else {
            throw fetchErr
          }
        }
      }

      if (!response || !response.ok) {
        throw new Error(`API error ${response?.status || 'unknown'}`)
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
      if (finalText === '__AI_PAUSED__') {
        console.log('🚫 AI is paused - human agent will respond')
        setAiPaused(true) // Remember AI is paused for future messages
        setStatus('human_mode') // Set status to human mode
        // Don't add any message - human agent response will come via SSE listener
        return
      }
      
      // 🐛 FIX: Empty response is NOT the same as AI paused!
      // An empty response might indicate a routing/transfer issue, not human takeover.
      // Show a generic message instead of silently failing.
      if (!finalText.trim()) {
        console.warn('⚠️ Empty response from agent - may be a routing issue')
        setMessages(prev => [...prev, {
          role: 'agent',
          text: 'I apologize, I encountered an issue processing your request. Please try again or rephrase your question.'
        }])
        setStatus('idle')
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
        console.error('SSE error:', error)
        setMessages(prev => [...prev, {
          role: 'agent',
          text: 'Sorry, I had a connection issue. Could you please try sending your message again?'
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
  }, [status, ensureSession, aiPaused, hasPendingRecovery])

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

  // 🔐 Check for recoverable session on mount and PROMPT user (don't auto-load!)
  useEffect(() => {
    const storedSessionId = getStoredSessionId()
    if (storedSessionId && !sessionRecoveryAttempted.current) {
      sessionRecoveryAttempted.current = true
      console.log('🔍 Found stored session on mount, checking if recoverable...')
      
      checkForRecoverableSession(storedSessionId, userIdRef.current)
        .then(result => {
          if (result) {
            // Found recoverable session - show prompt to user
            setPendingSessionData(result)
            setHasPendingRecovery(true)
            console.log('💬 Prompting user to continue or start fresh')
          } else {
            // No recoverable session - clear stored reference
            sessionIdRef.current = null
            clearStoredSession()
            console.log('🆕 No recoverable session - ready for new conversation')
          }
        })
        .catch(err => {
          console.error('❌ Session check failed:', err.message)
          sessionIdRef.current = null
          clearStoredSession()
        })
    }
  }, [checkForRecoverableSession])

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
    sessionId: sessionIdRef.current,  // Current sessionId (for debugging)
    // 🆕 Session recovery prompt
    hasPendingRecovery, // Show "continue conversation?" prompt
    pendingMessageCount: pendingSessionData?.recoveredMessages?.length || 0,
    confirmRecovery,    // User chose to continue
    declineRecovery     // User chose to start fresh
  }
}

