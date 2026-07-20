import { useEffect, useState } from 'react'
import { api } from './api'

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

  const Field = ({ k, label, type = 'text' }: { k: string; label: string; type?: string }) => (
    <div className="field">
      <label>{label}</label>
      <input type={type} value={s[k] || ''} onChange={e => set(k, e.target.value)} />
    </div>
  )
  const dualRetrievalEnabled = (s['retrieval.lexical_production_enabled'] || 'true') === 'true'

  return (
    <div className="settings">
      <h3>设置</h3>
      <p className="muted">LLM 走 OpenAI 兼容接口。OCR 是通用 HTTP 适配器（MinerU / PaddleOCR 均可配）。
        注意：MinerU 桌面版大概率只收整篇 PDF，区域 OCR 推荐指向 PaddleOCR/RapidOCR。</p>

      <h4>LLM 抽取</h4>
      <Field k="llm.base_url" label="Base URL" />
      <Field k="llm.api_key" label="API Key" type="password" />
      <Field k="llm.model" label="Model" />
      <div className="field">
        <label>响应格式</label>
        <select value={s['llm.response_format'] || 'json_object'} onChange={e => set('llm.response_format', e.target.value)}>
          <option value="json_schema">json_schema (优先, enum 约束)</option>
          <option value="json_object">json_object</option>
          <option value="plain">plain (解析)</option>
        </select>
      </div>

      <h4>OCR / MinerU 云端解析</h4>
      <p className="muted">手动框选切片时，若 PDF 内嵌文本不可用，将同步调用 MinerU 云端解析（裁剪图 → 1 页 PDF → 返回 markdown）。
        token 也可放后端 .env 的 MINERU_TOKEN。</p>
      <Field k="mineru.token" label="MinerU Token" type="password" />
      <Field k="mineru.base_url" label="Base URL (默认 https://mineru.net)" />
      <Field k="mineru.model_version" label="模型 (vlm / txt)" />

      <h4>检索</h4>
      <label className="setting-toggle">
        <input
          type="checkbox"
          checked={dualRetrievalEnabled}
          onChange={e => set('retrieval.lexical_production_enabled', e.target.checked ? 'true' : 'false')}
        />
        <span>启用 Dense + FTS5 双路生产检索</span>
      </label>
      <label className="setting-toggle">
        <input
          type="checkbox"
          disabled={dualRetrievalEnabled}
          checked={(s['retrieval.lexical_shadow_enabled'] || 'true') === 'true'}
          onChange={e => set('retrieval.lexical_shadow_enabled', e.target.checked ? 'true' : 'false')}
        />
        <span>启用 FTS5 Shadow（纯 Dense 模式）</span>
      </label>

      <button onClick={save}>保存设置</button>
      {saved && <span className="ok">已保存</span>}
    </div>
  )
}
