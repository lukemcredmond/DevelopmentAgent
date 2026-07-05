interface FileDiffModalProps {
  path: string
  previousContent: string
  content: string
  onClose: () => void
}

function diffLines(before: string, after: string): { type: 'same' | 'add' | 'remove'; line: string }[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const result: { type: 'same' | 'add' | 'remove'; line: string }[] = []
  const max = Math.max(a.length, b.length)
  for (let i = 0; i < max; i++) {
    const la = a[i]
    const lb = b[i]
    if (la === lb) {
      if (la !== undefined) result.push({ type: 'same', line: la })
    } else {
      if (la !== undefined) result.push({ type: 'remove', line: la })
      if (lb !== undefined) result.push({ type: 'add', line: lb })
    }
  }
  return result
}

export default function FileDiffModal({
  path,
  previousContent,
  content,
  onClose,
}: FileDiffModalProps) {
  const lines = diffLines(previousContent, content)

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-[60]">
      <div className="bg-cat-surface0 rounded-xl max-w-4xl w-full max-h-[85vh] flex flex-col border border-cat-surface1">
        <div className="px-4 py-3 border-b border-cat-surface1 flex items-center justify-between shrink-0">
          <h3 className="text-sm font-bold text-white font-mono truncate">{path}</h3>
          <button type="button" onClick={onClose} className="text-cat-subtext hover:text-white">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <pre className="flex-1 overflow-auto p-3 text-[11px] font-mono leading-relaxed min-h-0">
          {lines.map((l, i) => (
            <div
              key={i}
              className={
                l.type === 'add'
                  ? 'bg-emerald-950/40 text-emerald-200'
                  : l.type === 'remove'
                    ? 'bg-rose-950/40 text-rose-200 line-through'
                    : 'text-cat-subtext'
              }
            >
              {l.type === 'add' ? '+ ' : l.type === 'remove' ? '- ' : '  '}
              {l.line}
            </div>
          ))}
        </pre>
      </div>
    </div>
  )
}
