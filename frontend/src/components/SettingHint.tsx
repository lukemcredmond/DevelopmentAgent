import { useId, useState, type ReactNode } from 'react'

interface SettingHintProps {
  /** Plain-English explanation of the setting. */
  hint: string
}

/** Clickable ⓘ that reveals a short plain-English tip (works on touch). */
export function SettingHint({ hint }: SettingHintProps) {
  const [open, setOpen] = useState(false)
  const tipId = useId()
  return (
    <span className="relative inline-flex items-center align-middle ml-1 shrink-0">
      <button
        type="button"
        data-testid="setting-hint"
        aria-label={`About this setting: ${hint}`}
        aria-expanded={open}
        aria-controls={tipId}
        title={hint}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-cat-overlay/50 text-[9px] text-cat-overlay hover:text-indigo-300 hover:border-indigo-400/50 leading-none"
      >
        i
      </button>
      {open && (
        <span
          id={tipId}
          role="tooltip"
          className="absolute z-20 left-0 top-full mt-1 w-56 max-w-[70vw] rounded border border-cat-surface1 bg-cat-mantle px-2 py-1.5 text-[10px] text-cat-subtext leading-snug shadow-lg"
        >
          {hint}
        </span>
      )}
    </span>
  )
}

interface SettingLabelProps {
  hint: string
  children: ReactNode
  className?: string
  htmlFor?: string
}

/** Label text plus info icon for Settings / Workflow controls. */
export function SettingLabel({ hint, children, className = '', htmlFor }: SettingLabelProps) {
  return (
    <span className={`inline-flex items-center flex-wrap gap-0.5 ${className}`}>
      {htmlFor ? <label htmlFor={htmlFor}>{children}</label> : <span>{children}</span>}
      <SettingHint hint={hint} />
    </span>
  )
}

export default SettingHint
