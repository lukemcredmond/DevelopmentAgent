import { useEffect, useRef, useState } from 'react'

interface NumberSettingInputProps {
  value: number
  onCommit: (value: number) => void
  min?: number
  max?: number
  className?: string
  /** Milliseconds to wait after typing stops before auto-saving. */
  debounceMs?: number
}

/**
 * Numeric setting field — edits freely while focused, saves after pause or on blur/Enter.
 * Reverts invalid partial values instead of snapping to min (e.g. "5" while typing "500").
 */
export default function NumberSettingInput({
  value,
  onCommit,
  min,
  max,
  className,
  debounceMs = 500,
}: NumberSettingInputProps) {
  const [draft, setDraft] = useState(String(value))
  const [focused, setFocused] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastCommittedRef = useRef(value)

  useEffect(() => {
    lastCommittedRef.current = value
    if (!focused) {
      setDraft(String(value))
    }
  }, [value, focused])

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const parseDraft = (text: string): number | null => {
    const trimmed = text.trim()
    if (trimmed === '' || trimmed === '-') return null
    const parsed = parseInt(trimmed, 10)
    return Number.isNaN(parsed) ? null : parsed
  }

  const isInRange = (num: number): boolean => {
    if (min != null && num < min) return false
    if (max != null && num > max) return false
    return true
  }

  const commit = (opts?: { allowOutOfRange?: boolean }): boolean => {
    const parsed = parseDraft(draft)
    if (parsed === null) {
      setDraft(String(lastCommittedRef.current))
      return false
    }
    if (!opts?.allowOutOfRange && !isInRange(parsed)) {
      setDraft(String(lastCommittedRef.current))
      return false
    }
    let next = parsed
    if (min != null) next = Math.max(min, next)
    if (max != null) next = Math.min(max, next)
    setDraft(String(next))
    if (next !== lastCommittedRef.current) {
      lastCommittedRef.current = next
      onCommit(next)
    }
    return true
  }

  const scheduleCommit = () => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null
      if (focused) commit()
    }, debounceMs)
  }

  const handleChange = (next: string) => {
    if (next === '' || next === '-' || /^-?\d*$/.test(next)) {
      setDraft(next)
      scheduleCommit()
    }
  }

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      spellCheck={false}
      value={draft}
      onChange={(e) => handleChange(e.target.value)}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false)
        if (debounceRef.current) {
          clearTimeout(debounceRef.current)
          debounceRef.current = null
        }
        commit()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          if (debounceRef.current) {
            clearTimeout(debounceRef.current)
            debounceRef.current = null
          }
          commit({ allowOutOfRange: true })
          e.currentTarget.blur()
        }
        if (e.key === 'Escape') {
          if (debounceRef.current) {
            clearTimeout(debounceRef.current)
            debounceRef.current = null
          }
          setDraft(String(lastCommittedRef.current))
          e.currentTarget.blur()
        }
      }}
      className={className}
    />
  )
}
