import { useState } from 'react'
import { motion } from 'motion/react'
import { sourceLabel } from '../workspaceApi'
import './Sidebar.css'

function Sidebar({
  workspaces,
  selectedId,
  sources,
  onNewPack,
  onSelectWorkspace,
  onAddSources,
  onConnectGitHub,
  onConnectSlack,
  onConnectNotion,
  onRemoveSource,
  disabled,
  hasWorkspace,
  canConnectLiveSources,
  status,
}) {
  const [githubRepo, setGithubRepo] = useState('')
  const [slackChannel, setSlackChannel] = useState('')
  const [notionPage, setNotionPage] = useState('')

  const uploaded = sources.filter((source) => source.source_type === 'upload')
  const connected = sources.filter((source) => source.source_type === 'connector')
  const demo = sources.filter((source) => source.source_type === 'demo')

  const connectorDisabled = disabled || !canConnectLiveSources

  return (
    <motion.aside
      className="sidebar"
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: [0.2, 0, 0, 1] }}
    >
      <div className="sidebar-inner">
        <div className="sidebar-brand">
          <svg className="sidebar-logo" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--accent)" fillOpacity="0.15" />
            <path d="M8 16 L16 8 L24 16 L16 24 Z" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" fill="none" />
            <circle cx="16" cy="16" r="3" fill="var(--accent)" />
          </svg>
          <span className="sidebar-title">ContextPack</span>
        </div>

        <div className="sidebar-divider" />

        <nav className="sidebar-nav" aria-label="ContextPack menu">
          <button type="button" className="sidebar-item" onClick={onNewPack} disabled={disabled}>
            <span className="sidebar-item-icon">+</span>
            <span className="sidebar-item-text">New Context Pack</span>
          </button>

          <div className="sidebar-group-label">Workspaces</div>
          {workspaces.map((workspace) => (
            <button key={workspace.workspace_id} type="button"
              className={`sidebar-item ${workspace.workspace_id === selectedId ? 'sidebar-item--selected' : ''}`}
              onClick={() => onSelectWorkspace(workspace.workspace_id)} disabled={disabled}
              aria-current={workspace.workspace_id === selectedId ? 'page' : undefined} title={workspace.name}>
              <span className="sidebar-item-text">{workspace.name}</span>
              {workspace.is_demo && <span className="sidebar-demo-badge">Demo</span>}
            </button>
          ))}
          {!workspaces.length && <p className="sidebar-empty">Workspaces will appear here.</p>}

          <div className="sidebar-group-label" style={{ marginTop: '14px' }}>Sources</div>
          <button type="button" className="sidebar-item" onClick={onAddSources} disabled={disabled || !hasWorkspace}>
            <span className="sidebar-item-icon">+</span><span className="sidebar-item-text">Add Sources</span>
          </button>
          <p className="sidebar-source-help">PDF, DOCX, TXT, MD, JSON · 10 MiB each</p>

          <div className="sidebar-connector">
            <div className="sidebar-connector-label">GitHub</div>
            <input
              type="text"
              className="sidebar-github-input"
              value={githubRepo}
              onChange={(event) => setGithubRepo(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && githubRepo.trim() && !connectorDisabled) {
                  event.preventDefault()
                  onConnectGitHub(githubRepo.trim())
                }
              }}
              placeholder="owner/repo or GitHub URL"
              aria-label="GitHub repository"
              disabled={connectorDisabled}
            />
            <button
              type="button"
              className="sidebar-connect-btn"
              onClick={() => onConnectGitHub(githubRepo.trim())}
              disabled={connectorDisabled || !githubRepo.trim()}
            >
              Connect GitHub
            </button>
          </div>

          <div className="sidebar-connector">
            <div className="sidebar-connector-label">Slack</div>
            <input
              type="text"
              className="sidebar-github-input"
              value={slackChannel}
              onChange={(event) => setSlackChannel(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && slackChannel.trim() && !connectorDisabled) {
                  event.preventDefault()
                  onConnectSlack(slackChannel.trim())
                }
              }}
              placeholder="Channel URL or C012ABCDEF"
              aria-label="Slack channel"
              disabled={connectorDisabled}
            />
            <button
              type="button"
              className="sidebar-connect-btn"
              onClick={() => onConnectSlack(slackChannel.trim())}
              disabled={connectorDisabled || !slackChannel.trim()}
            >
              Connect Slack
            </button>
          </div>

          <div className="sidebar-connector">
            <div className="sidebar-connector-label">Notion</div>
            <input
              type="text"
              className="sidebar-github-input"
              value={notionPage}
              onChange={(event) => setNotionPage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && notionPage.trim() && !connectorDisabled) {
                  event.preventDefault()
                  onConnectNotion(notionPage.trim())
                }
              }}
              placeholder="Notion page URL or page ID"
              aria-label="Notion page"
              disabled={connectorDisabled}
            />
            <button
              type="button"
              className="sidebar-connect-btn"
              onClick={() => onConnectNotion(notionPage.trim())}
              disabled={connectorDisabled || !notionPage.trim()}
            >
              Connect Notion
            </button>
          </div>

          <p className="sidebar-source-help">
            Live connectors use server-side read-only credentials. Demo workspace remains isolated.
          </p>

          {connected.length > 0 && <>
            <div className="sidebar-source-group">Connected <span>{connected.length}</span></div>
            <div className="sidebar-sources">
              {connected.map((source) => (
                <div key={source.id} className="sidebar-source-row">
                  <span className="sidebar-source-name" title={sourceLabel(source)}>{sourceLabel(source)}</span>
                  <button type="button" className="sidebar-remove-source" onClick={() => onRemoveSource(source)}
                    disabled={disabled} aria-label={`Remove ${sourceLabel(source)}`} title="Disconnect source">×</button>
                </div>
              ))}
            </div>
          </>}

          <div className="sidebar-source-group">Uploaded <span>{uploaded.length}</span></div>
          <div className="sidebar-sources">
            {uploaded.map((source) => (
              <div key={source.id} className="sidebar-source-row">
                <span className="sidebar-source-name" title={[sourceLabel(source), ...(source.warnings || []).map((warning) => warning.message || String(warning))].join('\n')}>
                  {sourceLabel(source)}{source.warnings?.length > 0 ? ' ⚠' : ''}
                </span>
                <button type="button" className="sidebar-remove-source" onClick={() => onRemoveSource(source)}
                  disabled={disabled} aria-label={`Remove ${sourceLabel(source)}`} title="Remove source">×</button>
              </div>
            ))}
            {!uploaded.length && <p className="sidebar-empty">No uploaded files yet.</p>}
          </div>

          {demo.length > 0 && <>
            <div className="sidebar-source-group">Demo fixtures <span>{demo.length}</span></div>
            <div className="sidebar-sources">
              {demo.map((source) => <span key={source.id} className="sidebar-source-dot"><span className="sidebar-source-name">{sourceLabel(source)}</span></span>)}
            </div>
            <p className="sidebar-source-help">Local demo data · no live connections</p>
          </>}
        </nav>

        <div className="sidebar-divider" style={{ margin: '16px 0' }} />

        <div className="sidebar-footer">
          <div className="sidebar-footer-row">
            <span className="sidebar-footer-label">Model</span>
            <span className="sidebar-footer-value">Solar Pro 4</span>
          </div>
          <div className="sidebar-footer-row">
            <span className="sidebar-footer-label">Agent</span>
            <span className="sidebar-footer-value">Hermes</span>
          </div>
          <div className="sidebar-footer-row">
            <span className="sidebar-footer-label">Skill</span>
            <span className="sidebar-footer-value">ContextPack</span>
          </div>
          <div className="sidebar-footer-row">
            <span className="sidebar-footer-label">Status</span>
            <span className={`sidebar-status sidebar-status--${status.toLowerCase()}`}>
              <span className="sidebar-status-dot" aria-hidden="true" />
              {status}
            </span>
          </div>
        </div>
      </div>
    </motion.aside>
  )
}

export default Sidebar
