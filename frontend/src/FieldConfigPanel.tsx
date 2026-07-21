import { useState } from 'react'
import { api, type FieldConfig } from './api'
import { Explain } from '@/components/explain'
import { helpText } from '@/lib/help-text'

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
      alert('请填写字段标识和显示名')
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
    if (!confirm(`确定删除字段「${key}」？`)) return
    await api.deleteField(key)
    onChanged()
  }

  const editingExisting = !!draft && fields.some(f => f.field_key === draft.field_key)

  return (
    <div className="legacy-surface field-config">
      <section className="field-config-panel">
        <div className="section-head">
          <div>
            <Explain text={helpText.settings.fields} title="业务字段">
              <h3>业务字段列表</h3>
            </Explain>
            <p className="muted">
              这里定义编辑内容时能填哪些信息。例如「标准号」「条款号」。有固定选项的字段可设为枚举，避免大家写法不一致。
            </p>
          </div>
          <Explain text="新增一个可在内容编辑器里填写的业务字段。" title="新增字段">
            <button onClick={() => setDraft(blank())}>+ 新增字段</button>
          </Explain>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <Explain text="系统内部用的唯一标识，创建后尽量不要改。建议用英文小写加下划线，例如 standard_no。" title="字段标识">
                    <span>字段标识</span>
                  </Explain>
                </th>
                <th>
                  <Explain text="界面上显示给使用者的名字，例如「标准号」。" title="显示名">
                    <span>显示名</span>
                  </Explain>
                </th>
                <th>
                  <Explain text="manual=人工填写；auto=系统自动写入；llm=AI 建议后人工采纳。" title="来源">
                    <span>来源</span>
                  </Explain>
                </th>
                <th>
                  <Explain text="free=随便填；enum=只能从给定选项里选。" title="约束">
                    <span>约束</span>
                  </Explain>
                </th>
                <th>类型</th>
                <th>
                  <Explain text="数据存在哪一层。一般用默认即可，除非你清楚存储结构。" title="存储路径">
                    <span>存储路径</span>
                  </Explain>
                </th>
                <th>属性</th>
                <th>可选标签</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {fields.map(f => (
                <tr key={f.field_key}>
                  <td className="mono">{f.field_key}</td>
                  <td>{f.display_name}</td>
                  <td><span className={`pill ${f.extract_source}`}>{
                    f.extract_source === 'manual' ? '人工' : f.extract_source === 'auto' ? '自动' : 'AI 建议'
                  }</span></td>
                  <td>{f.value_constraint === 'enum' ? '枚举' : '自由填'}</td>
                  <td>{f.value_type === 'list' ? '多值' : f.value_type === 'structured' ? '结构化' : '单值'}</td>
                  <td className="mono">{f.storage_path || 'business_metadata.' + f.field_key}</td>
                  <td className="labels-cell">
                    {[
                      f.editable && '可编辑',
                      f.visible && '可见',
                      f.filterable && '可筛选',
                      f.indexable && '可检索',
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

            <label>字段标识</label>
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
                <label>来源</label>
                <select
                  value={draft.extract_source}
                  onChange={e => setDraft({ ...draft, extract_source: e.target.value as 'manual' | 'auto' | 'llm' })}
                >
                  <option value="manual">人工填写</option>
                  <option value="auto">系统自动写入</option>
                  <option value="llm">AI 建议后采纳</option>
                </select>
              </div>
              <div>
                <label>填写方式</label>
                <select
                  value={draft.value_constraint}
                  onChange={e => setDraft({ ...draft, value_constraint: e.target.value as 'free' | 'enum' })}
                >
                  <option value="free">自由文本</option>
                  <option value="enum">从选项里选</option>
                </select>
              </div>
              <div>
                <label>值类型</label>
                <select
                  value={draft.value_type}
                  onChange={e => setDraft({ ...draft, value_type: e.target.value as 'text' | 'list' | 'structured' })}
                >
                  <option value="text">单值</option>
                  <option value="list">多值（列表）</option>
                  <option value="structured">结构化</option>
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
