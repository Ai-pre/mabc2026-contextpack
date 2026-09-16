import './MessageBubble.css'

function MessageBubble({ text }) {
  return (
    <div className="msg-user">
      <div className="msg-bubble msg-bubble--user">
        <pre className="msg-text">{text}</pre>
      </div>
    </div>
  )
}

export default MessageBubble
