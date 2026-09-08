import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api, type AuditWorkflowNode, type AuditWorkflowTrace } from '@/api'
import { cn } from '@/lib/utils'

function nodeKindLabel(node: AuditWorkflowNode): string {
  if (node.kind === 'retrieval') return '检索'
  if (node.kind === 'diagnostic') return '诊断'
  return 'AI'
}

function pretty(value: unknown): string {
  if (value == null) return '（空）'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function isPromptWarning(text: string): boolean {
  return /提示词|prompt/i.test(text)
}

function IoPane({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <h4 className="mb-2 shrink-0 text-[13px] font-medium text-[#111827]">{title}</h4>
      <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-xl border border-border-button bg-white p-3 font-mono text-[12px] leading-relaxed text-[#374151]">
        {pretty(value)}
      </pre>
    </div>
  )
}

export function CaseWorkflowTrace({
  reportName,
  caseId,
}: {
  reportName: string
  caseId: string
}) {
  const [trace, setTrace] = useState<AuditWorkflowTrace | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedNodeId, setSelectedNodeId] = useState('')

  useEffect(() => {
    if (!reportName || !caseId) {
      setTrace(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    api
      .getAuditWorkflow(reportName, caseId)
      .then(payload => {
        if (cancelled) return
        setTrace(payload)
        const prefer =
          payload.nodes.find(node => node.id === 'agent_audit')?.id
          || payload.nodes.find(node => node.id === 'audit_judge')?.id
          || payload.nodes.find(node => node.id === 'retrieval')?.id
          || payload.nodes[0]?.id
          || ''
        setSelectedNodeId(prefer)
      })
      .catch(err => {
        if (cancelled) return
        setError((err as Error).message || '加载工作流失败')
        setTrace(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [reportName, caseId])

  const selected =
    trace?.nodes.find(node => node.id === selectedNodeId) || trace?.nodes[0] || null
  const warnings = (trace?.warnings || []).filter(item => !isPromptWarning(item))

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[14px] text-text-secondary">
        <Loader2 className="size-4 animate-spin" />
        加载工作流…
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-[14px] text-state-error">
        {error}
      </div>
    )
  }

  if (!trace || !selected) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-[14px] text-text-secondary">
        请先在左侧选择一条判定。
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border-button px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-[15px] font-semibold text-[#111827]">工作流追踪</h3>
            <p className="mt-0.5 text-[12px] text-text-secondary">
              当前条目的规划 / 检索 / 判定过程，用于优化流水线。
            </p>
          </div>
          <span className="rounded-md bg-bg-canvas px-2 py-1 text-[11px] text-text-secondary">
            {trace.trace_source === 'recorded' ? '运行时记录' : '旧报告还原'}
          </span>
        </div>
        {warnings.length > 0 && (
          <div className="mt-2 space-y-1 rounded-lg border border-[#fde68a] bg-[#fffbeb] px-3 py-2 text-[12px] text-[#92400e]">
            {warnings.map(item => (
              <p key={item}>{item}</p>
            ))}
          </div>
        )}
        <div className="mt-3 flex flex-wrap gap-1.5">
          {trace.nodes.map((node, index) => (
            <button
              key={node.id}
              type="button"
              className={cn(
                'rounded-lg border px-2.5 py-1.5 text-left text-[12px] transition',
                selected.id === node.id
                  ? 'border-[#13c2c2] bg-[#ecfdfd] text-[#0f766e]'
                  : 'border-border-button bg-white text-text-secondary hover:bg-bg-canvas',
              )}
              onClick={() => setSelectedNodeId(node.id)}
            >
              <span className="font-medium text-[#111827]">
                {String(index + 1).padStart(2, '0')} {node.label}
              </span>
              <span className="ml-1.5 opacity-70">{nodeKindLabel(node)}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col px-4 py-3">
        {selected.note ? (
          <p className="mb-2 shrink-0 text-[12px] text-text-secondary">{selected.note}</p>
        ) : null}
        <div className="grid min-h-0 flex-1 gap-3 md:grid-cols-2">
          <IoPane title="输入" value={selected.input} />
          <IoPane title="输出" value={selected.output} />
        </div>
      </div>
    </div>
  )
}
