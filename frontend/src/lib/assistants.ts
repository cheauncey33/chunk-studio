import type { AuditAssistant, AssistantVersion } from '@/api'

export const GENERIC_TEMPLATE_ASSISTANT_ID = 'assistant_audit_template'
export const DEFAULT_OIL_ASSISTANT_ID = 'assistant_oil_transformer_audit'

/** Provenance sources that mean "still a generic template copy; suggest category init". */
const UNSPECIALIZED_PROVENANCE_SOURCES = new Set([
  '',
  'built_in_seed',
  'template_snapshot',
])

export function findAssistantForKnowledgeBase(
  assistants: AuditAssistant[],
  knowledgeBaseId: string,
): AuditAssistant | undefined {
  return assistants.find(
    item =>
      item.id !== GENERIC_TEMPLATE_ASSISTANT_ID &&
      item.knowledge_bases.some(kb => kb.id === knowledgeBaseId),
  )
}

/** True when this KB-bound assistant still looks like an unspecialized template copy. */
export function needsCategoryInit(
  assistantId: string,
  provenance: AssistantVersion['initialization_provenance'] | null | undefined,
): boolean {
  if (
    assistantId === GENERIC_TEMPLATE_ASSISTANT_ID
    || assistantId === DEFAULT_OIL_ASSISTANT_ID
  ) {
    return false
  }
  const source = String(provenance?.source || '').trim()
  return UNSPECIALIZED_PROVENANCE_SOURCES.has(source)
}
