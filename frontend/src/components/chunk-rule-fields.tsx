import { Label, Input } from '@/components/ui/input'

export type ChunkRuleConfig = {
  auto_chunk_after_parse: boolean
  sections: { enabled: boolean; target_level: number; max_chars: number }
  tables: { enabled: boolean; include_caption: boolean }
  images: { enabled: boolean; include_caption: boolean; require_caption: boolean }
}

export const DEFAULT_CHUNK_RULES: ChunkRuleConfig = {
  auto_chunk_after_parse: true,
  sections: { enabled: true, target_level: 2, max_chars: 8192 },
  tables: { enabled: true, include_caption: true },
  images: { enabled: true, include_caption: true, require_caption: true },
}

export function normalizeChunkRules(raw: Record<string, unknown> | null | undefined): ChunkRuleConfig {
  const sections = (raw?.sections as Record<string, unknown> | undefined) || {}
  const tables = (raw?.tables as Record<string, unknown> | undefined) || {}
  const images = (raw?.images as Record<string, unknown> | undefined) || {}
  return {
    auto_chunk_after_parse: raw?.auto_chunk_after_parse !== false,
    sections: {
      enabled: sections.enabled !== false,
      target_level: Number(sections.target_level ?? 2),
      max_chars: Number(sections.max_chars ?? 8192),
    },
    tables: {
      enabled: tables.enabled !== false,
      include_caption: tables.include_caption !== false,
    },
    images: {
      enabled: images.enabled !== false,
      include_caption: images.include_caption !== false,
      require_caption: images.require_caption !== false,
    },
  }
}

export function ChunkRuleFields({
  value,
  onChange,
  compact = false,
}: {
  value: ChunkRuleConfig
  onChange: (next: ChunkRuleConfig) => void
  compact?: boolean
}) {
  return (
    <div className={compact ? 'space-y-3' : 'space-y-4'}>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.auto_chunk_after_parse}
          onChange={e => onChange({ ...value, auto_chunk_after_parse: e.target.checked })}
        />
        解析完成后自动切片
      </label>

      <div className="grid gap-3 sm:grid-cols-3">
        <fieldset className="space-y-2 rounded-lg border border-border-button p-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={value.sections.enabled}
              onChange={e =>
                onChange({
                  ...value,
                  sections: { ...value.sections, enabled: e.target.checked },
                })
              }
            />
            章节切片
          </label>
          <div className="space-y-1">
            <Label>目标层级</Label>
            <Input
              type="number"
              min={1}
              max={6}
              disabled={!value.sections.enabled}
              value={value.sections.target_level}
              onChange={e =>
                onChange({
                  ...value,
                  sections: { ...value.sections, target_level: Number(e.target.value) },
                })
              }
            />
          </div>
          <div className="space-y-1">
            <Label>最大字符</Label>
            <Input
              type="number"
              min={512}
              step={512}
              disabled={!value.sections.enabled}
              value={value.sections.max_chars}
              onChange={e =>
                onChange({
                  ...value,
                  sections: { ...value.sections, max_chars: Number(e.target.value) },
                })
              }
            />
          </div>
        </fieldset>

        <fieldset className="space-y-2 rounded-lg border border-border-button p-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={value.tables.enabled}
              onChange={e =>
                onChange({
                  ...value,
                  tables: { ...value.tables, enabled: e.target.checked },
                })
              }
            />
            表格切片
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!value.tables.enabled}
              checked={value.tables.include_caption}
              onChange={e =>
                onChange({
                  ...value,
                  tables: { ...value.tables, include_caption: e.target.checked },
                })
              }
            />
            含表题
          </label>
        </fieldset>

        <fieldset className="space-y-2 rounded-lg border border-border-button p-3">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={value.images.enabled}
              onChange={e =>
                onChange({
                  ...value,
                  images: { ...value.images, enabled: e.target.checked },
                })
              }
            />
            图片切片
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!value.images.enabled}
              checked={value.images.include_caption}
              onChange={e =>
                onChange({
                  ...value,
                  images: { ...value.images, include_caption: e.target.checked },
                })
              }
            />
            含图题
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!value.images.enabled}
              checked={value.images.require_caption}
              onChange={e =>
                onChange({
                  ...value,
                  images: { ...value.images, require_caption: e.target.checked },
                })
              }
            />
            必须有图题
          </label>
        </fieldset>
      </div>
    </div>
  )
}
