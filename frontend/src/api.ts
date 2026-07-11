// Thin API client. All paths are relative (proxied by Vite in dev, same-origin
// in prod).

export const API = '/api'

export interface CSFile {
  id: string
  name: string
  page_count: number
  metadata: Record<string, unknown>
  created_at: string
}

export interface BBox { x: number; y: number; w: number; h: number }

export type ChunkStatus = 'pending' | 'reviewed' | 'approved' | 'rejected'

export interface Chunk {
  id: string
  file_id: string
  page: number
  bbox: BBox
  rotation: number
  crop_path: string | null
  crop_url: string | null
  text: string | null
  text_source: 'digital' | 'manual' | 'ocr' | 'pending'
  metadata: Record<string, unknown>
  business_metadata: Record<string, unknown>
  metadata_llm: Record<string, unknown>
  source_trace: Record<string, unknown>
  chunk_logic: Record<string, unknown>
  relations: Record<string, unknown>
  ui_state: Record<string, unknown>
  indexing: Record<string, unknown>
  status: ChunkStatus
  ocr_status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | null
  ocr_error: string | null
  ocr_job_id: string | null
  created_at: string
  updated_at: string
}

export interface Job {
  id: string
  type: string
  target_type: string
  target_id: string
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  priority: number
  attempts: number
  max_attempts: number
  error: string
  result: Record<string, unknown>
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface DocumentParse {
  id: string
  file_id: string
  provider: string
  status: 'queued' | 'running' | 'done' | 'failed'
  markdown_path: string | null
  raw_zip_path: string | null
  result: Record<string, unknown>
  error: string
  created_at: string
  updated_at: string
}

export interface AutoTableChunkResult {
  file_id: string
  parse_id: string
  dry_run: boolean
  candidates: Array<{
    page: number
    bbox: BBox
    table_bbox: number[]
    caption_bbox: number[] | null
    caption: string
    text: string
    block_index: number
    metadata: Record<string, unknown>
  }>
  created: Chunk[]
  skipped: number
}

export interface AutoSectionChunkResult {
  file_id: string
  parse_id: string
  dry_run: boolean
  candidates: Array<{
    page: number
    bbox: BBox
    text: string
    section: string
    section_title: string
    section_level: number
    section_path: Array<{ section: string; title: string }>
    source_blocks: Array<Record<string, unknown>>
    split_from: string | null
    split_reason: string | null
    chunk_part: number | null
    chunk_parts: number | null
    metadata: Record<string, unknown>
  }>
  created: Chunk[]
  skipped: number
}

export interface AutoImageChunkResult {
  file_id: string
  parse_id: string
  dry_run: boolean
  candidates: Array<{
    page: number
    bbox: BBox
    image_bbox: number[]
    caption_bbox: number[] | null
    caption: string
    block_index: number
    section: string | null
    section_path: Array<{ section: string; title: string }>
    metadata: Record<string, unknown>
  }>
  created: Chunk[]
  skipped: number
}

export interface FieldConfig {
  field_key: string
  display_name: string
  extract_source: 'manual' | 'auto' | 'llm'
  value_constraint: 'free' | 'enum'
  label_list: string[]
  value_type: 'text' | 'list' | 'structured'
  llm_description: string
  order_index: number
  storage_path: string
  accepted_storage_path: string
  scope: string
  editable: boolean
  filterable: boolean
  indexable: boolean
  visible: boolean
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const t = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${t}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  listFiles: () => fetch(`${API}/files`).then(j<CSFile[]>),
  uploadFile: (file: File, metadata: Record<string, unknown> = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('metadata', JSON.stringify(metadata))
    return fetch(`${API}/files`, { method: 'POST', body: fd }).then(j<CSFile>)
  },
  deleteFile: (id: string) => fetch(`${API}/files/${id}`, { method: 'DELETE' }).then(j),
  updateFile: (id: string, body: Partial<{ metadata: Record<string, unknown> }>) =>
    fetch(`${API}/files/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<CSFile>),
  parseFile: (id: string) =>
    fetch(`${API}/files/${id}/parse`, { method: 'POST' }).then(j<Job>),
  listFileParses: (id: string) =>
    fetch(`${API}/files/${id}/parses`).then(j<DocumentParse[]>),

  pageImageUrl: (fileId: string, page: number) =>
    `${API}/files/${fileId}/pages/${page}`,

  listChunks: (fileId?: string) => {
    const qs = fileId ? `?file_id=${encodeURIComponent(fileId)}` : ''
    return fetch(`${API}/chunks${qs}`).then(j<Chunk[]>)
  },
  createChunk: (body: { file_id: string; page: number; bbox: BBox }) =>
    fetch(`${API}/chunks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<Chunk>),
  updateChunk: (id: string, body: Partial<{
    text: string
    metadata: Record<string, unknown>
    business_metadata: Record<string, unknown>
    metadata_llm: Record<string, unknown>
    source_trace: Record<string, unknown>
    chunk_logic: Record<string, unknown>
    relations: Record<string, unknown>
    ui_state: Record<string, unknown>
    indexing: Record<string, unknown>
    text_source: string
    status: ChunkStatus
  }>) =>
    fetch(`${API}/chunks/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<Chunk>),
  deleteChunk: (id: string) => fetch(`${API}/chunks/${id}`, { method: 'DELETE' }).then(j),

  ocrChunk: (id: string) =>
    fetch(`${API}/ocr/${id}`, { method: 'POST' }).then(j<Chunk>),
  ocrChunkSync: (id: string) =>
    fetch(`${API}/ocr/${id}/sync`, { method: 'POST' }).then(j<Chunk>),
  ocrBulk: (fileId: string, opts: { page?: number; pending_only?: boolean } = {}) => {
    const qs = new URLSearchParams({ file_id: fileId })
    if (opts.page != null) qs.set('page', String(opts.page))
    if (opts.pending_only != null) qs.set('pending_only', String(opts.pending_only))
    return fetch(`${API}/ocr/bulk?${qs}`, { method: 'POST' }).then(j<{ queued: number; jobs: Job[] }>)
  },
  autoTableChunks: (fileId: string, opts: {
    parse_id?: string
    dry_run?: boolean
    include_caption?: boolean
    require_caption?: boolean
    max_caption_gap?: number
    skip_existing?: boolean
  } = {}) =>
    fetch(`${API}/auto-chunks/tables/${fileId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    }).then(j<AutoTableChunkResult>),
  autoSectionChunks: (fileId: string, opts: {
    parse_id?: string
    dry_run?: boolean
    target_level?: number
    max_chars?: number
    skip_existing?: boolean
  } = {}) =>
    fetch(`${API}/auto-chunks/sections/${fileId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    }).then(j<AutoSectionChunkResult>),
  autoImageChunks: (fileId: string, opts: {
    parse_id?: string
    dry_run?: boolean
    include_caption?: boolean
    max_caption_gap?: number
    skip_existing?: boolean
  } = {}) =>
    fetch(`${API}/auto-chunks/images/${fileId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    }).then(j<AutoImageChunkResult>),
  listJobs: (params: { target_id?: string; status?: string; type?: string } = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v) qs.set(k, v) })
    const query = qs.toString()
    return fetch(`${API}/jobs${query ? `?${query}` : ''}`).then(j<Job[]>)
  },

  listFields: () => fetch(`${API}/fields`).then(j<FieldConfig[]>),
  upsertField: (f: FieldConfig) =>
    fetch(`${API}/fields/${f.field_key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(f),
    }).then(j<FieldConfig[]>),
  deleteField: (key: string) =>
    fetch(`${API}/fields/${key}`, { method: 'DELETE' }).then(j),

  getSettings: () => fetch(`${API}/settings`).then(j<Record<string, string>>),
  updateSettings: (settings: Record<string, string>) =>
    fetch(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    }).then(j<Record<string, string>>),
}
