import {
  diagramNodeStates,
  resolveDevPhaseSnapshot,
  type DevPhaseGraphSnapshot,
  type DevPhaseSegmentState,
} from '../utils/devPhaseStepper'

interface DevPhaseDiagramProps {
  snapshot?: DevPhaseGraphSnapshot | null
  label?: string | null
  className?: string
}

function nodeFill(state: DevPhaseSegmentState | 'idle'): string {
  if (state === 'current') return 'rgba(14, 165, 233, 0.35)' // sky
  if (state === 'done') return 'rgba(16, 185, 129, 0.22)' // emerald
  if (state === 'stuck') return 'rgba(244, 63, 94, 0.35)' // rose
  return 'rgba(49, 50, 68, 0.9)' // surface
}

function nodeStroke(state: DevPhaseSegmentState | 'idle'): string {
  if (state === 'current') return 'rgb(56, 189, 248)'
  if (state === 'done') return 'rgb(52, 211, 153)'
  if (state === 'stuck') return 'rgb(251, 113, 133)'
  return 'rgb(69, 71, 90)'
}

function textFill(state: DevPhaseSegmentState | 'idle'): string {
  if (state === 'current') return 'rgb(224, 242, 254)'
  if (state === 'done') return 'rgb(167, 243, 208)'
  if (state === 'stuck') return 'rgb(254, 205, 211)'
  return 'rgb(166, 173, 200)'
}

/** Lightweight SVG state machine for Explore → Patch → Verify. */
export default function DevPhaseDiagram({
  snapshot,
  label,
  className = '',
}: DevPhaseDiagramProps) {
  const snap = resolveDevPhaseSnapshot({ snapshot, label })
  if (!snap) {
    return (
      <p className="text-[10px] text-cat-subtext" data-testid="dev-phase-diagram-empty">
        No phase snapshot yet — run a Developer In Progress step with Dev phase graph on.
      </p>
    )
  }

  const nodes = diagramNodeStates(snap)
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
  const phase = String(snap.phase || '').toLowerCase()

  const layout: Record<string, { x: number; y: number; w: number; h: number }> = {
    explore: { x: 24, y: 36, w: 88, h: 44 },
    patch: { x: 156, y: 36, w: 88, h: 44 },
    verify: { x: 288, y: 36, w: 88, h: 44 },
    stuck: { x: 90, y: 118, w: 72, h: 36 },
    done: { x: 300, y: 118, w: 72, h: 36 },
  }

  const mid = (id: string) => {
    const b = layout[id]
    return { x: b.x + b.w / 2, y: b.y + b.h / 2 }
  }

  const edge = (
    from: string,
    to: string,
    opts?: { dashed?: boolean; active?: boolean; label?: string; bend?: number },
  ) => {
    const a = mid(from)
    const b = mid(to)
    const aw = layout[from].w / 2
    const bw = layout[to].w / 2
    const leftToRight = b.x > a.x
    const x1 = leftToRight ? a.x + aw : a.x - aw
    const x2 = leftToRight ? b.x - bw : b.x + bw
    const y1 = a.y + (opts?.bend ?? 0)
    const y2 = b.y + (opts?.bend ?? 0)
    const stroke = opts?.active ? 'rgb(56, 189, 248)' : 'rgb(88, 91, 112)'
    return (
      <g key={`${from}-${to}-${opts?.label ?? ''}`}>
        <line
          x1={x1}
          y1={y1}
          x2={x2}
          y2={y2}
          stroke={stroke}
          strokeWidth={opts?.active ? 1.75 : 1.25}
          strokeDasharray={opts?.dashed ? '4 3' : undefined}
          markerEnd="url(#ah-phase-arrow)"
        />
        {opts?.label && (
          <text
            x={(x1 + x2) / 2}
            y={(y1 + y2) / 2 - 4}
            fill="rgb(108, 112, 134)"
            fontSize={8}
            textAnchor="middle"
            fontFamily="ui-monospace, monospace"
          >
            {opts.label}
          </text>
        )}
      </g>
    )
  }

  const box = (id: keyof typeof layout) => {
    const n = byId[id]
    const b = layout[id]
    const state = (n?.state ?? 'idle') as DevPhaseSegmentState | 'idle'
    const budget =
      n?.count != null && n?.max != null ? `${n.count}/${n.max}` : undefined
    return (
      <g key={id} data-testid={`dev-phase-diagram-node-${id}`} data-state={state}>
        {state === 'current' && (
          <rect
            x={b.x - 3}
            y={b.y - 3}
            width={b.w + 6}
            height={b.h + 6}
            rx={10}
            fill="none"
            stroke="rgb(56, 189, 248)"
            strokeWidth={1.5}
            opacity={0.7}
            className="animate-pulse"
          />
        )}
        <rect
          x={b.x}
          y={b.y}
          width={b.w}
          height={b.h}
          rx={8}
          fill={nodeFill(state)}
          stroke={nodeStroke(state)}
          strokeWidth={1.5}
        />
        <text
          x={b.x + b.w / 2}
          y={b.y + (budget ? b.h / 2 - 4 : b.h / 2 + 4)}
          fill={textFill(state)}
          fontSize={11}
          fontWeight={600}
          textAnchor="middle"
          fontFamily="ui-sans-serif, system-ui, sans-serif"
        >
          {n?.label ?? id}
        </text>
        {budget && (
          <text
            x={b.x + b.w / 2}
            y={b.y + b.h / 2 + 12}
            fill={textFill(state)}
            fontSize={9}
            textAnchor="middle"
            fontFamily="ui-monospace, monospace"
          >
            {budget}
          </text>
        )}
      </g>
    )
  }

  const exploreActive = phase === 'explore' || (phase === 'stuck' && byId.explore?.state === 'stuck')
  const patchActive = phase === 'patch' || (phase === 'stuck' && byId.patch?.state === 'stuck')
  const verifyActive = phase === 'verify' || phase === 'done'

  return (
    <div className={className} data-testid="dev-phase-diagram">
      <svg
        viewBox="0 0 400 168"
        className="w-full max-w-md h-auto"
        role="img"
        aria-label={`Dev phase diagram: ${snap.label || snap.phase}`}
      >
        <defs>
          <marker
            id="ah-phase-arrow"
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L6,3 L0,6 Z" fill="rgb(88, 91, 112)" />
          </marker>
        </defs>
        {edge('explore', 'patch', {
          active: exploreActive || patchActive || verifyActive,
          label: 'context',
        })}
        {edge('patch', 'verify', {
          active: patchActive || verifyActive,
          label: 'write ok',
        })}
        {edge('verify', 'done', {
          active: phase === 'done' || phase === 'verify',
          label: 'ok',
        })}
        {edge('patch', 'explore', {
          dashed: true,
          bend: 18,
          label: 'need ctx',
        })}
        {edge('verify', 'patch', {
          dashed: true,
          bend: -14,
          label: 'retry',
        })}
        {/* Stuck branches */}
        <line
          x1={mid('explore').x}
          y1={layout.explore.y + layout.explore.h}
          x2={layout.stuck.x + layout.stuck.w / 2}
          y2={layout.stuck.y}
          stroke={phase === 'stuck' && byId.explore?.state === 'stuck' ? 'rgb(251, 113, 133)' : 'rgb(88, 91, 112)'}
          strokeWidth={1.25}
          strokeDasharray="3 3"
          markerEnd="url(#ah-phase-arrow)"
        />
        <line
          x1={mid('patch').x}
          y1={layout.patch.y + layout.patch.h}
          x2={layout.stuck.x + layout.stuck.w / 2 + 10}
          y2={layout.stuck.y}
          stroke={phase === 'stuck' && byId.patch?.state === 'stuck' ? 'rgb(251, 113, 133)' : 'rgb(88, 91, 112)'}
          strokeWidth={1.25}
          strokeDasharray="3 3"
          markerEnd="url(#ah-phase-arrow)"
        />
        {box('explore')}
        {box('patch')}
        {box('verify')}
        {box('stuck')}
        {box('done')}
      </svg>
      {snap.label && (
        <p className="text-[10px] text-cat-overlay font-mono mt-1">{snap.label}</p>
      )}
      {Number(snap.cycle ?? 0) > 1 && (
        <p className="text-[10px] text-sky-200/90 font-mono" data-testid="dev-phase-diagram-cycle">
          Cycle {snap.cycle}
        </p>
      )}
      {snap.statusText && (
        <p className="text-[10px] text-cat-subtext leading-snug mt-0.5" data-testid="dev-phase-diagram-status">
          {snap.statusText}
        </p>
      )}
    </div>
  )
}
