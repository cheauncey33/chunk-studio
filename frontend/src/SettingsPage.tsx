import { useEffect, useState } from 'react'
import { api } from './api'
import { Explain } from '@/components/explain'
import { helpText } from '@/lib/help-text'

/** LLM + OCR settings. Keys match backend KNOWN_KEYS. api_key/token are
 * masked on GET; we only send them back if the user types a fresh value. */
export function SettingsPage() {
  const [s, setS] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)

  useEffect(() => { api.getSettings().then(setS) }, [])

  const set = (k: string, v: string) => { setS(prev => ({ ...prev, [k]: v })); setSaved(false) }

  const save = async () => {
    await api.updateSettings(s)
    const fresh = await api.getSettings()
    setS(fresh)
    setSaved(true)
  }

  const Field = ({ k, label, help, type = 'text' }: { k: string; label: string; help: string; type?: string }) => (
    <div className="field">
      <Explain text={help} title={label}>
        <label>{label}</label>
      </Explain>
      <input type={type} value={s[k] || ''} onChange={e => set(k, e.target.value)} />
    </div>
  )
  const dualRetrievalEnabled = (s['retrieval.lexical_production_enabled'] || 'true') === 'true'

  return (
    <div className="legacy-surface settings">
      <p className="muted">
        这里改的是整套系统共用的参数。一般只需填好大模型和文字识别，并保持检索开关开启。
      </p>

      <Explain text={helpText.settings.llm} title="大模型">
        <h4>大模型（DeepSeek）</h4>
      </Explain>
      <Field k="llm.base_url" label="服务地址" help="大模型接口地址，一般用默认即可。" />
      <Field k="llm.api_key" label="API 密钥" help="访问大模型的密钥。页面上会打码，只有你重新输入时才会覆盖。" type="password" />
      <Field k="llm.model" label="模型名称" help="用哪个模型做审查。不确定时保留默认。" />

      <Explain text={helpText.settings.ocr} title="文字识别">
        <h4>文字识别（OCR / MinerU）</h4>
      </Explain>
      <p className="muted">
        扫描件 PDF、或框选区域里文字读不出来时，会走这里。密钥也可放在服务器环境变量里。
      </p>
      <Field k="mineru.token" label="MinerU 令牌" help="文字识别服务的访问令牌。" type="password" />
      <Field k="mineru.base_url" label="服务地址" help="文字识别服务的网址，一般用默认。" />
      <Field k="mineru.model_version" label="识别模式" help="vlm = 视觉识别；txt = 文本优先。不确定时用默认。" />

      <Explain text={helpText.settings.retrievalToggle} title="检索">
        <h4>检索</h4>
      </Explain>
      <label className="setting-toggle">
        <input
          type="checkbox"
          checked={dualRetrievalEnabled}
          onChange={e => set('retrieval.lexical_production_enabled', e.target.checked ? 'true' : 'false')}
        />
        <Explain text={helpText.settings.retrievalToggle} title="双路检索">
          <span>启用「语义 + 关键词」双路检索（推荐保持开启）</span>
        </Explain>
      </label>
      <label className="setting-toggle">
        <input
          type="checkbox"
          disabled={dualRetrievalEnabled}
          checked={(s['retrieval.lexical_shadow_enabled'] || 'true') === 'true'}
          onChange={e => set('retrieval.lexical_shadow_enabled', e.target.checked ? 'true' : 'false')}
        />
        <Explain text={helpText.settings.shadowToggle} title="旁路对照">
          <span>启用旁路对照（给对比实验用；双路开启时会锁定）</span>
        </Explain>
      </label>

      <Explain text={helpText.settings.save} title="保存设置">
        <button onClick={save}>保存设置</button>
      </Explain>
      {saved && <span className="ok">已保存</span>}
    </div>
  )
}
