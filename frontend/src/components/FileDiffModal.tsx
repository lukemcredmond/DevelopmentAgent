import SlideOver from './SlideOver'

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
    <SlideOver
      open
      onClose={onClose}
      side="right"
      title={<span className="font-mono text-xs">{path}</span>}
      widthClass="w-full max-w-3xl"
      zIndexClass="z-[60]"
    >
      <pre className="p-3 text-[11px] font-mono leading-relaxed">
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
    </SlideOver>
  )
}
