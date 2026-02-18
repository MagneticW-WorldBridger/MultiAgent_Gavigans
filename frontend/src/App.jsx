/**
 * Iframe chat content - Live streaming chat with bubble/teaser theme
 * Powered by Google ADK
 */
import { useState, useRef, useEffect } from 'react'
import { RotateCcw } from 'lucide-react'
import { useSSEChat } from './hooks/useSSEChat'
import ChatMessage from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import EventFeed from './components/EventFeed'
import './index.css'

const CLIENT_LOGO_URL = 'https://imageresizer.furnituredealer.net/img/remote/images.furnituredealer.net/img/dealer/13381/upload/logo/507d3c181b1545dc83336fd9cc1781cb.png?format=webp&quality=85'

function App() {
  const [input, setInput] = useState('')
  const messagesEndRef = useRef(null)
  
  const {
    messages,
    liveEvents,
    status,
    streamingText,
    sendMessage,
    reset,
    cancelStream,
    aiPaused,  // 🆕 Track if human is handling the conversation
    hasPendingRecovery,
    pendingMessageCount,
    confirmRecovery,
    declineRecovery
  } = useSSEChat()

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, liveEvents, status])

  const handleSend = (text) => {
    const messageText = text || input
    if (messageText.trim()) {
      sendMessage(messageText.trim())
      setInput('')
    }
  }

  const isStreaming = status === 'streaming'
  const isLoading = status === 'loading'
  const isHumanMode = status === 'human_mode' || aiPaused  // 🆕 Don't show "thinking" in human mode

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-content">
          <div className="header-brand">
            <div className="brand-logo">
              <img src={CLIENT_LOGO_URL} alt="" className="logo-image" />
            </div>
            <div className="brand-text">
              <h1 className="brand-title">Gavigans</h1>
              <p className="brand-subtitle">Multi-Agent Platform</p>
            </div>
          </div>
          
          <button
            onClick={reset}
            className="reset-btn"
            title="New conversation"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Messages */}
      <main className="messages-container">
        <div className="messages">
          {messages.map((message, index) => (
            <ChatMessage key={index} message={message} />
          ))}
          
          {/* Session Recovery Prompt */}
          {hasPendingRecovery && (
            <div className="recovery-prompt">
              <div className="recovery-content">
                <p className="recovery-text">
                  You have a previous conversation with {pendingMessageCount} messages.
                </p>
                <div className="recovery-buttons">
                  <button 
                    onClick={confirmRecovery}
                    className="recovery-btn continue-btn"
                  >
                    Continue
                  </button>
                  <button 
                    onClick={declineRecovery}
                    className="recovery-btn fresh-btn"
                  >
                    Start Fresh
                  </button>
                </div>
              </div>
            </div>
          )}
          
          {/* Live Event Feed - Shows during streaming (NOT in human mode) */}
          {(isLoading || isStreaming) && !isHumanMode && (
            <EventFeed 
              events={liveEvents}
              isStreaming={isLoading || isStreaming}
              streamingText={streamingText}
            />
          )}
          
          <div ref={messagesEndRef} />
        </div>
      </main>

      {/* Input */}
      <footer className="footer">
        <ChatInput
          value={input}
          onChange={setInput}
          onSend={handleSend}
          onCancel={cancelStream}
          status={hasPendingRecovery ? 'loading' : status}
        />
      </footer>
    </div>
  )
}

export default App
