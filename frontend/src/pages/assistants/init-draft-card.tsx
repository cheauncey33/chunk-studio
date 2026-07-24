import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Pencil, Sparkles, Upload, CircleHelp } from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  type AssistantInitDraft,
  type CSFile,
  type ParameterSchema,
  type ParameterSchemaField,
} from '@/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input, Label, Textarea } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { queryKeys } from '@/hooks/use-knowledge-request'
import { cn } from '@/lib/utils'

function emptyField(): ParameterSchemaField {
  return { key: '', label: '', required: false, hint: '' }
}

function defaultSchema(): ParameterSchema {
  return {
    version: 1,
    allow_extra: true,
    fields: [{ key: 'model', label: '型号', required: true, hint: '' }],
  }
}

function isParseReady(file: Pick<CSFile, 'parse_ready' | 'parse_status'>): boolean {
  return Boolean(file.parse_ready || file.parse_status === 'done')
}

function isParseFailed(file: Pick<CSFile, 'parse_status'>): boolean {
  return file.parse_status === 'failed'
}

async function waitUntilParsed(fileId: string, maxAttempts = 90): Promise<CSFile> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const hit = await api.getFile(fileId)
    if (isParseReady(hit)) return hit
    if (isParseFailed(hit)) {
      throw new Error(hit.parse_error || '样例报告解析失败（MinerU 超时或未就绪），请重试或先只用标准语料生成')
    }
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  throw new Error('样例报告解析超时，请稍后在文件列表确认解析完成后再选')
}

async function pollInitDraft(
  assistantId: string,
  jobId: string,
  maxAttempts = 120,
): Promise<AssistantInitDraft> {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const job = await api.getJob(jobId)
    const draft = await api.getAssistantInitDraft(assistantId)
    if (job.status === 'failed') {
      throw new Error(job.error || draft?.payload?.error || '初始化失败')
    }
    if (draft && (draft.status === 'ready' || draft.status === 'failed')) {
      if (draft.status === 'failed') {
        throw new Error(draft.payload?.error || '初始化失败')
      }
      return draft
    }
    if (job.status === 'done' && draft?.status === 'ready') return draft
    await new Promise(resolve => setTimeout(resolve, 2000))
  }
  throw new Error('初始化超时，请稍后刷新查看草案')
}

type EditTarget =
  | 'name'
  | 'equipment_type'
  | 'focus'
  | 'notes'
  | 'schema'
  | 'prompt'

function SummaryRow({
  label,
  value,
  actionLabel,
  onAction,
  disabled,
}: {
  label: string
  value: string
  actionLabel?: string
  onAction?: () => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-[#e5e7eb] bg-white px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-[12px] font-medium text-[#6b7280]">{label}</div>
        <div className="mt-0.5 truncate text-[14px] text-[#111827]">{value || '—'}</div>
      </div>
      {onAction && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 shrink-0 rounded-lg"
          disabled={disabled}
          onClick={onAction}
        >
          <Pencil className="size-3.5" />
          {actionLabel || '修改'}
        </Button>
      )}
    </div>
  )
}

export function AssistantInitDraftCard({
  assistantId,
  activeVersion,
  onApplied,
  onJumpToReportParameters,
  bare = false,
}: {
  assistantId: string
  activeVersion?: number | null
  onApplied: (info: { version: number }) => void
  onJumpToReportParameters?: () => void
  /** 嵌套在外层卡片内时去掉自身边框，避免双框。 */
  bare?: boolean
}) {
  const client = useQueryClient()
  const uploadRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [editTarget, setEditTarget] = useState<EditTarget | null>(null)
  const [selectedSampleIds, setSelectedSampleIds] = useState<string[]>([])
  const [sessionSamples, setSessionSamples] = useState<CSFile[]>([])
  const [draft, setDraft] = useState<AssistantInitDraft | null>(null)
  const [appliedVersion, setAppliedVersion] = useState<number | null>(activeVersion ?? null)
  const [profile, setProfile] = useState({
    name: '',
    equipment_type: '',
    focus: '',
    notes: '',
  })
  const [schema, setSchema] = useState<ParameterSchema>(defaultSchema())
  const [prompt, setPrompt] = useState('')

  useEffect(() => {
    if (activeVersion != null) setAppliedVersion(activeVersion)
  }, [activeVersion])

  const filesQuery = useQuery({
    queryKey: ['files', 'init-samples'],
    queryFn: () => api.listFiles(),
    refetchInterval: 5000,
  })

  const sampleCandidates = useMemo(() => {
    const byId = new Map<string, CSFile>()
    for (const file of sessionSamples) byId.set(file.id, file)
    for (const file of filesQuery.data || []) {
      const meta = (file.metadata || {}) as Record<string, unknown>
      const role = String(meta.doc_role || meta.doc_type || '').toLowerCase()
      if (role === 'sample_report' || role === 'report') {
        byId.set(file.id, file)
      }
    }
    return [...byId.values()]
  }, [filesQuery.data, sessionSamples])

  const hydrateFromDraft = (item: AssistantInitDraft) => {
    const p = item.payload?.category_profile || {}
    setProfile({
      name: String(p.name || ''),
      equipment_type: String(p.equipment_type || ''),
      focus: String(p.focus || ''),
      notes: String(p.notes || ''),
    })
    setSchema(item.payload?.parameter_schema || defaultSchema())
    setPrompt(String(item.payload?.report_parameters_prompt || ''))
    const samples = item.payload?.source_file_ids?.sample_reports || []
    if (samples.length) setSelectedSampleIds(samples)
  }

  useEffect(() => {
    let cancelled = false
    api
      .getAssistantInitDraft(assistantId)
      .then(item => {
        if (cancelled || !item) return
        setDraft(item)
        hydrateFromDraft(item)
        if (item.status === 'generating' && item.job_id) {
          setGenerating(true)
          void pollInitDraft(assistantId, item.job_id)
            .then(ready => {
              if (cancelled) return
              setDraft(ready)
              hydrateFromDraft(ready)
              toast.success('草案已生成，请逐项确认后启用')
            })
            .catch(err => {
              if (cancelled) return
              toast.error((err as Error).message)
            })
            .finally(() => {
              if (!cancelled) setGenerating(false)
            })
        }
      })
      .catch(() => {
        /* no draft yet */
      })
    return () => {
      cancelled = true
    }
  }, [assistantId])

  const toggleSample = (fileId: string) => {
    const file = sampleCandidates.find(item => item.id === fileId)
    if (file && isParseFailed(file)) {
      toast.error('该样例解析失败，请重新上传或不要勾选')
      return
    }
    if (file && !isParseReady(file)) {
      toast.error('样例仍在解析，请稍后再选')
      return
    }
    setSelectedSampleIds(prev => {
      if (prev.includes(fileId)) return prev.filter(id => id !== fileId)
      if (prev.length >= 3) {
        toast.error('最多选择 3 份样例报告')
        return prev
      }
      return [...prev, fileId]
    })
  }

  const onUploadSample = async (file: File | null) => {
    if (!file) return
    setUploading(true)
    try {
      const uploaded = await api.uploadFile(file, {
        doc_role: 'sample_report',
        doc_type: 'sample_report',
      })
      setSessionSamples(prev => [uploaded, ...prev.filter(item => item.id !== uploaded.id)])
      toast.message('样例已上传，正在解析…')
      const current = await api.getFile(uploaded.id)
      const parsePending = current.parse_status === 'queued' || current.parse_status === 'running'
      if (!isParseReady(current) && !parsePending) {
        await api.parseFile(uploaded.id)
      }
      const parsed = await waitUntilParsed(uploaded.id)
      setSessionSamples(prev => [parsed, ...prev.filter(item => item.id !== parsed.id)])
      setSelectedSampleIds(prev => {
        if (prev.includes(parsed.id) || prev.length >= 3) return prev
        return [...prev, parsed.id]
      })
      await filesQuery.refetch()
      toast.success('样例报告已解析完成')
    } catch (err) {
      toast.error((err as Error).message)
      await filesQuery.refetch()
    } finally {
      setUploading(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const startInit = async () => {
    const readySelected = selectedSampleIds.filter(id => {
      const file = sampleCandidates.find(item => item.id === id)
      return file && isParseReady(file)
    })
    if (selectedSampleIds.length && readySelected.length !== selectedSampleIds.length) {
      toast.error('所选样例尚未解析完成或已失败，请取消勾选后再生成（也可不选样例，仅用标准语料）')
      return
    }
    setGenerating(true)
    try {
      const started = await api.startAssistantInit(assistantId, {
        sample_report_file_ids: readySelected,
      })
      setDraft(started.draft)
      toast.message(
        readySelected.length
          ? '正在根据标准与样例生成草案…'
          : '未选样例，正在仅根据标准语料生成草案…',
      )
      const ready = await pollInitDraft(assistantId, started.job.id)
      setDraft(ready)
      hydrateFromDraft(ready)
      toast.success('草案已生成，请逐项确认后启用')
    } catch (err) {
      toast.error((err as Error).message)
      const latest = await api.getAssistantInitDraft(assistantId).catch(() => null)
      if (latest) {
        setDraft(latest)
        hydrateFromDraft(latest)
      }
    } finally {
      setGenerating(false)
    }
  }

  const saveDraftEdits = async () => {
    setGenerating(true)
    try {
      const updated = await api.updateAssistantInitDraft(assistantId, {
        category_profile: profile,
        parameter_schema: schema,
        report_parameters_prompt: prompt,
      })
      setDraft(updated)
      toast.success('草案已保存')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const applyDraft = async () => {
    setGenerating(true)
    try {
      await api.updateAssistantInitDraft(assistantId, {
        category_profile: profile,
        parameter_schema: schema,
        report_parameters_prompt: prompt,
      })
      const applied = await api.applyAssistantInitDraft(assistantId)
      await client.invalidateQueries({ queryKey: queryKeys.assistantVersion(assistantId) })
      await client.invalidateQueries({ queryKey: queryKeys.assistantVersions(assistantId) })
      await client.invalidateQueries({ queryKey: queryKeys.assistants })
      const versionNo = Number(applied.version) || 0
      setAppliedVersion(versionNo)
      toast.success(versionNo ? `已启用 v${versionNo}` : '已启用新品类配置版本')
      onApplied({ version: versionNo })
      const latest = await api.getAssistantInitDraft(assistantId)
      setDraft(latest)
      onJumpToReportParameters?.()
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const discardDraft = async () => {
    setGenerating(true)
    try {
      const discarded = await api.discardAssistantInitDraft(assistantId)
      setDraft(discarded)
      toast.success('已丢弃草案')
    } catch (err) {
      toast.error((err as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  const busy = uploading || generating || draft?.status === 'generating'
  const editable = draft?.status === 'ready' || draft?.status === 'failed'
  const canEdit = editable && !busy

  const promptSummary = prompt.trim()
    ? prompt.trim().replace(/\s+/g, ' ').slice(0, 48) + (prompt.trim().length > 48 ? '…' : '')
    : '未填写'

  const dialogTitle: Record<EditTarget, string> = {
    name: '品类名称',
    equipment_type: '设备类型',
    focus: '审查关注点',
    notes: '备注',
    schema: '参数 schema',
    prompt: '报告参数提取提示词',
  }

  return (
    <div
      className={cn(
        'space-y-2',
        !bare && 'rounded-xl border border-[#e5e7eb] bg-[#f8fafc] p-3',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[14px] font-semibold text-[#111827]">
          <Sparkles className="size-4 text-[#2563eb]" />
          初始化
        </h3>
        {draft?.status === 'applied' && (
          <span className="text-[13px] text-[#6b7280]">
            已启用{appliedVersion ? ` v${appliedVersion}` : ''}
          </span>
        )}
      </div>

      {draft?.status === 'applied' ? (
        <details className="rounded-lg border border-[#e5e7eb] bg-white px-3 py-2">
          <summary className="cursor-pointer select-none text-[13px] font-medium text-[#374151]">
            样例与重新生成
            {selectedSampleIds.length ? `（已选 ${selectedSampleIds.length}）` : ''}
          </summary>
          <div className="mt-2 space-y-2 border-t border-[#e5e7eb] pt-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label className="text-[13px]">样例报告</Label>
              <input
                ref={uploadRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={e => void onUploadSample(e.target.files?.[0] || null)}
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8"
                disabled={busy}
                onClick={() => uploadRef.current?.click()}
              >
                {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
                {uploading ? '解析中…' : '上传 PDF'}
              </Button>
            </div>
            <div className="max-h-28 space-y-1 overflow-auto">
              {sampleCandidates.length === 0 && (
                <p className="px-1 py-1 text-[13px] text-[#9ca3af]">暂无样例</p>
              )}
              {sampleCandidates.map(file => {
                const checked = selectedSampleIds.includes(file.id)
                const ready = isParseReady(file)
                const failed = isParseFailed(file)
                return (
                  <label
                    key={file.id}
                    className={cn(
                      'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[13px] hover:bg-[#f3f4f6]',
                      checked && 'bg-[#eff6ff]',
                      failed && 'opacity-60',
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={busy || failed || !ready}
                      onChange={() => toggleSample(file.id)}
                    />
                    <span className="min-w-0 flex-1 truncate">{file.name}</span>
                    <span
                      className={cn(
                        'shrink-0 text-[12px]',
                        ready && 'text-emerald-600',
                        failed && 'text-red-600',
                        !ready && !failed && 'text-[#9ca3af]',
                      )}
                    >
                      {ready ? '已解析' : failed ? '解析失败' : '解析中'}
                    </span>
                  </label>
                )
              })}
            </div>
            <Button size="sm" disabled={busy} onClick={() => void startInit()}>
              {generating ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  生成中…
                </>
              ) : (
                '重新生成草案'
              )}
            </Button>
          </div>
        </details>
      ) : (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label className="text-[13px]">样例报告</Label>
          <input
            ref={uploadRef}
            type="file"
            accept="application/pdf,.pdf"
            className="hidden"
            onChange={e => void onUploadSample(e.target.files?.[0] || null)}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8"
            disabled={busy}
            onClick={() => uploadRef.current?.click()}
          >
            {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
            {uploading ? '解析中…' : '上传 PDF'}
          </Button>
        </div>
        <div className="max-h-28 space-y-1 overflow-auto rounded-lg border border-[#e5e7eb] bg-white p-2">
          {sampleCandidates.length === 0 && (
            <p className="px-1 py-1 text-[13px] text-[#9ca3af]">
              暂无样例，可不选直接生成
            </p>
          )}
          {sampleCandidates.map(file => {
            const checked = selectedSampleIds.includes(file.id)
            const ready = isParseReady(file)
            const failed = isParseFailed(file)
            return (
              <label
                key={file.id}
                className={cn(
                  'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-[13px] hover:bg-[#f3f4f6]',
                  checked && 'bg-[#eff6ff]',
                  failed && 'opacity-60',
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={busy || failed || !ready}
                  onChange={() => toggleSample(file.id)}
                />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
                <span
                  className={cn(
                    'shrink-0 text-[12px]',
                    ready && 'text-emerald-600',
                    failed && 'text-red-600',
                    !ready && !failed && 'text-[#9ca3af]',
                  )}
                >
                  {ready ? '已解析' : failed ? '解析失败' : '解析中'}
                </span>
              </label>
            )
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy} onClick={() => void startInit()}>
            {generating || draft?.status === 'generating' ? (
              <>
                <Loader2 className="size-3.5 animate-spin" />
                生成中…
              </>
            ) : (
              '生成草案'
            )}
          </Button>
          {draft && (
            <span className="text-[13px] text-[#6b7280]">
              状态：{draft.status}
              {draft.payload?.error ? ` · ${draft.payload.error}` : ''}
            </span>
          )}
        </div>
      </div>
      )}

      {draft?.status === 'ready' && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">
          草案已生成。点「修改」逐项确认后启用。
        </div>
      )}

      {editable && (
        <div className="space-y-1.5">
          <SummaryRow
            label="品类名称"
            value={profile.name}
            actionLabel="修改"
            onAction={() => setEditTarget('name')}
          />
          <SummaryRow
            label="设备类型"
            value={profile.equipment_type}
            actionLabel="修改"
            onAction={() => setEditTarget('equipment_type')}
          />
          <SummaryRow
            label="审查关注点"
            value={profile.focus}
            actionLabel="修改"
            onAction={() => setEditTarget('focus')}
          />
          <SummaryRow
            label="备注"
            value={profile.notes}
            actionLabel="修改"
            onAction={() => setEditTarget('notes')}
          />
          <SummaryRow
            label="参数 schema"
            value={`${schema.fields.length} 个字段${schema.allow_extra ? ' · 允许额外' : ''}`}
            actionLabel="修改"
            onAction={() => setEditTarget('schema')}
          />
          <SummaryRow
            label="报告参数提示词"
            value={promptSummary}
            actionLabel="修改"
            onAction={() => setEditTarget('prompt')}
          />
        </div>
      )}

      {editable && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void saveDraftEdits()}>
            保存草案
          </Button>
          <Button size="sm" disabled={busy || !prompt.trim()} onClick={() => void applyDraft()}>
            确认并启用
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void discardDraft()}>
            丢弃
          </Button>
        </div>
      )}

      <Dialog open={editTarget !== null} onOpenChange={open => !open && setEditTarget(null)}>
        <DialogContent
          className={cn(
            'flex max-h-[min(720px,90vh)] flex-col gap-0 overflow-hidden p-0',
            editTarget === 'schema' || editTarget === 'prompt'
              ? 'w-[min(720px,92vw)] max-w-none'
              : 'sm:max-w-md',
          )}
          aria-describedby={undefined}
        >
          <DialogHeader className="shrink-0 border-b border-[#e5e7eb] px-5 py-4">
            <DialogTitle>{editTarget ? dialogTitle[editTarget] : ''}</DialogTitle>
            <DialogDescription className="sr-only">
              {canEdit ? '编辑后关闭即写回草案，记得点「保存草案」或「确认并启用」。' : '只读查看'}
            </DialogDescription>
          </DialogHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {editTarget === 'name' && (
              <div className="space-y-2">
                <Label>品类名称</Label>
                <Input
                  value={profile.name}
                  disabled={!canEdit}
                  onChange={e => setProfile(prev => ({ ...prev, name: e.target.value }))}
                />
              </div>
            )}
            {editTarget === 'equipment_type' && (
              <div className="space-y-2">
                <Label>设备类型</Label>
                <Input
                  value={profile.equipment_type}
                  disabled={!canEdit}
                  onChange={e => setProfile(prev => ({ ...prev, equipment_type: e.target.value }))}
                />
              </div>
            )}
            {editTarget === 'focus' && (
              <div className="space-y-2">
                <Label>审查关注点</Label>
                <Textarea
                  className="min-h-[120px]"
                  value={profile.focus}
                  disabled={!canEdit}
                  onChange={e => setProfile(prev => ({ ...prev, focus: e.target.value }))}
                />
              </div>
            )}
            {editTarget === 'notes' && (
              <div className="space-y-2">
                <Label>备注</Label>
                <Textarea
                  className="min-h-[120px]"
                  value={profile.notes}
                  disabled={!canEdit}
                  onChange={e => setProfile(prev => ({ ...prev, notes: e.target.value }))}
                />
              </div>
            )}

            {editTarget === 'schema' && (
              <div className="space-y-3">
                {canEdit && (
                  <div className="flex items-center gap-1.5 text-[13px] text-[#6b7280]">
                    <label className="inline-flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={schema.allow_extra}
                        onChange={e => setSchema(prev => ({ ...prev, allow_extra: e.target.checked }))}
                      />
                      允许额外字段
                    </label>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          className="inline-flex text-[#9ca3af] hover:text-[#6b7280]"
                          aria-label="允许额外字段说明"
                        >
                          <CircleHelp className="size-3.5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-[260px] leading-relaxed">
                        开启后，抽参时除了列出的字段，还可保留报告里其它未声明但对审查有用的参数；关闭则只提取已声明字段。
                      </TooltipContent>
                    </Tooltip>
                  </div>
                )}
                <div className="overflow-hidden rounded-lg border border-[#e5e7eb]">
                  <table className="w-full text-left text-[13px]">
                    <thead className="bg-[#f9fafb] text-[#6b7280]">
                      <tr>
                        <th className="px-2 py-1.5 font-medium">key</th>
                        <th className="px-2 py-1.5 font-medium">中文名</th>
                        <th className="px-2 py-1.5 font-medium">必填</th>
                        {canEdit && <th className="px-2 py-1.5 font-medium" />}
                      </tr>
                    </thead>
                    <tbody>
                      {schema.fields.map((field, index) => (
                        <tr key={`${field.key}-${index}`} className="border-t border-[#e5e7eb]">
                          <td className="px-2 py-1.5">
                            {canEdit ? (
                              <Input
                                className="h-8"
                                value={field.key}
                                onChange={e => {
                                  const fields = schema.fields.map((item, i) =>
                                    i === index ? { ...item, key: e.target.value } : item,
                                  )
                                  setSchema({ ...schema, fields })
                                }}
                              />
                            ) : (
                              <code className="text-[12px]">{field.key}</code>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {canEdit ? (
                              <Input
                                className="h-8"
                                value={field.label}
                                onChange={e => {
                                  const fields = schema.fields.map((item, i) =>
                                    i === index ? { ...item, label: e.target.value } : item,
                                  )
                                  setSchema({ ...schema, fields })
                                }}
                              />
                            ) : (
                              field.label
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {canEdit ? (
                              <input
                                type="checkbox"
                                checked={field.required}
                                onChange={e => {
                                  const fields = schema.fields.map((item, i) =>
                                    i === index ? { ...item, required: e.target.checked } : item,
                                  )
                                  setSchema({ ...schema, fields })
                                }}
                              />
                            ) : field.required ? (
                              '是'
                            ) : (
                              '否'
                            )}
                          </td>
                          {canEdit && (
                            <td className="px-2 py-1.5">
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  setSchema({
                                    ...schema,
                                    fields: schema.fields.filter((_, i) => i !== index),
                                  })
                                }
                              >
                                删
                              </Button>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {canEdit && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setSchema({ ...schema, fields: [...schema.fields, emptyField()] })}
                  >
                    添加字段
                  </Button>
                )}
              </div>
            )}

            {editTarget === 'prompt' && (
              <div className="space-y-2">
                <Label>提示词</Label>
                <Textarea
                  className="min-h-[240px] font-mono text-[13px]"
                  value={prompt}
                  disabled={!canEdit}
                  onChange={e => setPrompt(e.target.value)}
                />
              </div>
            )}
          </div>

          <DialogFooter className="shrink-0 border-t border-[#e5e7eb] px-5 py-3">
            <Button type="button" variant="outline" onClick={() => setEditTarget(null)}>
              {canEdit ? '完成' : '关闭'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
