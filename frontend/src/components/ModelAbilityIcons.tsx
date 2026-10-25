export type AbilityTier = 'good' | 'warn' | 'bad' | 'unknown'

function tierClass(tier: AbilityTier): string {
  switch (tier) {
    case 'good':
      return 'text-emerald-300'
    case 'warn':
      return 'text-amber-300'
    case 'bad':
      return 'text-rose-300'
    default:
      return 'text-cat-overlay'
  }
}

export function ToolAbilityIcon({
  score,
  tier,
  title,
}: {
  score?: number | null
  tier?: AbilityTier | string
  title?: string
}) {
  const t = (tier as AbilityTier) || 'unknown'
  return (
    <span className={`inline-flex items-center gap-0.5 ${tierClass(t)}`} title={title}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
        <path
          d="M14.7 6.3a1 1 0 0 0 0-1.4l-1.6-1.6a1 1 0 0 0-1.4 0l-1.3 1.3L7 2.3a1 1 0 0 0-1.4 0L4 3.9a1 1 0 0 0 0 1.4l2.3 2.3-1.3 1.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l1.3-1.3 2.3 2.3a1 1 0 0 0 1.4 0l1.6-1.6a1 1 0 0 0 0-1.4l-1.3-1.3 2.3-2.3Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
      {score != null ? <span className="text-[9px] tabular-nums font-mono">{score}</span> : null}
    </span>
  )
}

export function CodeAbilityIcon({
  score,
  tier,
  title,
}: {
  score?: number | null
  tier?: AbilityTier | string
  title?: string
}) {
  const t = (tier as AbilityTier) || 'unknown'
  return (
    <span className={`inline-flex items-center gap-0.5 ${tierClass(t)}`} title={title}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden className="shrink-0">
        <path
          d="M8 7L3 12l5 5M16 7l5 5-5 5M14 4l-4 16"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {score != null ? <span className="text-[9px] tabular-nums font-mono">{score}</span> : null}
    </span>
  )
}
