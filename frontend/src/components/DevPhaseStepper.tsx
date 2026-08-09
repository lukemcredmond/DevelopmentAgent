import {
  buildDevPhaseSegments,
  resolveDevPhaseSnapshot,
  segmentClassName,
  type DevPhaseGraphSnapshot,
} from '../utils/devPhaseStepper'

interface DevPhaseStepperProps {
  snapshot?: DevPhaseGraphSnapshot | null
  /** Fallback label e.g. "Explore 2/3" when snapshot missing. */
  label?: string | null
  compact?: boolean
  className?: string
}

export default function DevPhaseStepper({
  snapshot,
  label,
  compact = false,
  className = '',
}: DevPhaseStepperProps) {
  const snap = resolveDevPhaseSnapshot({ snapshot, label })
  if (!snap) return null

  const segments = buildDevPhaseSegments(snap)
  const phase = String(snap.phase || '').toLowerCase()
  const showStuck = phase === 'stuck'
  const showDone = phase === 'done'

  return (
    <div
      className={`inline-flex items-center gap-0.5 flex-wrap ${className}`}
      data-testid="dev-phase-stepper"
      title="Dev phase graph: Explore → Patch → Verify"
    >
      {segments.map((seg, i) => (
        <span key={seg.id} className="inline-flex items-center gap-0.5">
          {i > 0 && <span className="text-cat-overlay text-[9px] mx-0.5">→</span>}
          <span
            className={segmentClassName(seg.state, compact)}
            data-testid={`dev-phase-seg-${seg.id}`}
            data-state={seg.state}
          >
            {seg.label} {seg.count}/{seg.max}
          </span>
        </span>
      ))}
      {showStuck && (
        <span
          className={`${segmentClassName('stuck', compact)} ml-0.5`}
          data-testid="dev-phase-stuck"
        >
          Stuck
        </span>
      )}
      {showDone && (
        <span
          className={`${segmentClassName('done', compact)} ml-0.5`}
          data-testid="dev-phase-done"
        >
          Done
        </span>
      )}
    </div>
  )
}
