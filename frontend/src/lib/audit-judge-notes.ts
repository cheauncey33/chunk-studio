export function looksLikeFullAuditJudgePrompt(content: string): boolean {
  const text = (content || '').trim()
  if (!text) return false
  const lowered = text.toLowerCase()

  const hasCurrentStatuses =
    lowered.includes('supported')
    && lowered.includes('mismatch')
    && lowered.includes('insufficient_context')
    && lowered.includes('not_audited')
  const hasLegacyStatuses =
    lowered.includes('correct')
    && lowered.includes('incorrect')
    && lowered.includes('insufficient_context')
    && lowered.includes('evidence_not_found')
  const hasStatuses = hasCurrentStatuses || hasLegacyStatuses

  const hasRole =
    text.includes('候选')
    || text.includes('标准要求')
    || lowered.includes('evidence_candidate_keys')
    || (text.includes('审查') && text.includes('判断'))
    || text.includes('不得用常识')
  const hasOutput =
    lowered.includes('evidence_candidate_keys')
    || lowered.includes('"status"')
    || text.includes('严格输出')

  return hasStatuses && hasRole && (hasOutput || text.length > 400)
}
