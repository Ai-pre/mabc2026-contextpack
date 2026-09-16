export const DEMO_ROLE = '3주 만에 프로젝트에 복귀한 백엔드 개발자'
export const DEMO_TASK = '결제 모듈의 부분환불 기능 수정'

export const SECTION_HEADINGS = [
  '[TASK]',
  '[MUST KNOW]',
  '[CONSTRAINTS]',
  '[USEFUL IF SPACE ALLOWS]',
  '[UNRESOLVED CONFLICTS]',
  '[VERIFY BEFORE USE]',
  '[DO NOT ASSUME]',
  '[SOURCE MAP]',
]

export const SOURCE_BADGES = [
  { name: 'GitHub', re: /\bgithub\b|pr-#?\d+|pull request|diff|src\/|payment_service\.py|refund_service\.py/i },
  { name: 'Jira', re: /\bjira\b|pay-\d+|issue|ticket|story|epic/i },
  { name: 'Slack', re: /\bslack\b|payment-eng|#payments|channel|message|timestamp/i },
  { name: 'Notion', re: /\bnotion\b|아카이브|문서|가이드|아키텍처|정책|요약/i },
]

export const SECTION_RE = new RegExp(
  '^\\[(?:' +
    SECTION_HEADINGS
      .map((h) => h.replace(/^\[|\]$/g, ''))
      .join('|') +
    ')\\]',
  'i'
)

export function parseHandoff(handoff) {
  const sections = []
  const lines = handoff.split('\n')
  let current = null

  for (const line of lines) {
    const m = line.trim().match(SECTION_RE)
    if (m) {
      if (current) sections.push(current)
      current = { heading: m[0], content: '' }
    } else if (current) {
      current.content += (current.content ? '\n' : '') + line
    }
  }
  if (current) sections.push(current)
  return sections
}

export function sectionClass(heading) {
  const h = heading.toUpperCase()
  if (h.includes('UNRESOLVED CONFLICT')) return 'conflict'
  if (h.includes('VERIFY BEFORE USE')) return 'stale'
  if (h.includes('DO NOT ASSUME')) return 'missing'
  if (h.includes('MUST KNOW')) return 'mustknow'
  if (h.includes('SOURCE MAP')) return 'sourcemap'
  return 'default'
}

export function extractSource(heading, content) {
  const text = `${heading} ${content}`.toLowerCase()
  for (const b of SOURCE_BADGES) {
    if (b.re.test(text)) return b.name
  }
  return null
}

export function formatElapsed(sec) {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function extractHandoffText(sections) {
  return sections
    .map((s) => `${s.heading}\n${s.content}`)
    .join('\n\n')
}
