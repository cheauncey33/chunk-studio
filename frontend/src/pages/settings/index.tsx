import { Link } from 'react-router-dom'
import { SettingsPage as LegacySettingsPage } from '@/SettingsPage'
import { Explain } from '@/components/explain'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useDevMode } from '@/lib/dev-mode'
import { helpText } from '@/lib/help-text'
import { AuditDefaultsCard } from '@/pages/settings/assistant-template'

export default function SettingsWorkspacePage() {
  const [devMode, setDevMode] = useDevMode()

  return (
    <div className="h-full overflow-auto px-8 py-7">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Explain text={helpText.settings.page} title="设置">
            <h1 className="text-[18px] font-semibold tracking-tight">设置</h1>
          </Explain>
          <p className="mt-1.5 text-[15px] leading-relaxed text-text-secondary">
            全系统共用：默认审查助手、通用模板、大模型、文字识别、检索总开关。改完记得点保存。
          </p>
        </div>
        <Explain text={helpText.settings.fields} title="字段配置">
          <Button asChild variant="outline">
            <Link to="/settings/fields">字段配置</Link>
          </Button>
        </Explain>
      </div>

      <AuditDefaultsCard />

      <Card className="mb-5 border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle className="text-[17px]">开发者模式</CardTitle>
          <CardDescription className="text-[15px]" title={helpText.settings.devMode}>
            打开后，知识库「审查配置」里可编辑检测项目、型号规则、检索规划、判定等步骤的系统提示词。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <label className="inline-flex items-center gap-2 text-[15px] text-text-primary">
            <input
              type="checkbox"
              className="size-4 rounded border-border-button"
              checked={devMode}
              onChange={e => setDevMode(e.target.checked)}
            />
            启用开发者模式
          </label>
        </CardContent>
      </Card>

      <Card className="border-border-button bg-bg-base">
        <CardHeader>
          <CardTitle className="text-[17px]">运行参数</CardTitle>
          <CardDescription className="text-[15px]">切片上能填哪些业务字段，请点右上角「字段配置」。</CardDescription>
        </CardHeader>
        <CardContent>
          <LegacySettingsPage />
        </CardContent>
      </Card>
    </div>
  )
}
