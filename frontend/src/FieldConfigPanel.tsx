import { useState } from 'react'
import { api, type FieldConfig } from './api'

interface Props {
  fields: FieldConfig[]
  onChanged: () => void
}

export function FieldConfigPanel({ fields, onChanged }: Props) {
  const [draft, setDraft] = useState<FieldConfig | null>(null)

  const blank = (): FieldConfig => ({
    field_key: '',
    display_name: '',
    extract_source: 'manual',
    value_constraint: 'free',
    label_list: [],
    value_type: 'text',
    llm_description: '',
    order_index: fields.length,
    storage_path: '',
    accepted_storage_path: '',
    scope: 'chunk',
    editable: true,
    filterable: true,
    indexable: true,
    visible: true,
  })

  const save = async () => {
    if (!draft || !draft.field_key || !draft.display_name) {
      alert('field_key 和显示名必填')
      return
    }
    try {
      await api.upsertField(draft)
      setDraft(null)
      onChanged()
    } catch (e) {
      alert('保存失败: ' + (e as Error).message)
    }
  }

  const del = async (key: string) => {
    if (!confirm(`删除字段 ${key}?`)) return
    await api.deleteField(key)
    onChanged()
  }

  const editingExisting = !!draft && fields.some(f => f.field_key === draft.field_key)

  return (
    <div className="field-config">
      <section className="field-config-panel">
        <div className="section-head">
          <div>
            <h3>元数据字段配置</h3>
            <p className="muted">
              定义 chunk 的人工字段和自动抽取字段。枚举字段会约束可选标签，便于后续 RAG 入库保持一致。
            </p>
          </div>
          <button onClick={() => setDraft(blank())}>+ 新增字段</button>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>field_key</th>
                <th>显示名</th>
                <th>来源</th>
                <th>约束</th>
                <th>类型</th>
                <th>存储路径</th>
                <th>属性</th>
                <th>标签</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {fields.map(f => (
                <tr key={f.field_key}>
                  <td className="mono">{f.field_key}</td>
                  <td>{f.display_name}</td>
                  <td><span className={`pill ${f.extract_source}`}>{f.extract_source}</span></td>
                  <td>{f.value_constraint}</td>
                  <td>{f.value_type}</td>
                  <td className="mono">{f.storage_path || 'business_metadata.' + f.field_key}</td>
                  <td className="labels-cell">
                    {[
                      f.editable && 'editable',
                      f.visible && 'visible',
                      f.filterable && 'filterable',
                      f.indexable && 'indexable',
                    ].filter(Boolean).join(' / ') || '—'}
                  </td>
                  <td className="labels-cell">{f.value_constraint === 'enum' ? f.label_list.join(' / ') : '—'}</td>
                  <td className="row-actions">
                    <button onClick={() => setDraft({ ...f, label_list: [...f.label_list] })}>编辑</button>
                    <button onClick={() => del(f.field_key)} className="danger">删</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {draft && (
        <div className="modal" role="dialog" aria-modal="true">
          <div className="modal-panel">
            <div className="modal-title">
              <h4>{editingExisting ? '编辑字段' : '新增字段'}</h4>
              <button className="ghost" onClick={() => setDraft(null)}>×</button>
            </div>

            <label>field_key</label>
            <input
              value={draft.field_key}
              onChange={e => setDraft({ ...draft, field_key: e.target.value })}
              disabled={editingExisting}
              placeholder="例如 standard_no"
            />

            <label>显示名</label>
            <input
              value={draft.display_name}
              onChange={e => setDraft({ ...draft, display_name: e.target.value })}
              placeholder="例如 标准号"
            />

            <div className="form-grid">
              <div>
                <label>抽取来源</label>
                <select
                  value={draft.extract_source}
                  onChange={e => setDraft({ ...draft, extract_source: e.target.value as 'manual' | 'auto' | 'llm' })}
                >
                  <option value="manual">manual 手动</option>
                  <option value="auto">auto 正则抽取</option>
                  <option value="llm">llm 小模型抽取</option>
                </select>
              </div>
              <div>
                <label>取值约束</label>
                <select
                  value={draft.value_constraint}
                  onChange={e => setDraft({ ...draft, value_constraint: e.target.value as 'free' | 'enum' })}
                >
                  <option value="free">free 自由文本</option>
                  <option value="enum">enum 标签选择</option>
                </select>
              </div>
              <div>
                <label>值类型</label>
                <select
                  value={draft.value_type}
                  onChange={e => setDraft({ ...draft, value_type: e.target.value as 'text' | 'list' | 'structured' })}
                >
                  <option value="text">text 单值</option>
                  <option value="list">list 多值</option>
                  <option value="structured">structured 结构化</option>
                </select>
              </div>
            </div>

            <label>存储路径</label>
            <input
              value={draft.storage_path}
              onChange={e => setDraft({ ...draft, storage_path: e.target.value })}
              placeholder={draft.extract_source === 'llm' ? `metadata_llm.${draft.field_key || 'keywords'}` : `business_metadata.${draft.field_key || 'standard_no'}`}
            />

            <label>采纳后存储路径</label>
            <input
              value={draft.accepted_storage_path}
              onChange={e => setDraft({ ...draft, accepted_storage_path: e.target.value })}
              placeholder={draft.extract_source === 'llm' ? `business_metadata.${draft.field_key || 'keywords'}` : 'LLM 字段使用，可留空'}
            />

            <div className="field-properties">
              <div className="scope-field">
                <label>适用范围</label>
                <input
                  value={draft.scope}
                  onChange={e => setDraft({ ...draft, scope: e.target.value })}
                  placeholder="chunk"
                />
              </div>
              <fieldset className="field-flags">
                <legend>字段属性</legend>
                <div className="field-flags-grid">
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={draft.editable}
                      onChange={e => setDraft({ ...draft, editable: e.target.checked })}
                    />
                    <span>可编辑 <small>editable</small></span>
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={draft.visible}
                      onChange={e => setDraft({ ...draft, visible: e.target.checked })}
                    />
                    <span>可见 <small>visible</small></span>
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={draft.filterable}
                      onChange={e => setDraft({ ...draft, filterable: e.target.checked })}
                    />
                    <span>可筛选 <small>filterable</small></span>
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={draft.indexable}
                      onChange={e => setDraft({ ...draft, indexable: e.target.checked })}
                    />
                    <span>可索引 <small>indexable</small></span>
                  </label>
                </div>
              </fieldset>
            </div>

            <label>标签列表</label>
            <textarea
              rows={3}
              value={draft.label_list.join('\n')}
              onChange={e => setDraft({
                ...draft,
                label_list: e.target.value.split(/[\n,，]+/).map(s => s.trim()).filter(Boolean),
              })}
              placeholder="枚举字段使用。可用逗号或换行分隔。"
            />

            <label>LLM 描述</label>
            <textarea
              rows={3}
              value={draft.llm_description}
              onChange={e => setDraft({ ...draft, llm_description: e.target.value })}
              placeholder="写给自动抽取的说明，例如：从标准封面或条款文本中抽取标准号。"
            />

            <div className="modal-actions">
              <button onClick={() => setDraft(null)}>取消</button>
              <button onClick={save} className="primary">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
