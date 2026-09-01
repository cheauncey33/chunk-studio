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
  const authority = source === 'programmatic_table' || source === 'programmatic_formula'
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
  const closed = (authority === 'programmatic_table' || authority === 'programmatic_formula')
    && (verdict === 'supported' || verdict === 'mismatch')
  return { verdict, authority, closed, bindState, reasonCode }
}

export function caseAuthorityLabel(item: Record<string, unknown>): string {
  const layer = caseStatusLayer(item)
  if (layer.closed) {
    return AUTHORITY_LABELS[layer.authority] || '程序闭合'
  }
  if (layer.authority === 'model') {
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
  const full = caseRequirementText(item)
  if (!full) return '—'
  const parts = full.split(/[:：]/)
  if (parts.length >= 2) {
    const tail = parts.slice(1).join(':').trim()
    if (tail) return tail
  }
  return full
}

export function caseEvidenceList(item: Record<string, unknown>): CaseEvidence[] {
  const judgment = asRecord(item.judgment)
  const raw = Array.isArray(judgment.evidence) ? judgment.evidence : []
  return raw.map((entry, index) => {
    const row = asRecord(entry)
    const meta = asRecord(row.business_metadata)
    const locator = asRecord(row.locator)
    const standardNo = String(
      locator.standard_no || meta.standard_no || '',
    ).trim()
    const section = String(locator.section || meta.section || '').trim()
    const sectionTitle = String(
      locator.section_title || meta.section_title || '',
    ).trim()
    const tableNo = String(locator.table_no || meta.table_no || '').trim()
    const tableTitle = String(locator.table_title || meta.table_title || '').trim()
    const key = String(row.candidate_key || `e${index + 1}`)
    const place = tableNo
      ? `表 ${tableNo}${tableTitle ? ` ${tableTitle}` : ''}`
      : [section && `§${section}`, sectionTitle].filter(Boolean).join(' ')
    const citation = [standardNo, place].filter(Boolean).join(' · ') || key
    const pageStart = locator.page_start ?? meta.page_start
    const pageEnd = locator.page_end ?? meta.page_end
    let pages = ''
    if (pageStart != null && pageEnd != null && pageStart !== pageEnd) {
      pages = `p.${pageStart}–${pageEnd}`
    } else if (pageStart != null) {
      pages = `p.${pageStart}`
    }
    return {
      key,
      title: citation,
      citation,
      text: String(row.text || '').trim(),
      pages,
    }
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

/** Best-effort standard-side value from adopted evidence (not a separate model field). */
export function caseStandardValueDisplay(item: Record<string, unknown>): string {
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
