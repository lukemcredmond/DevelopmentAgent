import { useEffect, useState } from 'react'

interface NumberSettingInputProps {
  value: number
  onCommit: (value: number) => void
  min?: number
  max?: number
  className?: string
  /** When true, allow clearing the field while typing (reverts on blur if empty). */
  allowEmptyWhileTyping?: boolean
}

/**
 * Numeric setting field that commits on blur/Enter instead of every keystroke.
 * Avoids fighting the user when replacing values like 300 → 500.
 */
export default function NumberSettingInput({
  value,
  onCommit,
  min,
  max,
  className,
  allowEmptyWhileTyping = true,
}: NumberSettingInputProps) {
  const [draft, setDraft] = useState(String(value))
  const [focused, setFocused] = useState(false)

  useEffect(() => {
    if (!focused) {
      setDraft(String(value))
    }
  }, [value, focused])

  const clamp = (num: number): number => {
    let next = num
    if (min != null) next = Math.max(min, next)
    if (max != null) next = Math.min(max, next)
    return next
  }

  const commit = () => {
    const trimmed = draft.trim()
    if (trimmed === '' || trimmed === '-') {
      setDraft(String(value))
      return
    }
    const parsed = parseInt(trimmed, 10)
    if (Number.isNaN(parsed)) {
      setDraft(String(value))
      return
    }
    const next = clamp(parsed)
    setDraft(String(next))
    if (next !== value) {
      onCommit(next)
    }
  }

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      spellCheck={false}
      value={draft}
      onChange={(e) => {
        const next = e.target.value
        if (allowEmptyWhileTyping && (next === '' || next === '-')) {
          setDraft(next)
          return
        }
        if (/^-?\d*$/.test(next)) {
          setDraft(next)
        }
      }}
      onFocus={(e) => {
        setFocused(true)
        e.target.select()
      }}
      onBlur={() => {
        setFocused(false)
        commit()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.currentTarget.blur()
        }
        if (e.key === 'Escape') {
          setDraft(String(value))
          e.currentTarget.blur()
        }
      }}
      className={className}
    />
  )
}
