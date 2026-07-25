import type { ManualKnowledgeRules } from '@/api'

export type StepRuleDraft = NonNullable<ManualKnowledgeRules['rules']>[number]

const AI_STEP_IDS = [
  'report_parameters',
  'test_items',
  'model_decode',
  'query_planner',
  'audit_judge',
] as const

export type AiStepId = (typeof AI_STEP_IDS)[number]

export function emptyStepRule(): StepRuleDraft {
  return {
    rule_id: `rule_${Date.now().toString(36)}`,
    rule_text: '',
    domain: 'step_manual_rules',
    rule_type: 'manual',
  }
}

export function normalizeStepRuleDrafts(
  payload: ManualKnowledgeRules | Record<string, unknown> | null | undefined,
): StepRuleDraft[] {
  const rules = Array.isArray(payload?.rules) ? payload.rules : []
  return rules
    .filter((item): item is StepRuleDraft => Boolean(item && typeof item === 'object'))
    .map(item => ({ ...item }))
}

/** Read per-step human rules from assistant_versions.rules.step_rules. */
export function getStepRulesFromVersionRules(
  versionRules: Record<string, unknown> | null | undefined,
  stepId: string,
): StepRuleDraft[] {
  if (!versionRules || typeof versionRules !== 'object') return []
  const stepRules = versionRules.step_rules
  if (!stepRules || typeof stepRules !== 'object' || Array.isArray(stepRules)) return []
  const payload = (stepRules as Record<string, unknown>)[stepId]
  if (!payload || typeof payload !== 'object') return []
  return normalizeStepRuleDrafts(payload as ManualKnowledgeRules)
}

export function setStepRulesOnVersionRules(
  versionRules: Record<string, unknown> | null | undefined,
  stepId: string,
  rules: StepRuleDraft[],
): Record<string, unknown> {
  const base = { ...(versionRules || {}) }
  const prevStepRules =
    base.step_rules && typeof base.step_rules === 'object' && !Array.isArray(base.step_rules)
      ? { ...(base.step_rules as Record<string, unknown>) }
      : {}
  prevStepRules[stepId] = {
    version: 1,
    scope: 'assistant_step_manual_rules',
    status: 'draft',
    rules,
  }
  return { ...base, step_rules: prevStepRules }
}

export function formatStepRulesLines(rules: StepRuleDraft[]): string {
  const lines: string[] = []
  for (const rule of rules) {
    const text = String(rule.rule_text || '').trim()
    if (!text) continue
    const ruleId = String(rule.rule_id || '').trim() || 'rule'
    lines.push(`- ${ruleId}: ${text}`)
  }
  return lines.length ? lines.join('\n') : '（未配置）'
}

export function isAiStepId(stepId: string): stepId is AiStepId {
  return (AI_STEP_IDS as readonly string[]).includes(stepId)
}
