import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { FieldConfigPanel } from '@/FieldConfigPanel'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { queryKeys, useFields } from '@/hooks/use-knowledge-request'

export default function FieldsSettingsPage() {
  const { data: fields = [], isLoading } = useFields()
  const client = useQueryClient()

  return (
    <div className="h-full overflow-auto p-5">
      <div className="mb-5 flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/settings">← 返回设置</Link>
        </Button>
      </div>
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle>字段配置</CardTitle>
          <CardDescription>系统级元数据 schema，影响切片编辑器中的业务字段。</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="py-8 text-sm text-text-secondary">加载中…</div>
          ) : (
            <FieldConfigPanel
              fields={fields}
              onChanged={() => client.invalidateQueries({ queryKey: queryKeys.fields })}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
