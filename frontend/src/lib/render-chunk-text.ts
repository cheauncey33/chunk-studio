/** Render chunk text for preview: keep HTML tables, convert markdown tables/headings. */

export function renderChunkText(raw: string): string {
  const text = raw.trim()
  if (!text) return ''
  const parts: string[] = []
  const tableRe = /<table[\s\S]*?<\/table>/gi
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = tableRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(renderTextLines(text.slice(lastIndex, match.index)))
    }
    parts.push(sanitizeTableHtml(match[0]))
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push(renderTextLines(text.slice(lastIndex)))
  }
  return parts.join('')
}

function renderTextLines(block: string): string {
  const lines = block.split(/\r?\n/)
  const html: string[] = []

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]
    if (!line.trim()) continue

    if (isMarkdownTableStart(lines, i)) {
      const { table, nextIndex } = renderMarkdownTable(lines, i)
      html.push(table)
      i = nextIndex - 1
      continue
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      html.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`)
      continue
    }

    html.push(`<p>${escapeHtml(line)}</p>`)
  }

  return html.join('')
}

function isMarkdownTableStart(lines: string[], index: number): boolean {
  const current = lines[index]
  const next = lines[index + 1]
  return !!current?.includes('|') && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(next || '')
}

function renderMarkdownTable(lines: string[], start: number): { table: string; nextIndex: number } {
  const header = splitTableRow(lines[start])
  let index = start + 2
  const rows: string[][] = []

  while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
    rows.push(splitTableRow(lines[index]))
    index += 1
  }

  const thead = `<thead><tr>${header.map(cell => `<th>${escapeHtml(cell)}</th>`).join('')}</tr></thead>`
  const tbody = `<tbody>${rows.map(row => (
    `<tr>${header.map((_, cellIndex) => `<td>${escapeHtml(row[cellIndex] || '')}</td>`).join('')}</tr>`
  )).join('')}</tbody>`

  return { table: `<table>${thead}${tbody}</table>`, nextIndex: index }
}

function splitTableRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())
}

function sanitizeTableHtml(html: string): string {
  const tableMatch = html.match(/<table[\s\S]*?<\/table>/i)
  if (!tableMatch) return `<pre>${escapeHtml(html)}</pre>`
  return tableMatch[0]
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/\son\w+='[^']*'/gi, '')
    .replace(/\sstyle="[^"]*"/gi, '')
    .replace(/\sstyle='[^']*'/gi, '')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
