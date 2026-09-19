import { formatElapsed } from '../utils'
import { sourceLabel } from '../workspaceApi'
import './AssistantCard.css'

const WORKFLOW_STAGES = ['Understand task', 'Explore workspace sources', 'Evaluate evidence', 'Build handoff']

function readableMcpTool(name) {
  if (typeof name !== 'string') return String(name || '')
  const parts = name.split('__')
  if (parts.length >= 3 && parts[0] === 'mcp') {
    return `${parts[1].replaceAll('_', '-')} / ${parts.slice(2).join('__')}`
  }
  return name
}

function AssistantCard({ message, onRetry }) {
  const { status, elapsedSec, toolCalls, skillUsed, handoff, error, errorDetail, sources = [], trace = {} } = message
  const mcpTools = Array.isArray(trace.mcp_tools)
    ? [...new Set(trace.mcp_tools.filter(Boolean))]
    : []

  return (
    <div className={`assistant-card assistant-card--${status}`}>
      {/* Identity */}
      <div className="assistant-identity">
        <span className="agent-dot" />
        <span className="agent-name">ContextPack Agent</span>
        <span className="agent-meta">Powered by Solar Pro 4 · Hermes</span>
        <span className={`agent-status agent-status--${status}`}>
          {status === 'loading' && 'Working'}
          {status === 'done' && 'Ready'}
          {status === 'error' && 'Error'}
        </span>
      </div>

      {/* Content */}
      {status === 'loading' && (
        <div className="assistant-content">
          <div className="processing-text" role="status">Working...</div>
          <div className="processing-elapsed">Elapsed {formatElapsed(elapsedSec ?? 0)}</div>

          {/* Workflow stages */}
          <div className="agent-workflow">
            <div className="workflow-label">Agent workflow</div>
            <div className="workflow-steps">
              {WORKFLOW_STAGES.map((s, i) => (
                <div key={s} className="workflow-step">
                  <span className="workflow-step-num">{String(i + 1).padStart(2, '0')}</span>
                  <span className="workflow-step-label">{s}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Sources */}
          <div className="sources-line">
            <span className="sources-line-label">Configured sources</span>
            {sources.map((source) => <span className="source-chip" key={source.id} title={sourceLabel(source)}>{sourceLabel(source)}{source.source_type === 'demo' ? ' · Demo' : ''}</span>)}
          </div>
        </div>
      )}

      {status === 'done' && (
        <div className="assistant-content">
          <div className="result-ready">
            <div className="result-ready-title">Context pack ready.</div>
            <div className="result-preview">
              {handoff ? (
                <pre className="preview-text">{handoff}</pre>
              ) : (
                'ContextPack이 분석할 내용이 없습니다.'
              )}
            </div>
            <div className="result-meta">
              <span>{toolCalls ?? '-'} tool calls</span>
              <span className="meta-sep" />
              <span>{typeof elapsedSec === 'number' ? `${elapsedSec.toFixed(1)} sec` : 'Duration unavailable'}</span>
              <span className="meta-sep" />
              <span>{skillUsed == null ? 'ContextPack Unavailable' : skillUsed ? 'ContextPack Enabled' : 'ContextPack Disabled'}</span>
            </div>
            {mcpTools.length > 0 && (
              <div className="result-tools" title={mcpTools.join('\n')}>
                <span className="result-tools-label">MCP used</span>
                <span>{mcpTools.map(readableMcpTool).join(' · ')}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {status === 'error' && (
        <div className="assistant-content">
          <div className="error-inline" role="alert">Something went wrong. {error}</div>
          {errorDetail && <div className="error-detail">{errorDetail}</div>}
          {onRetry && <button type="button" className="load-demo-btn" onClick={onRetry}>Try Again</button>}
        </div>
      )}
    </div>
  )
}

export default AssistantCard
