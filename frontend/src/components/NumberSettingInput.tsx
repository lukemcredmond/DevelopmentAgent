import { useEffect, useRef, useState } from 'react'

interface NumberSettingInputProps {
  value: number
  onCommit: (value: number) => void
  min?: number
  max?: number
  className?: string
}

function parseDigits(text: string): number | null {
  const trimmed = text.trim()
  if (!/^\d+$/.test(trimmed)) return null
  const parsed = parseInt(trimmed, 10)
  return Number.isNaN(parsed) ? null : parsed
}

/**
 * Plain text numeric field — only saves when the value is a valid in-range integer.
 */
export default function NumberSettingInput({
  value,
  onCommit,
  min,
  max,
  className,
}: NumberSettingInputProps) {
  const [text, setText] = useState(String(value))
  const committedRef = useRef(value)

  useEffect(() => {
    committedRef.current = value
    setText(String(value))
  }, [value])

  const tryCommit = (raw: string): boolean => {
    const parsed = parseDigits(raw)
    if (parsed === null) return false
    if (min != null && parsed < min) return false
    if (max != null && parsed > max) return false
    setText(String(parsed))
    if (parsed !== committedRef.current) {
      committedRef.current = parsed
      onCommit(parsed)
    }
    return true
  }

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      spellCheck={false}
      value={text}
      onChange={(e) => {
        const next = e.target.value
        if (next === '' || /^\d*$/.test(next)) {
          setText(next)
          if (next !== '') tryCommit(next)
        }
      }}
      onBlur={() => {
        if (!tryCommit(text)) {
          setText(String(committedRef.current))
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          if (!tryCommit(text)) {
            setText(String(committedRef.current))
          }
          e.currentTarget.blur()
        }
        if (e.key === 'Escape') {
          setText(String(committedRef.current))
          e.currentTarget.blur()
        }
      }}
      className={className}
    />
  )
}
