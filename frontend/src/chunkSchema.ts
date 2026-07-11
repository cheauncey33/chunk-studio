import type { Chunk, FieldConfig } from './api'
import type { ChunkKind } from './ChunkList'

export function getBusinessMetadata(chunk: Chunk): Record<string, unknown> {
  const business = chunk.business_metadata || {}
  if (Object.keys(business).length) return business
  return migrateLegacyBusiness(chunk.metadata || {})
}

export function getSourceTrace(chunk: Chunk): Record<string, unknown> {
  return { ...pickLegacyTrace(chunk.metadata || {}), ...(chunk.source_trace || {}) }
}

export function getChunkLogic(chunk: Chunk): Record<string, unknown> {
  return { ...pickLegacyLogic(chunk.metadata || {}), ...(chunk.chunk_logic || {}) }
}

export function getRelations(chunk: Chunk): Record<string, unknown> {
  return { ...pickLegacyRelations(chunk.metadata || {}), ...(chunk.relations || {}) }
}

export function chunkKind(chunk: Chunk): ChunkKind {
  const type = String(getBusinessMetadata(chunk).content_type || '')
  if (type === 'section') return 'section'
  if (type === 'table') return 'table'
  if (type === 'image') return 'image'
  return 'manual'
}

export function isAutoChunk(chunk: Chunk): boolean {
  return getChunkLogic(chunk).creation_mode === 'auto'
}

export function storagePathForField(field: FieldConfig): string {
  if (field.storage_path) return field.storage_path
  return field.extract_source === 'llm'
    ? `metadata_llm.${field.field_key}`
    : `business_metadata.${field.field_key}`
}

export function acceptedPathForField(field: FieldConfig): string {
  return field.accepted_storage_path || `business_metadata.${field.field_key}`
}

export function getByPath(root: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.').filter(Boolean)
  let current: unknown = root
  for (const part of parts) {
    if (!current || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

export function setByPath<T extends Record<string, unknown>>(root: T, path: string, value: unknown): T {
  const parts = path.split('.').filter(Boolean)
  if (!parts.length) return root
  const next = { ...root }
  let current: Record<string, unknown> = next
  for (const part of parts.slice(0, -1)) {
    const existing = current[part]
    const child = existing && typeof existing === 'object' && !Array.isArray(existing)
      ? { ...(existing as Record<string, unknown>) }
      : {}
    current[part] = child
    current = child
  }
  current[parts[parts.length - 1]] = value
  return next
}

export function suggestionValue(value: unknown): unknown {
  if (value && typeof value === 'object' && 'value' in value) {
    return (value as Record<string, unknown>).value
  }
  return value
}

function migrateLegacyBusiness(metadata: Record<string, unknown>): Record<string, unknown> {
  const out = { ...metadata }
  if (out.table_header !== undefined && out.table_title === undefined) out.table_title = out.table_header
  if (out.figure_header !== undefined && out.figure_title === undefined) out.figure_title = out.figure_header
  delete out.table_header
  delete out.figure_header
  delete out.table_ref
  delete out.figure_ref
  delete out.notes
  delete out.auto_chunk_types
  return out
}

function pickLegacyTrace(metadata: Record<string, unknown>): Record<string, unknown> {
  const out = pick(metadata, [
    'parser',
    'parser_version',
    'parse_id',
    'page_start',
    'page_end',
    'source_blocks',
    'bbox_union',
    'snapshot_path',
    'source_file_hash',
  ])
  if (metadata.mineru_parse_id !== undefined && out.parse_id === undefined) out.parse_id = metadata.mineru_parse_id
  if (metadata.mineru_model !== undefined && out.parser_version === undefined) out.parser_version = metadata.mineru_model
  return out
}

function pickLegacyLogic(metadata: Record<string, unknown>): Record<string, unknown> {
  const out = pick(metadata, [
    'creation_mode',
    'generator',
    'chunk_type',
    'strategy',
    'strategy_version',
    'split',
  ])
  if (metadata.auto_source && out.creation_mode === undefined) {
    out.creation_mode = 'auto'
    out.generator = String(metadata.auto_source).startsWith('mineru') ? 'mineru' : 'rule'
  }
  return out
}

function pickLegacyRelations(metadata: Record<string, unknown>): Record<string, unknown> {
  const references = []
  const tableRefs = Array.isArray(metadata.table_ref) ? metadata.table_ref : []
  const figureRefs = Array.isArray(metadata.figure_ref) ? metadata.figure_ref : []
  for (const item of tableRefs) references.push({ kind: 'table', no: String(item), type: 'references_table' })
  for (const item of figureRefs) references.push({ kind: 'figure', no: String(item), type: 'references_figure' })
  return references.length ? { references } : {}
}

function pick(source: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  keys.forEach(key => {
    if (source[key] !== undefined) out[key] = source[key]
  })
  return out
}
