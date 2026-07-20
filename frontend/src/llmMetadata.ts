import type { Chunk } from './api'

export interface LlmSuggestion {
  value: unknown
  model: string
  promptVersion: string
  sourceTextSha256: string
  generatedAt: string
  status: string
}

export function parseLlmSuggestion(value: unknown): LlmSuggestion {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {
      value,
      model: '',
      promptVersion: '',
      sourceTextSha256: '',
      generatedAt: '',
      status: 'suggested',
    }
  }
  const record = value as Record<string, unknown>
  return {
    value: record.value,
    model: stringValue(record.model),
    promptVersion: stringValue(record.prompt_version),
    sourceTextSha256: stringValue(record.source_text_sha256),
    generatedAt: stringValue(record.generated_at),
    status: stringValue(record.status) || 'suggested',
  }
}

export function suggestionItems(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).map(item => item.trim()).filter(Boolean)
  if (value == null || value === '') return []
  return [String(value)]
}

export function hasLlmMetadata(chunk: Chunk): boolean {
  return suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.keywords).value).length > 0
    || suggestionItems(parseLlmSuggestion(chunk.metadata_llm?.questions).value).length > 0
}

export function suggestionVersion(chunk: Chunk): string {
  const metadata = chunk.metadata_llm || {}
  for (const key of ['questions', 'keywords']) {
    const version = parseLlmSuggestion(metadata[key]).promptVersion
    if (version) return version
  }
  return ''
}

export function suggestionGeneratedAt(chunk: Chunk): string {
  const metadata = chunk.metadata_llm || {}
  for (const key of ['questions', 'keywords']) {
    const generatedAt = parseLlmSuggestion(metadata[key]).generatedAt
    if (generatedAt) return generatedAt
  }
  return ''
}

export function formatGeneratedAt(value: string): string {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function shortHash(value: string): string {
  return value ? `${value.slice(0, 10)}…` : '未记录'
}

export async function textSha256(value: string): Promise<string> {
  const canonical = value.replace(/\s+/g, ' ').trim()
  const bytes = new TextEncoder().encode(canonical)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}
