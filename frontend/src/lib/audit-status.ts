export const AUDIT_STATUS_LABELS: Record<string, string> = {
  supported: '符合',
  mismatch: '不符合',
  insufficient_context: '依据不足',
  not_audited: '未审查',
  correct: '符合',
  incorrect: '不符合',
  evidence_not_found: '未找到证据',
  evaluated: '已评测',
  unknown: '待确认',
}

export const AUTHORITY_LABELS: Record<string, string> = {
  programmatic_table: '程序表格',
  programmatic_formula: '程序公式',
  programmatic_caliber: '程序口径',
  model: '模型判定',
  unknown: '',
}

export const BIND_STATE_LABELS: Record<string, string> = {
  unique: '唯一绑定',
  conflict: '绑定冲突',
  unbound: '未绑定',
  not_ready: '不可程序化',
  unknown: '',
}

export type ProblemFilter = 'mismatch' | 'insufficient_context' | 'not_audited'

export const PROBLEM_FILTERS: Array<{ id: ProblemFilter; label: string }> = [
  { id: 'mismatch', label: '不符合' },
  { id: 'insufficient_context', label: '依据不足' },
  { id: 'not_audited', label: '未审查' },
]

export function normalizeAuditStatus(status: string): string {
  const value = String(status || '').trim()
  if (value === 'correct') return 'supported'
  if (value === 'incorrect') return 'mismatch'
  if (value === 'evidence_not_found') return 'not_audited'
  return value || 'unknown'
}

export function statusVariant(status: string): 'success' | 'error' | 'secondary' {
  const normalized = normalizeAuditStatus(status)
  if (normalized === 'supported') return 'success'
  if (normalized === 'mismatch') return 'error'
  return 'secondary'
}

export function formatAuditTime(value: string | number | null | undefined): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'number') {
    try {
      return new Date(value * (value < 1e12 ? 1000 : 1)).toLocaleString('zh-CN', {
        hour12: false,
      })
    } catch {
      return String(value)
    }
  }
  const text = String(value)
  // Accept ISO-like "2026-07-26T01:02:03"
  const parsed = Date.parse(text.includes('T') ? text : text.replace(' ', 'T'))
  if (!Number.isNaN(parsed)) {
    return new Date(parsed).toLocaleString('zh-CN', { hour12: false })
  }
  return text
}

export function caseProjectName(item: Record<string, unknown>, index: number): string {
  const testItem = (item.test_item && typeof item.test_item === 'object'
    ? item.test_item
    : {}) as Record<string, unknown>
  return String(
    testItem.project_name
    || item.test_item_name
    || item.item_name
    || item.title
    || item.case_id
    || `检测项 ${index + 1}`,
  )
}

export function caseRequirementText(item: Record<string, unknown>): string {
  const req = (item.reported_requirement && typeof item.reported_requirement === 'object'
    ? item.reported_requirement
    : {}) as Record<string, unknown>
  return String(req.text || req.requirement_text || item.requirement_text || '').trim()
}

export function caseJudgmentStatus(item: Record<string, unknown>): string {
  const judgment = (item.judgment && typeof item.judgment === 'object'
    ? item.judgment
    : {}) as Record<string, unknown>
  return normalizeAuditStatus(String(judgment.status || item.evaluation_status || 'unknown'))
}

export function caseJudgmentReason(item: Record<string, unknown>): string {
  const judgment = (item.judgment && typeof item.judgment === 'object'
    ? item.judgment
    : {}) as Record<string, unknown>
  return String(judgment.reason || item.reason || '').trim()
}

export type CaseStatusLayer = {
  verdict: string
  authority: string
  closed: boolean
  bindState: string
  reasonCode: string
}

function asLayerRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

export function caseStatusLayer(item: Record<string, unknown>): CaseStatusLayer {
  const judgment = asLayerRecord(item.judgment)
  const attached = asLayerRecord(item.status_layer || judgment.status_layer)
  const deterministic = asLayerRecord(judgment.deterministic_judge)
  const trace = asLayerRecord(asLayerRecord(item.workflow_trace).audit_judge)
  const path = asLayerRecord(trace.table_claim_path)
  const source = String(
    attached.authority
    || judgment.authority
    || deterministic.mode
    || trace.judge_source
    || path.mode
    || '',
  ).trim()
  const authority = source === 'programmatic_table' || source === 'programmatic_formula' || source === 'programmatic_caliber'
    ? source
    : source === 'fallback_llm' || source === 'fallback_llm_rejudge' || source === 'llm' || source === 'model'
      ? 'model'
      : source
        ? 'model'
        : 'unknown'
  const verdict = caseJudgmentStatus(item)
  const reasonCode = String(
    attached.reason_code
    || judgment.bind_reason_code
    || deterministic.reason_code
    || path.reason_code
    || '',
  ).trim()
  const bindMap: Record<string, string> = {
    unique_bound_comparable: 'unique',
    derived_sum_comparable: 'unique',
    agent_retrieved: 'unique',
    conflicting_table_bindings: 'conflict',
    no_authoritative_table_claim: 'unbound',
    derived_sum_incomplete: 'unbound',
    requirement_not_program_ready: 'not_ready',
  }
  const bindState = String(
    attached.bind_state
    || judgment.bind_state
    || bindMap[reasonCode]
    || '',
  ).trim() || 'unknown'
  // C-05 rules a clause out of scope from the reported text alone, so the
  // caliber closes not_audited too; the table paths never reach that verdict.
  const closed = authority === 'programmatic_caliber'
    ? verdict === 'supported' || verdict === 'mismatch' || verdict === 'not_audited'
    : (authority === 'programmatic_table' || authority === 'programmatic_formula')
      && (verdict === 'supported' || verdict === 'mismatch')
  return { verdict, authority, closed, bindState, reasonCode }
}

export const KIND_LABELS: Record<string, string> = {
  exact: '精确一致',
  within_standard: '自行加严',
  unit_equivalent: '单位等价',
  formula_aggregate: '加和口径',
  numeric_looser: '限值放宽',
  numeric_tighter: '自行加严',
  comparator_flip: '比较符反转',
  bandwidth_exceeded: '超出带宽',
  wrong_level: '等级写错',
  wrong_condition: '条件写错',
  wrong_label: '标号写错',
  magnitude_error: '数量级错误',
  standard_not_found: '未找到标准',
  applicability_undetermined: '适用性未定',
}

export function caseAuthorityLabel(item: Record<string, unknown>): string {
  const layer = caseStatusLayer(item)
  const kind = String(asRecord(item.judgment).kind || '').trim()
  const kindLabel = KIND_LABELS[kind] || kind
  if (layer.closed) {
    return AUTHORITY_LABELS[layer.authority] || '程序闭合'
  }
  if (layer.authority === 'model') {
    if (kindLabel) return `模型判定 · ${kindLabel}`
    const bind = BIND_STATE_LABELS[layer.bindState]
    return bind ? `模型判定 · ${bind}` : '模型判定'
  }
  return ''
}

export type CaseEvidence = {
  key: string
  title: string
  citation: string
  text: string
  pages: string
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** Report-side limit / value shown on the requirement line. */
export function caseReportUsedValue(item: Record<string, unknown>): string {
  const judgment = asRecord(item.judgment)
  const reported = String(judgment.reported_value || '').trim()
  if (reported) return reported
  const requirement = asRecord(item.reported_requirement)
  const claim = asRecord(requirement.claim)
  const claimValue = asRecord(claim.value)
  const fromClaim = String(claimValue.raw || claimValue.normalized || '').trim()
  if (fromClaim) {
    const unit = String(claimValue.unit || requirement.unit || '').trim()
    return unit && !fromClaim.includes(unit) ? `${fromClaim} ${unit}` : fromClaim
  }
  const full = caseRequirementText(item)
  if (!full) return '—'
  const parts = full.split(/[:：]/)
  if (parts.length >= 2) {
    const tail = parts.slice(1).join(':').trim()
    if (tail) return tail
  }
  return full
}

function evidencePages(locator: Record<string, unknown>, meta: Record<string, unknown>): string {
  const pageStart = locator.page_start ?? meta.page_start
  const pageEnd = locator.page_end ?? meta.page_end
  if (pageStart != null && pageEnd != null && pageStart !== pageEnd) {
    return `p.${pageStart}–${pageEnd}`
  }
  if (pageStart != null) return `p.${pageStart}`
  return ''
}

function evidenceCitation(
  locator: Record<string, unknown>,
  meta: Record<string, unknown>,
  fallback: string,
): string {
  const standardNo = String(locator.standard_no || meta.standard_no || '').trim()
  const section = String(locator.section || meta.section || '').trim()
  const sectionTitle = String(locator.section_title || meta.section_title || '').trim()
  const tableNo = String(locator.table_no || meta.table_no || '').trim()
  const tableTitle = String(locator.table_title || meta.table_title || '').trim()
  const place = tableNo
    ? `表 ${tableNo}${tableTitle ? ` ${tableTitle}` : ''}`
    : [section && `§${section}`, sectionTitle].filter(Boolean).join(' ')
  return [standardNo, place].filter(Boolean).join(' · ') || fallback
}

function parseAgentEvidenceString(text: string, index: number): CaseEvidence {
  const trimmed = text.trim()
  const citation = trimmed.split(/[：:]/, 1)[0]?.trim() || `证据 ${index + 1}`
  const pageMatch = trimmed.match(/p\.?\s*(\d+)/i) || trimmed.match(/第(\d+)\s*页/)
  return {
    key: `e${index + 1}`,
    title: citation,
    citation,
    text: trimmed,
    pages: pageMatch ? `p.${pageMatch[1]}` : '',
  }
}

export function caseEvidenceList(item: Record<string, unknown>): CaseEvidence[] {
  const judgment = asRecord(item.judgment)
  const raw = Array.isArray(judgment.evidence) ? judgment.evidence : []
  return raw.flatMap((entry, index) => {
    if (typeof entry === 'string') {
      const text = entry.trim()
      return text ? [parseAgentEvidenceString(text, index)] : []
    }
    const row = asRecord(entry)
    if (!Object.keys(row).length) return []
    const meta = asRecord(row.business_metadata)
    const locator = asRecord(row.locator)
    const agentSource = String(row.source || '').trim()
    const agentLocation = String(row.location || '').trim()
    const key = String(row.candidate_key || row.chunk_id || `e${index + 1}`)
    const citation = evidenceCitation(locator, meta, '')
      || [agentSource, agentLocation].filter(Boolean).join(' · ')
      || key
    const text = String(row.text || agentLocation || agentSource || '').trim()
    if (!text && !citation) return []
    return [{
      key,
      title: citation,
      citation,
      text,
      pages: evidencePages(locator, meta),
    }]
  })
}

function stripHtml(text: string): string {
  return text
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(?:p|tr|div|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n+/g, '\n')
    .trim()
}

function extractLimitSnippet(text: string, reportValue: string): string {
  const normalized = stripHtml(text).replace(/\s+/g, ' ').trim()
  if (!normalized) return ''
  // Prefer clause fragments that carry an actual limit, not section titles.
  const chunks = normalized
    .split(/[。；;！？!?]/)
    .flatMap(part => part.split(/(?=对于)/))
    .map(part => part.trim())
    .filter(Boolean)
  const reportNum = reportValue.match(/-?\d+(?:\.\d+)?/)?.[0]
  const scored = chunks.map(sentence => {
    let score = 0
    if (/不应大于|不大于|不超过|≤|≥/.test(sentence)) score += 4
    if (/线电阻|相电阻|不平衡|限值|偏差|试验电压|损耗/.test(sentence)) score += 2
    if (reportNum && sentence.includes(reportNum)) score += 3
    if (/结束后应|检验规则|试验项目|换算/.test(sentence)) score -= 3
    return { sentence, score }
  })
  scored.sort((a, b) => b.score - a.score)
  const best = scored[0]
  if (!best || best.score < 4) return ''
  let snippet = best.sentence.replace(/^[，,、\s]+/, '')
  const focus = snippet.search(/对于|相电阻|线电阻|不应大于|不大于|≤/)
  if (focus > 0 && focus < 40) snippet = snippet.slice(focus)
  return snippet.length > 64 ? `${snippet.slice(0, 64)}…` : snippet
}

/** Best-effort standard-side value: agent field first, else adopted evidence. */
export function caseStandardValueDisplay(item: Record<string, unknown>): string {
  const judgment = asRecord(item.judgment)
  const value = String(judgment.standard_value || '').trim()
  const standardNo = String(judgment.standard_no || '').trim()
  if (value) {
    if (standardNo && !value.includes(standardNo)) return `${standardNo}：${value}`
    return value
  }
  const evidence = caseEvidenceList(item)
  if (!evidence.length) return '—'
  const primary = evidence[0]
  const snippet = extractLimitSnippet(primary.text, caseReportUsedValue(item))
  if (snippet) return `${primary.citation}：${snippet}`
  return primary.citation || '—'
}

/**
 * Compact judgment basis for card display.
 * Source is the model `judgment.reason` — we only take the first full sentence
 * and truncate on clause boundaries (never mid standard-no / mid word).
 */
export function shortJudgmentReason(reason: string, maxLen = 120): string {
  const text = reason.replace(/\s+/g, ' ').trim()
  if (!text) return '暂无判定说明'
  const firstSentence = text.match(/^[\s\S]*?[。！？!?]/)?.[0]?.trim() || text
  let pick = firstSentence.replace(/[。！？.!?]+$/, '')
  if (pick.length <= maxLen) return pick

  const window = pick.slice(0, maxLen)
  // Prefer cutting at Chinese clause marks; avoid slicing inside "GB/T 1094.1".
  const softMarks = [...window.matchAll(/[，、；;]/g)]
  for (let i = softMarks.length - 1; i >= 0; i -= 1) {
    const idx = softMarks[i].index ?? -1
    if (idx >= Math.floor(maxLen * 0.55)) {
      return `${window.slice(0, idx)}…`
    }
  }
  // Fall back: don't cut immediately after a letter/digit (keeps standard nos intact).
  let end = maxLen
  while (end > Math.floor(maxLen * 0.6) && /[A-Za-z0-9./-]/.test(pick[end - 1] || '')) {
    end -= 1
  }
  return `${pick.slice(0, end)}…`
}
