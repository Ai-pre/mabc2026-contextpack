import test from 'node:test'
import assert from 'node:assert/strict'
import { parseHandoff, sectionClass } from './utils.js'

test('all eight public Handoff sections retain their evidence and order', () => {
  const headings = ['[TASK]', '[MUST KNOW]', '[CONSTRAINTS]', '[USEFUL IF SPACE ALLOWS]',
    '[UNRESOLVED CONFLICTS]', '[VERIFY BEFORE USE]', '[DO NOT ASSUME]', '[SOURCE MAP]']
  const handoff = headings.map((heading, index) => `${heading}\nEvidence ${index}: 문서 출처`).join('\n\n')
  const sections = parseHandoff(handoff)
  assert.deepEqual(sections.map(section => section.heading), headings)
  sections.forEach((section, index) => assert.equal(section.content.trim(), `Evidence ${index}: 문서 출처`))
  assert.equal(sectionClass('[SOURCE MAP]'), 'sourcemap')
})

test('source text with other bracketed labels stays within the actual section', () => {
  const sections = parseHandoff('[MUST KNOW]\n[policy-v3] Confirmed\n\n[DO NOT ASSUME]\nUnspecified')
  assert.equal(sections.length, 2)
  assert.equal(sections[0].content.trim(), '[policy-v3] Confirmed')
  assert.equal(sections[1].content, 'Unspecified')
})
