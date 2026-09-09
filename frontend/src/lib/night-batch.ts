/** Night Batch UI helpers. External create always sends a timezone-aware instant. */

export const NIGHT_BATCH_MAX_REPORTS = 200

export const BATCH_STATUS_LABELS: Record<string, string> = {
  scheduled: '已预约',
  running: '进行中',
  completed: '已完成',
  partial_failed: '部分失败',
  failed: '全部失败',
}

export const BATCH_ITEM_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '审查中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const LIVE_BATCH_STATUSES = new Set(['scheduled', 'running'])

export function isLiveBatchStatus(status: string | null | undefined): boolean {
  return LIVE_BATCH_STATUSES.has(String(status || ''))
}

export function batchStatusVariant(
  status: string,
): 'default' | 'secondary' | 'success' | 'warning' | 'error' {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'default'
  if (status === 'partial_failed') return 'warning'
  if (status === 'failed') return 'error'
  return 'secondary'
}

export function itemStatusVariant(
  status: string,
): 'default' | 'secondary' | 'success' | 'warning' | 'error' {
  if (status === 'done') return 'success'
  if (status === 'running') return 'default'
  if (status === 'failed') return 'error'
  if (status === 'cancelled') return 'warning'
  return 'secondary'
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * Format a JS timezone offset (minutes east of UTC) as `+08:00` / `-05:30`.
 * Pass `-date.getTimezoneOffset()` so DST follows the given instant.
 */
export function formatUtcOffset(offsetMinutes: number): string {
  const sign = offsetMinutes >= 0 ? '+' : '-'
  const abs = Math.abs(offsetMinutes)
  return `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`
}

/** Browser offset for a specific instant, e.g. `+08:00` or `-05:30`. */
export function localTimezoneOffset(instant = new Date()): string {
  return formatUtcOffset(-instant.getTimezoneOffset())
}

export function localTimezoneLabel(instant = new Date()): string {
  return `UTC${localTimezoneOffset(instant)}`
}

/** Normalize datetime-local wall clock to `YYYY-MM-DDTHH:MM:SS`. */
export function normalizeDatetimeLocal(localDateTime: string): string {
  const trimmed = localDateTime.trim()
  if (!trimmed) throw new Error('请选择开始时间')
  const withSeconds = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed)
    ? `${trimmed}:00`
    : trimmed
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(withSeconds)) {
    throw new Error('开始时间格式无效')
  }
  return withSeconds
}

/**
 * Offset of the selected wall-clock instant, not "now".
 * DST zones must use the chosen date, or winter vs summer would be wrong.
 */
export function timezoneOffsetForDatetimeLocal(localDateTime: string): string {
  const wall = normalizeDatetimeLocal(localDateTime)
  const instant = new Date(wall)
  if (Number.isNaN(instant.getTime())) throw new Error('开始时间格式无效')
  return localTimezoneOffset(instant)
}

export function localTimezoneLabelForDatetimeLocal(localDateTime: string): string {
  try {
    return `UTC${timezoneOffsetForDatetimeLocal(localDateTime)}`
  } catch {
    return localTimezoneLabel()
  }
}

export function toDatetimeLocalValue(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** Next 23:00 local (today if still upcoming, otherwise tomorrow). */
export function defaultTonightDatetimeLocal(now = new Date()): string {
  const d = new Date(now)
  d.setHours(23, 0, 0, 0)
  if (d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1)
  return toDatetimeLocalValue(d)
}

/**
 * Convert `<input type="datetime-local">` to a timezone-aware ISO instant.
 * Never emit a naive timestamp. Offset follows the selected date (DST-safe).
 */
export function datetimeLocalToAwareIso(localDateTime: string): string {
  const wall = normalizeDatetimeLocal(localDateTime)
  return `${wall}${timezoneOffsetForDatetimeLocal(wall)}`
}

export function isAwareIsoInPast(awareIso: string, now = new Date()): boolean {
  const parsed = Date.parse(awareIso)
  if (Number.isNaN(parsed)) return false
  return parsed <= now.getTime()
}

export function formatUsdFromMicrounits(micro: number | null | undefined): string {
  const dollars = Number(micro || 0) / 1_000_000
  return dollars.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })
}

export function formatBatchCost(opts: {
  known_cost_microunits: number
  cost_microunits: number | null
  cost_complete: boolean
}): string {
  if (opts.cost_complete && opts.cost_microunits != null) {
    return formatUsdFromMicrounits(opts.cost_microunits)
  }
  return `已知 ${formatUsdFromMicrounits(opts.known_cost_microunits)}（不完整）`
}

export function formatTokenCount(count: number | null | undefined): string {
  return Number(count || 0).toLocaleString('zh-CN')
}

function detailFromHttpBody(raw: string): string | null {
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) {
      const first = parsed.detail[0] as { msg?: string } | undefined
      if (first && typeof first.msg === 'string') return first.msg
    }
  } catch {
    return null
  }
  return null
}

export function formatApiError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err || '')
  const match = raw.match(/^\d{3}\s+([\s\S]+)$/)
  const detail = match ? detailFromHttpBody(match[1]) : null
  return (detail || raw).trim() || '请求失败'
}

export function friendlyBatchError(err: unknown, fileNameById: Map<string, string>): string {
  const message = formatApiError(err)
  if (/already has an active audit/i.test(message)) {
    const ids = [...message.matchAll(/[0-9a-f]{8,}/gi)].map(hit => hit[0])
    const names = ids.map(id => fileNameById.get(id) || id).filter(Boolean)
    if (names.length) {
      return `这些报告已有排队或进行中的审查：${names.join('、')}。请等当前任务结束后再预约。`
    }
    return '所选报告已有排队或进行中的审查，请等当前任务结束后再预约。'
  }
  if (/report file not found/i.test(message)) return '有报告找不到，请重新上传后再预约。'
  if (/markdown/i.test(message) || /parse/i.test(message)) {
    return '有报告尚未解析完成，请等解析结束后再预约。'
  }
  return message
}
