import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, Loader2, Moon } from 'lucide-react'
import {
  api,
  type AuditBatchDetail,
  type AuditBatchListItem,
} from '@/api'
import { Badge } from '@/components/ui/input'
import { formatAuditTime } from '@/lib/audit-status'
import {
  BATCH_ITEM_STATUS_LABELS,
  BATCH_STATUS_LABELS,
  batchStatusVariant,
  formatBatchCost,
  formatTokenCount,
  isLiveBatchStatus,
  itemStatusVariant,
} from '@/lib/night-batch'

function reportLabel(fileId: string, names: Map<string, string>): string {
  return names.get(fileId) || `报告 ${fileId.slice(0, 8)}…`
}

function ProgressBar({ percent }: { percent: number }) {
  const width = Math.max(0, Math.min(100, Number(percent) || 0))
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[12px] text-text-secondary">
        <span>批次进度</span>
        <span className="tabular-nums">{Math.round(width)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-border-button">
        <div
          className="h-full rounded-full bg-accent-primary transition-[width] duration-500"
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  )
}

function UsageLine(props: {
  totalTokens: number
  knownCostMicrounits: number
  costMicrounits: number | null
  costComplete: boolean
}) {
  return (
    <p className="text-[13px] text-text-secondary">
      Token {formatTokenCount(props.totalTokens)}
      <span className="mx-1.5 text-border-button">·</span>
      成本 {formatBatchCost({
        known_cost_microunits: props.knownCostMicrounits,
        cost_microunits: props.costMicrounits,
        cost_complete: props.costComplete,
      })}
    </p>
  )
}

function BatchListRow({
  batch,
  onSelect,
}: {
  batch: AuditBatchListItem
  onSelect: (id: string) => void
}) {
  return (
    <button
      type="button"
      className="w-full rounded-xl border border-border-button px-3.5 py-2.5 text-left transition hover:bg-bg-canvas"
      onClick={() => onSelect(batch.id)}
    >
      <div className="flex items-center gap-2">
        <Badge variant={batchStatusVariant(batch.status)}>
          {BATCH_STATUS_LABELS[batch.status] || batch.status}
        </Badge>
        <span className="min-w-0 flex-1 truncate text-[15px] font-medium text-text-primary">
          {batch.total} 份报告
        </span>
        <span className="shrink-0 text-[13px] tabular-nums text-text-secondary">
          完成 {batch.completed}
          {batch.failed ? ` · 失败 ${batch.failed}` : ''}
        </span>
      </div>
      <div className="mt-1.5 text-[13px] tabular-nums text-text-secondary">
        预约 {formatAuditTime(batch.scheduled_at)}
      </div>
      <div className="mt-1">
        <UsageLine
          totalTokens={batch.total_tokens}
          knownCostMicrounits={batch.known_cost_microunits}
          costMicrounits={batch.cost_microunits}
          costComplete={batch.cost_complete}
        />
      </div>
    </button>
  )
}

function BatchDetailView({
  detail,
  fileNameById,
  assistantName,
  onBack,
}: {
  detail: AuditBatchDetail
  fileNameById: Map<string, string>
  assistantName: string
  onBack: () => void
}) {
  const failedItems = detail.items.filter(item => item.status === 'failed')
  const otherItems = detail.items.filter(item => item.status !== 'failed')
  const percent = detail.progress?.percent ?? (
    detail.total > 0
      ? Math.round(((detail.completed + detail.failed) / detail.total) * 100)
      : 0
  )

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[13px] text-accent-primary hover:underline"
          onClick={onBack}
        >
          <ChevronLeft className="size-3.5" />
          返回列表
        </button>
        <Badge variant={batchStatusVariant(detail.status)}>
          {BATCH_STATUS_LABELS[detail.status] || detail.status}
        </Badge>
      </div>

      <div className="rounded-xl border border-border-button px-3.5 py-3">
        <p className="text-[15px] font-medium text-text-primary">
          {detail.total} 份报告
          {assistantName ? ` · ${assistantName}` : ''}
        </p>
        <p className="mt-1 text-[13px] tabular-nums text-text-secondary">
          预约 {formatAuditTime(detail.scheduled_at)}
        </p>
        <div className="mt-3">
          <ProgressBar percent={percent} />
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[12px] text-text-secondary">
          <span>完成 {detail.completed}</span>
          <span>·</span>
          <span>失败 {detail.failed}</span>
          {detail.running ? (
            <>
              <span>·</span>
              <span>审查中 {detail.running}</span>
            </>
          ) : null}
          {detail.queued ? (
            <>
              <span>·</span>
              <span>排队 {detail.queued}</span>
            </>
          ) : null}
          {detail.scheduled ? (
            <>
              <span>·</span>
              <span>待开始 {detail.scheduled}</span>
            </>
          ) : null}
        </div>
        <div className="mt-2">
          <UsageLine
            totalTokens={detail.usage?.total_tokens || 0}
            knownCostMicrounits={detail.usage?.known_cost_microunits || 0}
            costMicrounits={detail.usage?.cost_microunits ?? null}
            costComplete={Boolean(detail.usage?.cost_complete)}
          />
        </div>
      </div>

      {failedItems.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[13px] font-medium text-state-error">失败报告</p>
          <ul className="space-y-1">
            {failedItems.map(item => (
              <li
                key={item.id}
                className="flex items-center gap-2 rounded-lg border border-state-error/20 bg-state-error/10 px-3 py-2"
              >
                <span className="min-w-0 flex-1 truncate text-[14px] text-text-primary">
                  {reportLabel(item.report_file_id, fileNameById)}
                </span>
                <Badge variant="error">失败</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}

      {otherItems.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[13px] font-medium text-text-secondary">报告明细</p>
          <ul className="space-y-1">
            {otherItems.map(item => (
              <li
                key={item.id}
                className="flex items-center gap-2 rounded-lg border border-border-button px-3 py-2"
              >
                <span className="min-w-0 flex-1 truncate text-[14px] text-text-primary">
                  {reportLabel(item.report_file_id, fileNameById)}
                </span>
                <Badge variant={itemStatusVariant(item.status)}>
                  {BATCH_ITEM_STATUS_LABELS[item.status] || item.status || '排队中'}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function NightBatchPanel({
  selectedBatchId,
  onSelectBatch,
  fileNameById,
  assistantNames,
}: {
  selectedBatchId: string | null
  onSelectBatch: (id: string | null) => void
  fileNameById: Map<string, string>
  assistantNames: Map<string, string>
}) {
  const listQuery = useQuery({
    queryKey: ['audit-batches'],
    queryFn: () => api.listAuditBatches(50),
    refetchInterval: query => {
      const rows = query.state.data
      return rows?.some(item => isLiveBatchStatus(item.status)) ? 5000 : false
    },
  })

  const detailQuery = useQuery({
    queryKey: ['audit-batch', selectedBatchId],
    queryFn: () => api.getAuditBatch(selectedBatchId as string),
    enabled: Boolean(selectedBatchId),
    refetchInterval: query => (
      isLiveBatchStatus(query.state.data?.status) ? 5000 : false
    ),
  })

  const batches = listQuery.data || []
  const assistantName = useMemo(() => {
    const id = detailQuery.data?.assistant_id
    if (!id) return ''
    return assistantNames.get(id) || ''
  }, [assistantNames, detailQuery.data?.assistant_id])

  if (selectedBatchId) {
    return (
      <div className="min-h-0 flex-1 space-y-3 overflow-auto">
        {detailQuery.isLoading && (
          <p className="flex items-center gap-2 text-[14px] text-text-secondary">
            <Loader2 className="size-3.5 animate-spin" />
            加载批次…
          </p>
        )}
        {detailQuery.isError && (
          <p className="text-[14px] text-state-error">
            {(detailQuery.error as Error).message || '加载批次失败'}
          </p>
        )}
        {detailQuery.data ? (
          <BatchDetailView
            detail={detailQuery.data}
            fileNameById={fileNameById}
            assistantName={assistantName}
            onBack={() => onSelectBatch(null)}
          />
        ) : null}
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 space-y-2.5 overflow-auto">
      {listQuery.isLoading && (
        <p className="flex items-center gap-2 text-[14px] text-text-secondary">
          <Loader2 className="size-3.5 animate-spin" />
          加载批次…
        </p>
      )}
      {listQuery.isError && (
        <p className="text-[14px] text-state-error">
          {(listQuery.error as Error).message || '加载批次失败'}
        </p>
      )}
      {!listQuery.isLoading && !batches.length && (
        <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-border-button px-6 py-12 text-center text-[15px] leading-relaxed text-text-secondary">
          <div>
            <Moon className="mx-auto mb-3 size-6 text-accent-primary" />
            还没有夜间批次。勾选报告并选择开始时间后，点「预约夜间审查」。
          </div>
        </div>
      )}
      {batches.map(batch => (
        <BatchListRow
          key={batch.id}
          batch={batch}
          onSelect={onSelectBatch}
        />
      ))}
    </div>
  )
}
