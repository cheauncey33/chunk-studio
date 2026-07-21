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

export interface VectorSearchHit {
  chunk_id: string
  score: number
  rerank_score?: number | null
  rrf_score?: number | null
  route_ranks?: Record<string, number>
  retrieval_sources?: string[]
  source_ranks?: Record<string, number>
  file_id: string
  file_name: string
  page: number
  crop_url: string | null
  text: string
  business_metadata: Record<string, unknown>
  source_trace: Record<string, unknown>
}

export interface VectorSearchResponse {
  query: string
  model: string
  dimension: number
  total_candidates: number
  candidate_count?: number
  retrieval_mode?: string
  query_routes?: Record<string, string>
  rerank_model?: string | null
  degraded?: string[]
  hits: VectorSearchHit[]
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  status: 'active' | 'archived'
  is_default: boolean
  parser_config: Record<string, unknown>
  retrieval_config: Record<string, unknown>
  file_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseFile extends CSFile {
  role: 'source' | 'reference'
  enabled: boolean
  chunk_count: number
  approved_count?: number
  parse_status?: string | null
  parse_error?: string
  parse_ready?: boolean
}

export interface KnowledgeBaseChunk {
  id: string
  file_id: string
  file_name: string
  page: number
  text: string
  status: ChunkStatus
  business_metadata: Record<string, unknown>
  source_trace: Record<string, unknown>
  updated_at: string
}

export interface AuditAssistant {
  id: string
  name: string
  description: string
  status: 'draft' | 'active' | 'archived'
  active_version_id: string | null
  active_version: number | null
  knowledge_bases: Array<{ id: string; name: string }>
  created_at: string
  updated_at: string
}

export interface AssistantVersion {
  id: string
  assistant_id: string
  version: number
  status: 'draft' | 'active' | 'retired'
  model_config: Record<string, unknown>
  node_prompts: Record<string, { path?: string; content?: string }>
  rules: Record<string, unknown>
  retrieval_config: Record<string, unknown>
  created_at: string
  activated_at: string | null
}

export interface AuditReportListItem {
  name: string
  kind: string
  size_bytes: number
  modified_at: number
  summary: Record<string, unknown> | null
  case_count: number | null
  parse_error: string | null
  version?: unknown
  scope?: unknown
  retrieval_policy?: unknown
}

export interface AuditReportListResponse {
  reports: AuditReportListItem[]
}

export interface AuditReportDetail {
  name: string
  kind: string
  size_bytes: number
  modified_at: number
  payload: Record<string, unknown>
}

export interface ManualKnowledgeRules {
  version: number
  scope: string
  status: string
  rules: Array<Record<string, unknown>>
}

export interface AuditWorkflowPrompt {
  path: string
  content: string
  source: 'recorded_report' | 'current_repository'
}

export interface AuditWorkflowNode {
  id: string
  label: string
  kind: 'llm' | 'retrieval' | 'diagnostic'
  diagnostic_only: boolean
  configuration: Record<string, unknown>
  prompt: AuditWorkflowPrompt | null
  input: unknown
  output: unknown
  note: string
}

export interface AuditWorkflowTrace {
  version: number
  case_id: string
  trace_source: 'recorded' | 'reconstructed'
  warnings: string[]
  nodes: AuditWorkflowNode[]
}

export interface LexicalIndexStatus {
  enabled: boolean
  production_enabled: boolean
  shadow_enabled: boolean
  fts5_available: boolean
  tokenizer_version: string
  approved_chunks: number
  indexed_chunks: number
  fts_rows: number
  pending_chunks: number
}

export interface RetrievalShadowHit {
  chunk_id: string
  content_type: string
  score: number
  rrf_score: number
  route_ranks: Record<string, number>
  matched_fields: Record<string, string[]>
  file_name: string
  page: number
  standard_no?: string | null
  section?: string | null
  section_title?: string | null
  table_no?: string | null
  table_title?: string | null
  text_excerpt: string
}

export interface RetrievalShadowRun {
  id: string
  query: string
  status: string
  duration_ms: number
  error: string
  created_at: string
  payload: {
    query_routes?: Record<string, string>
    query_tokens?: Record<string, string[]>
    production_hit_ids?: string[]
    lexical_hit_count?: number
    overlap_count?: number
    overlap_ids?: string[]
    lexical_hits?: RetrievalShadowHit[]
  }
}

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const t = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${t}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  listKnowledgeBases: () =>
    fetch(`${API}/knowledge-bases`).then(j<KnowledgeBase[]>),
  createKnowledgeBase: (body: { name: string; description: string }) =>
    fetch(`${API}/knowledge-bases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KnowledgeBase>),
  updateKnowledgeBase: (
    id: string,
    body: Partial<Pick<KnowledgeBase, 'name' | 'description' | 'retrieval_config'>>,
  ) =>
    fetch(`${API}/knowledge-bases/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KnowledgeBase>),
  listKnowledgeBaseFiles: (id: string) =>
    fetch(`${API}/knowledge-bases/${id}/files`).then(j<KnowledgeBaseFile[]>),
  addFileToKnowledgeBase: (knowledgeBaseId: string, fileId: string) =>
    fetch(`${API}/knowledge-bases/${knowledgeBaseId}/files/${fileId}`, {
      method: 'PUT',
    }).then(j<{ ok: boolean }>),
  listKnowledgeBaseChunks: (id: string, limit = 100, offset = 0) =>
    fetch(`${API}/knowledge-bases/${id}/chunks?limit=${limit}&offset=${offset}`)
      .then(j<KnowledgeBaseChunk[]>),
  testKnowledgeBaseRetrieval: (
    id: string,
    body: {
      query: string
      top_k: number
      similarity_threshold: number
      route_top_k?: number
      candidates_per_type?: number
      rrf_k?: number
    },
  ) =>
    fetch(`${API}/knowledge-bases/${id}/retrieval-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<VectorSearchResponse & {
      knowledge_base_id: string
      scoped_file_count: number
      retrieval_params?: Record<string, number>
    }>),

  listAssistants: () => fetch(`${API}/assistants`).then(j<AuditAssistant[]>),
  createAssistant: (body: { name: string; description: string }) =>
    fetch(`${API}/assistants`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AuditAssistant>),
  startAssistantRun: (
    id: string,
    body: { report_file_id: string; naming_rule_file_id?: string | null; report_id?: string },
  ) =>
    fetch(`${API}/assistants/${id}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<Job>),
  getJob: (id: string) => fetch(`${API}/jobs/${id}`).then(j<Job>),
  getActiveAssistantVersion: (id: string) =>
    fetch(`${API}/assistants/${id}/versions/active`).then(j<AssistantVersion>),
  listAssistantVersions: (id: string) =>
    fetch(`${API}/assistants/${id}/versions`).then(j<AssistantVersion[]>),
  createAssistantVersion: (
    id: string,
    body: Pick<AssistantVersion, 'model_config' | 'node_prompts' | 'rules' | 'retrieval_config'>,
  ) =>
    fetch(`${API}/assistants/${id}/versions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, activate: true }),
    }).then(j<AssistantVersion>),
  setAssistantKnowledgeBases: (id: string, knowledgeBaseIds: string[]) =>
    fetch(`${API}/assistants/${id}/knowledge-bases`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
    }).then(j<AuditAssistant>),

  listFiles: () => fetch(`${API}/files`).then(j<CSFile[]>),
  uploadFile: (
    file: File,
    metadata: Record<string, unknown> = {},
    knowledgeBaseId?: string,
  ) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('metadata', JSON.stringify(metadata))
    if (knowledgeBaseId) fd.append('knowledge_base_id', knowledgeBaseId)
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

  listChunks: (fileId?: string, options: { hasLlmSuggestions?: boolean } = {}) => {
    const params = new URLSearchParams()
    if (fileId) params.set('file_id', fileId)
    if (options.hasLlmSuggestions) params.set('has_llm_suggestions', 'true')
    const qs = params.size ? `?${params.toString()}` : ''
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

  searchChunks: (query: string, topK: number) =>
    fetch(`${API}/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, top_k: topK }),
    }).then(j<VectorSearchResponse>),

  getSettings: () => fetch(`${API}/settings`).then(j<Record<string, string>>),
  updateSettings: (settings: Record<string, string>) =>
    fetch(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    }).then(j<Record<string, string>>),

  listAuditReports: () => fetch(`${API}/audit/reports`).then(j<AuditReportListResponse>),
  getAuditReport: (name: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}`).then(j<AuditReportDetail>),
  getAuditWorkflow: (name: string, caseId: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}/workflow/${encodeURIComponent(caseId)}`)
      .then(j<AuditWorkflowTrace>),
  getManualKnowledgeRules: () =>
    fetch(`${API}/audit/manual-rules`).then(j<ManualKnowledgeRules>),
  getLexicalIndexStatus: () =>
    fetch(`${API}/audit/lexical-index`).then(j<LexicalIndexStatus>),
  listRetrievalShadowRuns: (limit = 25) =>
    fetch(`${API}/audit/shadow-runs?limit=${limit}`).then(j<{ runs: RetrievalShadowRun[] }>),
}
