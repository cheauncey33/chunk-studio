import type { Chunk } from './api'

interface Props {
  chunks: Chunk[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function ChunkList({ chunks, selectedId, onSelect }: Props) {
  return (
    <div className="chunk-list">
      <h3>当前文件切片 ({chunks.length})</h3>
      {chunks.length === 0 && <div className="muted">在页面上拖拽框选，创建第一个切片</div>}
      {chunks.map(chunk => (
        <div
          key={chunk.id}
          className={`chunk-item ${chunk.id === selectedId ? 'selected' : ''}`}
          onClick={() => onSelect(chunk.id)}
        >
          <div className="ci-head">
            <span>P{chunk.page}</span>
            <span className={`src ${chunk.text_source}`}>{chunk.text_source}</span>
            <span className={`status ${chunk.status}`}>{chunk.status}</span>
            {chunk.ocr_status && <span className={`job-status ${chunk.ocr_status}`}>ocr:{chunk.ocr_status}</span>}
          </div>
          <div className="ci-text">{chunk.text?.slice(0, 80) || '无文本'}</div>
        </div>
      ))}
    </div>
  )
}
