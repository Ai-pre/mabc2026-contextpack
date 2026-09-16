import { useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { parseHandoff, sectionClass } from '../utils'
import './ContextInspector.css'

const SECTION_ORDER = [
  '[MUST KNOW]',
  '[UNRESOLVED CONFLICTS]',
  '[VERIFY BEFORE USE]',
  '[DO NOT ASSUME]',
  '[CONSTRAINTS]',
  '[USEFUL IF SPACE ALLOWS]',
  '[TASK]',
  '[SOURCE MAP]',
]

function SectionCard({ heading, content, className: extra }) {
  const bc = sectionClass(heading)
  const lines = content.split('\n').filter(Boolean)

  return (
    <div className={`section-card section-card--${bc} ${extra || ''}`}>
      <div className="section-head">
        <span className="section-label">{heading}</span>
      </div>
      {bc === 'sourcemap' ? (
        <div className="sourcemap-list">
          {lines.map((line, i) => (
            <div key={i} className="sourcemap-item">
              <span className="sourcemap-name">{line}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="section-body">
          {lines.map((line, i) => (
            <div key={i} className="section-line">{line}</div>
          ))}
        </div>
      )}
    </div>
  )
}

function ContextInspector({ handoff, onCopy, copied, mode }) {
  const [activeTab, setActiveTab] = useState('overview')
  const sections = handoff ? parseHandoff(handoff) : []

  // 탭별 섹션 필터링
  const sectionsForTab = (tab) => {
    if (tab === 'overview') {
      return [...sections].sort((a, b) => SECTION_ORDER.indexOf(a.heading.toUpperCase()) - SECTION_ORDER.indexOf(b.heading.toUpperCase()))
    }
    if (tab === 'mustknow') return sections.filter((s) => s.heading.toUpperCase() === '[MUST KNOW]')
    if (tab === 'conflicts') return sections.filter((s) => sectionClass(s.heading) === 'conflict')
    if (tab === 'verify') return sections.filter((s) => sectionClass(s.heading) === 'stale')
    if (tab === 'missing') return sections.filter((s) => sectionClass(s.heading) === 'missing')
    if (tab === 'sources') {
      return sections.filter((s) => s.heading.toUpperCase() === '[SOURCE MAP]')
    }
    return sections
  }

  const tabs = [
    { key: 'overview', label: 'Overview' },
    { key: 'mustknow', label: 'Must Know' },
    { key: 'conflicts', label: 'Conflicts' },
    { key: 'verify', label: 'Verify' },
    { key: 'missing', label: 'Missing' },
    { key: 'sources', label: 'Sources' },
  ]

  const activeSections = sectionsForTab(activeTab)

  return (
    <div className="inspector">
      {/* Header */}
      <div className="inspector-header">
        <div className="inspector-title-row">
          <h2 className="inspector-title">Context Pack</h2>
          <span className={`inspector-status inspector-status--${mode}`}>
            {mode === 'input' && 'Ready'}
            {mode === 'processing' && 'Building...'}
            {mode === 'result' && 'Ready'}
          </span>
        </div>

        {/* Tabs */}
        <div className="inspector-tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.key}
              role="tab"
              className={`inspector-tab ${activeTab === t.key ? 'inspector-tab--active' : ''}`}
              onClick={() => setActiveTab(t.key)}
              aria-selected={activeTab === t.key}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="inspector-scroll">
        <AnimatePresence mode="wait">
          {mode === 'input' && (
            <motion.div
              key="input-empty"
              className="inspector-empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div className="inspector-empty-title">What ContextPack checks</div>
              <div className="inspector-empty-sub">
                This panel will show the structured context pack after analysis:
                <br /><br />
                <strong>Must Know</strong> — essentials for the next agent<br />
                <strong>Conflicts</strong> — contradictory information found<br />
                <strong>Verify</strong> — stale sources that need verification<br />
                <strong>Missing</strong> — information not found anywhere<br />
                <strong>Sources</strong> — where each piece came from
              </div>
            </motion.div>
          )}

          {mode === 'processing' && (
            <motion.div
              key="processing-placeholder"
              className="inspector-empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div className="inspector-empty-title">Building context...</div>
              <div className="inspector-empty-sub">
                The agent is analyzing your task across connected sources.<br />
                This may take a minute or two.
              </div>
            </motion.div>
          )}

          {mode === 'result' && (
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {activeSections.length === 0 ? (
                <div className="inspector-empty">
                  <div className="inspector-empty-title">No {activeTab} items</div>
                  <div className="inspector-empty-sub">
                    No section was provided for this view in the handoff.
                  </div>
                </div>
              ) : (
                <div className="inspector-sections">
                  {activeSections.map((s) => (
                    <SectionCard key={s.heading} heading={s.heading} content={s.content} />
                  ))}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Footer actions */}
      <div className="inspector-footer">
        <button
          type="button"
          className={`copy-handoff-btn ${copied ? 'copy-handoff-btn--copied' : ''}`}
          onClick={onCopy}
          disabled={mode !== 'result'}
        >
          {copied ? 'Copied' : 'Copy Handoff'}
        </button>
        <span className="inspector-ready-badge">
          Ready for Next Agent
        </span>
      </div>
    </div>
  )
}

export default ContextInspector
