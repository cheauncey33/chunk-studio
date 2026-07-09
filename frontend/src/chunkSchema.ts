import type { Chunk, FieldConfig } from './api'
import type { ChunkKind } from './ChunkList'

export function getChunkMetadata(chunk: Chunk): Record<string, unknown> {
  const metadataV2 = chunk.metadata_v2 || {}
  if (Object.keys(metadataV2).length) return metadataV2
  return chunk.metadata || {}
}

export function getSourceTrace(chunk: Chunk): Record<string, unknown> {
  const legacy = chunk.metadata || {}
  return { ...pickLegacyTrace(legacy), ...(chunk.source_trace || {}) }
}

export function getChunkLogic(chunk: Chunk): Record<string, unknown> {
  const legacy = chunk.metadata || {}
  return { ...pickLegacyLogic(legacy), ...(chunk.chunk_logic || {}) }
}

export function chunkKind(chunk: Chunk): ChunkKind {
  const type = String(getChunkMetadata(chunk).content_type || '')
  if (type === 'section') return 'section'
  if (type === 'table') return 'table'
  if (type === 'image') return 'image'
  return 'manual'
}

export function isAutoChunk(chunk: Chunk): boolean {
  return Boolean(getChunkLogic(chunk).auto_source)
}

export function storagePathForField(field: FieldConfig): string {
  if (field.storage_path) return field.storage_path
  return field.extract_source === 'llm'
    ? `metadata_llm.${field.field_key}`
    : `metadata_v2.${field.field_key}`
}

export function acceptedPathForField(field: FieldConfig): string {
  return field.accepted_storage_path || `metadata_v2.${field.field_key}`
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

function pickLegacyTrace(metadata: Record<string, unknown>): Record<string, unknown> {
  const keys = [
    'mineru_parse_id',
    'mineru_model',
    'mineru_page_idx',
    'mineru_block_index',
    'mineru_table_bbox',
    'mineru_image_bbox',
    'mineru_caption_bbox',
    'source_blocks',
    'page_start',
    'page_end',
    'bbox_union',
    'snapshot_path',
    'source_file_id',
    'source_file_hash',
    'parser',
  ]
  return pick(metadata, keys)
}

function pickLegacyLogic(metadata: Record<string, unknown>): Record<string, unknown> {
  const keys = [
    'auto_source',
    'chunk_type',
    'strategy',
    'strategy_version',
    'split_from',
    'split_reason',
    'chunk_part',
    'chunk_parts',
    'child_sections',
    'parent_section',
    'parent_title',
    'parent_chunk_id',
    'child_chunk_ids',
    'related_chunk_ids',
    'relations',
  ]
  return pick(metadata, keys)
}

function pick(source: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  keys.forEach(key => {
    if (source[key] !== undefined) out[key] = source[key]
  })
  return out
}
