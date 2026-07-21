import { Link } from 'react-router-dom'
import { SettingsPage as LegacySettingsPage } from '@/SettingsPage'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { helpText } from '@/lib/help-text'

export default function SettingsWorkspacePage() {
  return (
    <div className="h-full overflow-auto p-5">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Explain text={helpText.settings.page} title="设置">
            <h1 className="text-lg font-semibold">设置</h1>
          </Explain>
          <p className="mt-1 text-sm text-text-secondary">
            全系统共用：大模型、文字识别、检索总开关。改完记得点保存。
          </p>
        </div>
        <Explain text={helpText.settings.fields} title="字段配置">
          <Button asChild variant="outline">
            <Link to="/settings/fields">字段配置</Link>
          </Button>
        </Explain>
      </div>
      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle className="text-base">运行参数</CardTitle>
          <CardDescription>切片上能填哪些业务字段，请点右上角「字段配置」。</CardDescription>
        </CardHeader>
        <CardContent>
          <LegacySettingsPage />
        </CardContent>
      </Card>
    </div>
  )
}
