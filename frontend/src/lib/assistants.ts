import type { AuditAssistant } from '@/api'

export const GENERIC_TEMPLATE_ASSISTANT_ID = 'assistant_audit_template'
export const DEFAULT_OIL_ASSISTANT_ID = 'assistant_oil_transformer_audit'

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
