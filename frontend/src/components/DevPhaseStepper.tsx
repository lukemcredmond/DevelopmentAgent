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
  /** When true, render statusText under the chips (panel / diagram). */
  showStatus?: boolean
}

export default function DevPhaseStepper({
  snapshot,
  label,
  compact = false,
  className = '',
  showStatus = false,
}: DevPhaseStepperProps) {
  const snap = resolveDevPhaseSnapshot({ snapshot, label })
  if (!snap) return null

  const segments = buildDevPhaseSegments(snap)
  const phase = String(snap.phase || '').toLowerCase()
  const showStuck = phase === 'stuck'
  const showDone = phase === 'done'
  const cycle = Number(snap.cycle ?? 0)
  const statusText = (snap.statusText || '').trim()

  return (
    <div className={showStatus ? `space-y-1 ${className}` : className}>
      <div
        className="inline-flex items-center gap-0.5 flex-wrap"
        data-testid="dev-phase-stepper"
        title={
          statusText ||
          'Dev phase graph: Explore → Patch → Verify (Done = this step’s verify budget, not card Done)'
        }
      >
        {cycle > 1 && (
          <span
            className={`${segmentClassName('current', compact)} mr-0.5`}
            data-testid="dev-phase-cycle"
            title={`Developer step cycle ${cycle} on this card`}
          >
            Cycle {cycle}
          </span>
        )}
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
            title={statusText || 'Phase budget exhausted'}
          >
            Stuck
          </span>
        )}
        {showDone && (
          <span
            className={`${segmentClassName('done', compact)} ml-0.5`}
            data-testid="dev-phase-done"
            title="Verify budget for this step (not board Done)"
          >
            Done
          </span>
        )}
      </div>
      {showStatus && statusText && (
        <p className="text-[10px] text-cat-subtext leading-snug" data-testid="dev-phase-status">
          {statusText}
        </p>
      )}
    </div>
  )
}
