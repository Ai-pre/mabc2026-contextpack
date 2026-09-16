import './AgentComposer.css'
import { useRef, useEffect, useState } from 'react'

function AgentComposer({
  role,
  task,
  onRoleChange,
  onTaskChange,
  onBuild,
  onLoadDemo,
  onBlur,
  disabled,
  demoDisabled,
  isProcessing,
  buildDisabled,
  helperText,
  showRole,
  showTask,
}) {
  const roleRef = useRef(null)
  const textareaRef = useRef(null)
  const [roleFocused, setRoleFocused] = useState(false)

  useEffect(() => {
    if (roleRef.current && role === '') {
      roleRef.current.focus()
    }
  }, [])

  // Auto-grow textarea
  const handleTaskInput = (e) => {
    onTaskChange(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 280) + 'px'
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      onBuild()
    }
  }

  return (
    <div className="composer">
      {/* Role pill */}
      <div className="composer-role-row" style={{ opacity: showRole ? 1 : 0.3, pointerEvents: showRole ? 'auto' : 'none' }}>
        <div className={`role-pill ${roleFocused ? 'role-pill--focused' : ''}`}>
          <span className="role-icon" aria-hidden="true" />
          <input
              ref={roleRef}
              type="text"
              className="role-input"
              value={role}
              onChange={(e) => onRoleChange(e.target.value)}
              onFocus={() => setRoleFocused(true)}
              onBlur={() => { setRoleFocused(false); onBlur?.() }}
              placeholder="Role"
              aria-label="Role"
              disabled={disabled}
              maxLength={200}
          />
        </div>
      </div>

      {/* Task textarea */}
      <div className="composer-task" style={{ opacity: showTask ? 1 : 0.3, pointerEvents: showTask ? 'auto' : 'none' }}>
        <textarea
          ref={textareaRef}
          className="composer-task-input"
          value={task}
          onChange={handleTaskInput}
          onKeyDown={handleKeyDown}
          onBlur={onBlur}
          placeholder="Describe what you need to work on…"
          disabled={disabled}
          maxLength={4000}
          aria-label="Current Task"
          style={{ minHeight: showTask ? 140 : 80 }}
        />
      </div>

      {/* Actions */}
      <div className="composer-actions" style={{ opacity: 1 }}>
        <button
          type="button"
          className="build-btn"
          onClick={onBuild}
          disabled={disabled || buildDisabled || !role.trim() || !task.trim()}
        >
          {isProcessing ? 'Building...' : 'Build Context →'}
        </button>
        <button
          type="button"
          className="load-demo-btn"
          onClick={onLoadDemo}
          disabled={demoDisabled}
          title="Open the Partial Refund Demo and load its role and task"
        >
          Load Demo
        </button>
      </div>

      {/* Helper */}
      <p className="composer-helper">
        {helperText}
      </p>
    </div>
  )
}

export default AgentComposer

