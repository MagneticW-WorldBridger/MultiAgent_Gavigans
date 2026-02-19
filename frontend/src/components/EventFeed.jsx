/**
 * EventFeed - Live streaming events display
 */

function ThinkingIndicator() {
  return (
    <div className="thinking-indicator">
      <div className="thinking-dots">
        <span className="thinking-dot" />
        <span className="thinking-dot" />
        <span className="thinking-dot" />
      </div>
    </div>
  )
}

export function EventFeed({ isStreaming }) {
  if (!isStreaming) return null

  return <ThinkingIndicator />
}

export default EventFeed

