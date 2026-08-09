// Thin API client. All paths are relative (proxied by Vite in dev, same-origin
// in prod).

export const API = '/api'

export type BusinessChart =
  | { type: 'metric'; value: unknown }
  | { type: 'pie' | 'bar'; data: Array<{ label: string; value: number }>; denominator: number }

export interface BusinessOverview {
  metrics: {
    knowledge_bases: number
    files: number
    approved_chunks: number
    audit_reports: number
    audit_cases: number
  }
  status_distribution: Array<{ label: string; value: number }>
  denominator: number
}

export interface BusinessQueryResult {
  answer: string
  route: 'fixed_metric' | 'text2sql'
  question: string
  sql: string
  model: string | null
  columns: string[]
  rows: Array<Record<string, unknown>>
  truncated: boolean
  chart: BusinessChart | null
}

export interface AgentCitation {
  chunk_id?: string
  file_id?: string
  file_name?: string
  page?: number | null
  score?: number | null
  snippet?: string
}

export interface AgentConversation {
  id: string
  assistant_id: string
  title: string
  summary: string
  summary_version: number
  summary_sequence: number
  created_at: string
  updated_at: string
}

export interface AgentChatResponse {
  conversation_id: string
  answer: string
  citations: AgentCitation[]
  charts: BusinessChart[]
  model: string
  stop_reason: string
  turns: number
  tool_calls: number
}

export interface AgentStreamEvent {
  event: string
  data: Record<string, unknown>
}

export interface CSFile {
  id: string
  name: string
  page_count: number
  metadata: Record<string, unknown>
  created_at: string
  parse_status?: string | null
  parse_error?: string
  parse_ready?: boolean
  auto_chunk_error?: string
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

export type AuditJobProgress = {
  stage?: string
  stage_label?: string
  case_done?: number
  case_total?: number
  case_label?: string
  project_name?: string
  percent?: number
  message?: string
  updated_at?: string
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
  result: Record<string, unknown> & { progress?: AuditJobProgress }
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

export interface ManualKnowledgeRules {
  version?: number
  scope?: string
  status?: string
  rules?: Array<Record<string, unknown> & { rule_id?: string; rule_text?: string }>
}

export interface FewShotRules {
  version?: number
  items?: Array<{
    id: string
    node?: string
    title?: string
    input?: string
    output?: string
    note?: string
  }>
}

export interface KnowledgeBase {
  id: string
  name: string
  description: string
  status: 'active' | 'archived'
  is_default: boolean
  parser_config: Record<string, unknown>
  retrieval_config: Record<string, unknown>
  manual_rules: ManualKnowledgeRules
  few_shot_rules: FewShotRules
  default_naming_file_id: string | null
  default_naming_file_name?: string | null
  assistant_id?: string | null
  file_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export type CorpusKind = 'standard' | 'spec'

export interface KnowledgeBaseFile extends CSFile {
  role: 'source' | 'reference'
  corpus_kind: CorpusKind
  enabled: boolean
  chunk_count: number
  approved_count?: number
  parse_status?: string | null
  parse_error?: string
  parse_ready?: boolean
  auto_chunk_error?: string
}

export interface KeywordExtractionSummary {
  model: string
  prompt_version: string
  eligible: number
  extracted: number
  by_content_type?: Record<string, number>
}

export interface EmbeddingsStatus {
  model: string
  dimension: number
  approved_chunks: number
  pending_chunks: number
  embedded_chunks: number
  latest_job: Job | null
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

export interface ParameterSchemaField {
  key: string
  label: string
  required: boolean
  hint: string
}

export interface ParameterSchema {
  version: number
  allow_extra: boolean
  fields: ParameterSchemaField[]
}

export interface AssistantInitDraft {
  assistant_id: string
  status: 'generating' | 'ready' | 'failed' | 'applied' | 'discarded'
  payload: {
    parameter_schema?: ParameterSchema
    report_parameters_prompt?: string
    source_file_ids?: {
      standard?: string[]
      sample_reports?: string[]
    }
    model?: string
    error?: string
  }
  job_id?: string | null
  created_at: string
  updated_at: string
}

export interface AssistantVersion {
  id: string
  assistant_id: string
  version: number
  /** User-editable label; empty means fall back to v{version}. */
  name: string
  /** API convenience: name || `v{version}`. */
  label: string
  status: 'draft' | 'active' | 'retired'
  model_config: Record<string, unknown>
  node_prompts: Record<string, { path?: string; content?: string }>
  rules: Record<string, unknown>
  retrieval_config: Record<string, unknown>
  parameter_schema: ParameterSchema
  /** Deprecated empty object retained for version JSON compatibility. */
  category_profile: Record<string, unknown>
  initialization_provenance: {
    source?: string
    standard_file_ids?: string[]
    sample_report_file_ids?: string[]
    model?: string
    generated_at?: string
    applied_at?: string
    draft_job_id?: string | null
  }
  created_at: string
  activated_at: string | null
}

export interface AuditJudgmentCounts {
  supported?: number
  mismatch?: number
  insufficient_context?: number
  not_audited?: number
  [key: string]: number | undefined
}

export interface AuditReportListItem {
  name: string
  run_id?: string
  kind: string
  size_bytes: number
  modified_at: number
  summary: Record<string, unknown> | null
  case_count: number | null
  parse_error: string | null
  version?: unknown
  scope?: unknown
  retrieval_policy?: unknown
  report_file_id?: string | null
  report_file_name?: string | null
  assistant_id?: string | null
  assistant_name?: string | null
  knowledge_base_id?: string | null
  knowledge_base_name?: string | null
  started_at?: string | null
  finished_at?: string | null
  job_id?: string | null
  job_status?: string | null
  audit_mode?: string | null
  judgments?: AuditJudgmentCounts | null
}

export interface AuditReportListResponse {
  reports: AuditReportListItem[]
}

export interface AuditCaseReview {
  report_name: string
  case_id: string
  status: 'confirmed' | 'corrected'
  corrected_status: string
  note: string
  reviewer: string
  created_at: string
  updated_at: string
}

export interface AuditReportDetail extends AuditReportListItem {
  payload: Record<string, unknown>
  reviews?: Record<string, AuditCaseReview>
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

export type SettingSource = 'db' | 'env' | 'default' | 'unset'

export interface SettingsPayload {
  settings?: Record<string, string>
  sources?: Record<string, string>
  // legacy flat shape
  [key: string]: unknown
}

export interface NormalizedSettings {
  settings: Record<string, string>
  sources: Record<string, SettingSource>
}

function normalizeSettingsPayload(data: SettingsPayload): NormalizedSettings {
  if (data && typeof data === 'object' && data.settings && typeof data.settings === 'object') {
    const sources: Record<string, SettingSource> = {}
    for (const [key, value] of Object.entries(data.sources || {})) {
      if (value === 'db' || value === 'env' || value === 'default' || value === 'unset') {
        sources[key] = value
      }
    }
    return { settings: data.settings, sources }
  }
  const flat: Record<string, string> = {}
  for (const [key, value] of Object.entries(data || {})) {
    if (typeof value === 'string') flat[key] = value
  }
  return { settings: flat, sources: {} }
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
    body: Partial<
      Pick<
        KnowledgeBase,
        | 'name'
        | 'description'
        | 'retrieval_config'
        | 'parser_config'
        | 'manual_rules'
        | 'few_shot_rules'
        | 'default_naming_file_id'
      >
    > & { clear_default_naming_file?: boolean },
  ) =>
    fetch(`${API}/knowledge-bases/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KnowledgeBase>),
  deleteKnowledgeBase: (id: string) =>
    fetch(`${API}/knowledge-bases/${id}`, { method: 'DELETE' }).then(
      j<{ ok: boolean; deleted_file_count: number; deleted_chunk_count: number }>,
    ),
  listKnowledgeBaseFiles: (id: string) =>
    fetch(`${API}/knowledge-bases/${id}/files`).then(j<KnowledgeBaseFile[]>),
  addFileToKnowledgeBase: (knowledgeBaseId: string, fileId: string) =>
    fetch(`${API}/knowledge-bases/${knowledgeBaseId}/files/${fileId}`, {
      method: 'PUT',
    }).then(j<{ ok: boolean }>),
  updateKnowledgeBaseFile: (
    knowledgeBaseId: string,
    fileId: string,
    body: Partial<{ enabled: boolean; role: 'source' | 'reference'; corpus_kind: CorpusKind }>,
  ) =>
    fetch(`${API}/knowledge-bases/${knowledgeBaseId}/files/${fileId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KnowledgeBaseFile>),
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
      file_ids?: string[]
    },
  ) =>
    fetch(`${API}/knowledge-bases/${id}/retrieval-test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<VectorSearchResponse & {
      knowledge_base_id: string
      scoped_file_count: number
      scoped_file_ids?: string[]
      retrieval_params?: Record<string, number>
    }>),

  listAssistants: () => fetch(`${API}/assistants`).then(j<AuditAssistant[]>),
  createAssistant: (body: { name: string; description: string; knowledge_base_id?: string }) =>
    fetch(`${API}/assistants`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AuditAssistant>),
  routeAssistant: (body: { report_file_id: string; model?: string }) =>
    fetch(`${API}/assistants/route`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<{
      assistant_id: string
      knowledge_base_id: string
      confidence: number
      reason: string
      routed_by: string
      fallback_used: boolean
      candidates: Array<Record<string, unknown>>
    }>),
  routeAndRunAssistant: (body: {
    report_file_id: string
    naming_rule_file_id?: string | null
    model?: string
  }) =>
    fetch(`${API}/assistants/route-and-run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<{
      route: {
        assistant_id: string
        knowledge_base_id: string
        confidence: number
        reason: string
        routed_by: string
        fallback_used: boolean
        candidates: Array<Record<string, unknown>>
      }
      job: Job
    }>),
  startAssistantRun: (
    id: string,
    body: { report_file_id: string; naming_rule_file_id?: string | null },
  ) =>
    fetch(`${API}/assistants/${id}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<Job>),
  getJob: (id: string) => fetch(`${API}/jobs/${id}`).then(j<Job>),
  getActiveAssistantVersion: (id: string) =>
    fetch(`${API}/assistants/${id}/versions/active`).then(j<AssistantVersion>),
  updateActiveAssistantVersion: (
    id: string,
    body: Pick<
      AssistantVersion,
      | 'model_config'
      | 'node_prompts'
      | 'rules'
      | 'retrieval_config'
      | 'parameter_schema'
      | 'initialization_provenance'
    >,
  ) =>
    fetch(`${API}/assistants/${id}/versions/active`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AssistantVersion>),
  setAssistantKnowledgeBases: (id: string, knowledgeBaseIds: string[]) =>
    fetch(`${API}/assistants/${id}/knowledge-bases`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
    }).then(j<AuditAssistant>),
  startAssistantInit: (id: string, body: { sample_report_file_ids?: string[]; model?: string } = {}) =>
    fetch(`${API}/assistants/${id}/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<{ job: Job; draft: AssistantInitDraft | null }>),
  getAssistantInitDraft: (id: string) =>
    fetch(`${API}/assistants/${id}/init-draft`).then(async res => {
      if (res.status === 404) return null
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || res.statusText)
      }
      return res.json() as Promise<AssistantInitDraft>
    }),
  updateAssistantInitDraft: (
    id: string,
    body: {
      parameter_schema?: ParameterSchema
      report_parameters_prompt?: string
    },
  ) =>
    fetch(`${API}/assistants/${id}/init-draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AssistantInitDraft>),
  applyAssistantInitDraft: (id: string) =>
    fetch(`${API}/assistants/${id}/init-draft/apply`, { method: 'POST' }).then(
      j<{
        assistant_id: string
        version_id: string
        version: number
        parameter_schema: ParameterSchema
        assistant: AuditAssistant
      }>,
    ),
  discardAssistantInitDraft: (id: string) =>
    fetch(`${API}/assistants/${id}/init-draft/discard`, { method: 'POST' }).then(j<AssistantInitDraft>),
  chatAssistant: (
    id: string,
    body: { message: string; history?: Array<{ role: 'user' | 'assistant'; content: string }> },
  ) =>
    fetch(`${API}/assistants/${id}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<{
      answer: string
      citations: Array<{
        chunk_id?: string
        file_id?: string
        file_name?: string
        page?: number | null
        score?: number | null
        snippet?: string
      }>
      model: string
      retrieval: {
        hit_count: number
        scoped_file_count: number
        top_k: number
        similarity_threshold: number
        degraded: string[]
      }
    }>),
  agentChatAssistant: (
    id: string,
    body: { message: string; conversation_id?: string },
  ) =>
    fetch(`${API}/assistants/${id}/agent-chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AgentChatResponse>),
  listAgentConversations: (id: string) =>
    fetch(`${API}/assistants/${id}/conversations`).then(j<{ items: AgentConversation[] }>),
  getAgentConversation: (assistantId: string, conversationId: string) =>
    fetch(`${API}/assistants/${assistantId}/conversations/${conversationId}`).then(j<AgentConversation & {
      events: Array<{
        event_type: string
        payload: Record<string, unknown>
        sequence: number
      }>
    }>),
  streamAgentChatAssistant: async function* (
    id: string,
    body: { message: string; conversation_id?: string },
  ): AsyncGenerator<AgentStreamEvent> {
    const response = await fetch(`${API}/assistants/${id}/agent-chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const text = await response.text().catch(() => response.statusText)
      throw new Error(`${response.status} ${text}`)
    }
    if (!response.body) throw new Error('SSE response body is unavailable')
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        const eventLine = block.split('\n').find(line => line.startsWith('event:'))
        const dataLine = block.split('\n').find(line => line.startsWith('data:'))
        if (!dataLine) continue
        let data: Record<string, unknown>
        try {
          const parsed: unknown = JSON.parse(dataLine.slice(5).trim())
          data = parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {}
        } catch {
          continue
        }
        yield { event: eventLine?.slice(6).trim() || 'message', data }
      }
      if (done) break
    }
  },

  listFiles: () => fetch(`${API}/files`).then(j<CSFile[]>),
  getFile: (id: string) => fetch(`${API}/files/${id}`).then(j<CSFile>),
  fileContentUrl: (id: string) => `${API}/files/${id}/content`,
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
  updateFile: (id: string, body: Partial<{ name: string; metadata: Record<string, unknown> }>) =>
    fetch(`${API}/files/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<CSFile>),
  extractLlmSuggestionsBulk: (
    body: { force?: boolean; limit?: number; batch_size?: number } = {},
  ) =>
    fetch(`${API}/extract/bulk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KeywordExtractionSummary>),
  extractLlmSuggestionsForChunk: (chunkId: string, body: { force?: boolean } = {}) =>
    fetch(`${API}/extract/${chunkId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<KeywordExtractionSummary & { skipped?: number }>),
  getEmbeddingsStatus: () =>
    fetch(`${API}/embeddings/status`).then(j<EmbeddingsStatus>),
  buildEmbeddings: () =>
    fetch(`${API}/embeddings/build`, { method: 'POST' }).then(j<Job>),
  parseFile: (id: string, opts: { delete_chunks?: boolean; force?: boolean } = {}) =>
    fetch(`${API}/files/${id}/parse`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        delete_chunks: Boolean(opts.delete_chunks),
        force: opts.force !== false,
      }),
    }).then(j<Job>),
  autoChunkFile: (
    id: string,
    body: {
      chunk_config?: Record<string, unknown>
      skip_existing?: boolean
      persist_override?: boolean
    } = {},
  ) =>
    fetch(`${API}/files/${id}/auto-chunk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<{
      total: number
      sections: number
      tables: number
      images: number
      config: Record<string, unknown>
    }>),
  listFileParses: (id: string) =>
    fetch(`${API}/files/${id}/parses`).then(j<DocumentParse[]>),

  pageImageUrl: (fileId: string, page: number, dpi?: number) => {
    const qs = dpi ? `?dpi=${encodeURIComponent(String(dpi))}` : ''
    return `${API}/files/${fileId}/pages/${page}${qs}`
  },
  locateFileText: (
    fileId: string,
    q: string,
    options: { project?: string; requirement?: string } = {},
  ) => {
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (options.project) params.set('project', options.project)
    if (options.requirement) params.set('requirement', options.requirement)
    return fetch(
      `${API}/files/${encodeURIComponent(fileId)}/locate?${params.toString()}`,
    ).then(
      j<{
        file_id: string
        page: number | null
        page_count: number
        score: number
        matched: boolean
        reason: string
      }>,
    )
  },

  listChunks: (fileId?: string, options: { hasLlmSuggestions?: boolean } = {}) => {
    const params = new URLSearchParams()
    if (fileId) params.set('file_id', fileId)
    if (options.hasLlmSuggestions) params.set('has_llm_suggestions', 'true')
    const qs = params.size ? `?${params.toString()}` : ''
    return fetch(`${API}/chunks${qs}`).then(j<Chunk[]>)
  },
  getChunk: (id: string) => fetch(`${API}/chunks/${id}`).then(j<Chunk>),
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

  getSettings: () =>
    fetch(`${API}/settings`).then(j<SettingsPayload>).then(normalizeSettingsPayload),
  updateSettings: (settings: Record<string, string>) =>
    fetch(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    }).then(j<SettingsPayload>).then(normalizeSettingsPayload),

  listAuditReports: (opts?: { history?: boolean; scope?: string }) => {
    const params = new URLSearchParams()
    if (opts?.history) params.set('history', 'true')
    if (opts?.scope) params.set('scope', opts.scope)
    const qs = params.toString()
    return fetch(`${API}/audit/reports${qs ? `?${qs}` : ''}`).then(j<AuditReportListResponse>)
  },
  getAuditReport: (name: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}`).then(j<AuditReportDetail>),
  deleteAuditReport: (name: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}`, { method: 'DELETE' })
      .then(j<{ ok: boolean; removed: string[] }>),
  getAuditWorkflow: (name: string, caseId: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}/workflow/${encodeURIComponent(caseId)}`)
      .then(j<AuditWorkflowTrace>),
  putAuditCaseReview: (
    name: string,
    caseId: string,
    body: { status: 'confirmed' | 'corrected'; corrected_status?: string; note?: string; reviewer?: string },
  ) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}/reviews/${encodeURIComponent(caseId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(j<AuditCaseReview>),
  deleteAuditCaseReview: (name: string, caseId: string) =>
    fetch(`${API}/audit/reports/${encodeURIComponent(name)}/reviews/${encodeURIComponent(caseId)}`, {
      method: 'DELETE',
    }).then(j<{ ok: boolean }>),
  getManualKnowledgeRules: () =>
    fetch(`${API}/audit/manual-rules`).then(j<ManualKnowledgeRules>),
  getLexicalIndexStatus: () =>
    fetch(`${API}/audit/lexical-index`).then(j<LexicalIndexStatus>),
  listRetrievalShadowRuns: (limit = 25) =>
    fetch(`${API}/audit/shadow-runs?limit=${limit}`).then(j<{ runs: RetrievalShadowRun[] }>),
  getBusinessOverview: () =>
    fetch(`${API}/analytics/overview`).then(j<BusinessOverview>),
  queryBusinessData: (question: string) =>
    fetch(`${API}/analytics/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }).then(j<BusinessQueryResult>),
}
