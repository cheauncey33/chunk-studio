export const QUERY_PLANNER_ROUTE_IDS = [
  'semantic',
  'keyword',
  'table_target',
  'section_target',
] as const

export type QueryPlannerRouteId = (typeof QUERY_PLANNER_ROUTE_IDS)[number]

export interface QueryPlannerRoute {
  id: QueryPlannerRouteId
  enabled: boolean
  label: string
  instruction: string
}

const DEFAULT_ROUTES: QueryPlannerRoute[] = [
  {
    id: 'semantic',
    enabled: true,
    label: '语义改写',
    instruction: '用于寻找能判断该报告要求是否有标准依据的规则或参数证据',
  },
  {
    id: 'keyword',
    enabled: true,
    label: '关键词',
    instruction: '保留标准术语、参数符号、产品条件、试验简称与型号关键片段',
  },
  {
    id: 'table_target',
    enabled: true,
    label: '表格定向',
    instruction: '如果目标证据可能是参数表、限值表或试验电压表，描述希望寻找的表格主题，否则为空字符串',
  },
  {
    id: 'section_target',
    enabled: true,
    label: '章节定向',
    instruction: '如果目标证据可能是规则、公式、方法或适用条件，描述希望寻找的章节主题，否则为空字符串',
  },
]

export function defaultQueryPlannerRoutes(): QueryPlannerRoute[] {
  return DEFAULT_ROUTES.map(item => ({ ...item }))
}

export function resolveQueryPlannerRoutes(payload: unknown): QueryPlannerRoute[] {
  const byId = new Map<string, Partial<QueryPlannerRoute>>()
  if (Array.isArray(payload)) {
    for (const item of payload) {
      if (!item || typeof item !== 'object') continue
      const id = String((item as { id?: unknown }).id || '').trim()
      if (!(QUERY_PLANNER_ROUTE_IDS as readonly string[]).includes(id)) continue
      byId.set(id, item as Partial<QueryPlannerRoute>)
    }
  }

  const out = DEFAULT_ROUTES.map(defaultItem => {
    const raw = byId.get(defaultItem.id) || {}
    const label = String(raw.label || defaultItem.label).trim() || defaultItem.label
    const instruction = String(raw.instruction || defaultItem.instruction).trim()
      || defaultItem.instruction
    const enabled = raw.enabled === undefined ? defaultItem.enabled : Boolean(raw.enabled)
    return {
      id: defaultItem.id,
      enabled,
      label,
      instruction,
    }
  })
  if (!out.some(item => item.enabled)) {
    out[0] = { ...out[0], enabled: true }
  }
  return out
}

export function enabledQueryPlannerRouteIds(payload: unknown): QueryPlannerRouteId[] {
  return resolveQueryPlannerRoutes(payload)
    .filter(item => item.enabled)
    .map(item => item.id)
}

export function looksLikeFullQueryPlannerPrompt(content: string): boolean {
  const text = (content || '').trim()
  if (!text) return false
  const lowered = text.toLowerCase()
  if (!lowered.includes('query planner') && !text.includes('你是')) return false
  const hasRoutes =
    lowered.includes('semantic')
    && lowered.includes('keyword')
    && lowered.includes('table_target')
    && lowered.includes('section_target')
  const hasRole = lowered.includes('query planner') || text.includes('检索表达')
  return hasRoutes && hasRole
}
