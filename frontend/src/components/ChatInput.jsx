/**
 * ChatInput - Message input with quick suggestions
 * AiPRL powered assistant
 */
import { useRef, useEffect } from 'react'
import { Send, Loader2, StopCircle } from 'lucide-react'

const SUGGESTIONS = [
  'What are your store hours?',
  'Tell me about financing',
  'Book an appointment',
  'Help me find furniture'
]

export function ChatInput({ value, onChange, onSend, onCancel, status }) {
  const inputRef = useRef(null)
  const isLoading = status === 'loading'
  const isStreaming = status === 'streaming'
  const isBusy = isLoading || isStreaming

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSubmit = (e) => {
    e?.preventDefault()
    if (value.trim() && !isBusy) {
      onSend(value)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSuggestion = (text) => {
    onChange(text)
    inputRef.current?.focus()
  }

  return (
    <div className="chat-input-container">
      <form onSubmit={handleSubmit} className="chat-input-form">
        <div className="input-wrapper">
          <textarea
            ref={inputRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about furniture, locations, appointments..."
            rows={1}
            className="chat-textarea"
            disabled={isBusy}
          />
        </div>
        
        {isStreaming ? (
          <button
            type="button"
            onClick={onCancel}
            className="send-btn cancel-btn"
            aria-label="Cancel"
          >
            <StopCircle className="w-5 h-5" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!value.trim() || isBusy}
            className="send-btn"
            aria-label="Send message"
          >
            {isLoading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </button>
        )}
      </form>
      
      {/* Quick suggestions */}
      <div className="suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            onClick={() => handleSuggestion(suggestion)}
            className="suggestion-btn"
            disabled={isBusy}
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  )
}

export default ChatInput

