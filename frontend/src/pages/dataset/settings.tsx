import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { toast } from 'sonner'
import {
  ChunkRuleFields,
  DEFAULT_CHUNK_RULES,
  normalizeChunkRules,
  type ChunkRuleConfig,
} from '@/components/chunk-rule-fields'
import { Button } from '@/components/ui/button'
import { Input, Label, Textarea } from '@/components/ui/input'
import {
  useKnowledgeBase,
  useUpdateKnowledgeBase,
} from '@/hooks/use-knowledge-request'

export default function DatasetSettingsPage() {
  const { id = '' } = useParams()
  const { data: knowledgeBase } = useKnowledgeBase(id)
  const updateKb = useUpdateKnowledgeBase(id)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [topK, setTopK] = useState(10)
  const [threshold, setThreshold] = useState(0.2)
  const [vectorWeight, setVectorWeight] = useState(0.7)
  const [keywordWeight, setKeywordWeight] = useState(0.3)
  const [chunkRules, setChunkRules] = useState<ChunkRuleConfig>(DEFAULT_CHUNK_RULES)

  useEffect(() => {
    if (!knowledgeBase) return
    setName(knowledgeBase.name)
    setDescription(knowledgeBase.description)
    const config = knowledgeBase.retrieval_config || {}
    setTopK(Number(config.top_k ?? 10))
    setThreshold(Number(config.similarity_threshold ?? 0.2))
    setVectorWeight(Number(config.vector_weight ?? 0.7))
    setKeywordWeight(Number(config.keyword_weight ?? 0.3))
    setChunkRules(normalizeChunkRules(knowledgeBase.parser_config || {}))
  }, [knowledgeBase])

  const save = async () => {
    try {
      await updateKb.mutateAsync({
        name,
        description,
        retrieval_config: {
          ...(knowledgeBase?.retrieval_config || {}),
          top_k: topK,
          similarity_threshold: threshold,
          vector_weight: vectorWeight,
          keyword_weight: keywordWeight,
        },
        parser_config: chunkRules as unknown as Record<string, unknown>,
      })
      toast.success('已保存')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div className="max-w-3xl space-y-6 pt-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">配置</h1>
        <Button disabled={updateKb.isPending} onClick={save}>
          {updateKb.isPending ? '保存中…' : '保存'}
        </Button>
      </div>

      <section className="space-y-4 rounded-xl border border-border-button bg-bg-base p-5">
        <h2 className="text-sm font-semibold">基本信息</h2>
        <div className="space-y-2">
          <Label htmlFor="kb-config-name">名称</Label>
          <Input id="kb-config-name" value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="kb-config-desc">说明</Label>
          <Textarea id="kb-config-desc" value={description} onChange={e => setDescription(e.target.value)} />
        </div>
      </section>

      <section className="space-y-4 rounded-xl border border-border-button bg-bg-base p-5">
        <h2 className="text-sm font-semibold">默认切片规则</h2>
        <ChunkRuleFields value={chunkRules} onChange={setChunkRules} />
      </section>

      <section className="space-y-4 rounded-xl border border-border-button bg-bg-base p-5">
        <h2 className="text-sm font-semibold">检索默认参数</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="kb-config-topk">返回条数</Label>
            <Input
              id="kb-config-topk"
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={e => setTopK(Number(e.target.value))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="kb-config-threshold">相似度门槛</Label>
            <Input
              id="kb-config-threshold"
              type="number"
              step={0.05}
              min={-1}
              max={1}
              value={threshold}
              onChange={e => setThreshold(Number(e.target.value))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="kb-config-vector">语义权重</Label>
            <Input
              id="kb-config-vector"
              type="number"
              step={0.05}
              value={vectorWeight}
              onChange={e => setVectorWeight(Number(e.target.value))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="kb-config-keyword">关键词权重</Label>
            <Input
              id="kb-config-keyword"
              type="number"
              step={0.05}
              value={keywordWeight}
              onChange={e => setKeywordWeight(Number(e.target.value))}
            />
          </div>
        </div>
      </section>
    </div>
  )
}
