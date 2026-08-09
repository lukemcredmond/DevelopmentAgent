import {
  buildUnrolledPhaseRows,
  resolveDevPhaseSnapshot,
  type DevPhaseGraphSnapshot,
  type DevPhaseSegmentState,
  type UnrolledPhaseNode,
  type UnrolledPhaseRow,
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

const ROW_H = 88
const LABEL_H = 14
const NODE_W = 72
const NODE_H = 36
const MAIN_Y = 18
const STUCK_DONE_Y = 56

type Box = { x: number; y: number; w: number; h: number }

function rowLayout(rowIndex: number): Record<string, Box> {
  const y0 = rowIndex * ROW_H + LABEL_H + 4
  return {
    explore: { x: 56, y: y0 + MAIN_Y, w: NODE_W, h: NODE_H },
    patch: { x: 156, y: y0 + MAIN_Y, w: NODE_W, h: NODE_H },
    verify: { x: 256, y: y0 + MAIN_Y, w: NODE_W, h: NODE_H },
    stuck: { x: 120, y: y0 + STUCK_DONE_Y, w: 56, h: 28 },
    done: { x: 280, y: y0 + STUCK_DONE_Y, w: 56, h: 28 },
  }
}

function mid(box: Box) {
  return { x: box.x + box.w / 2, y: box.y + box.h / 2 }
}

function terminalId(phase: string): 'done' | 'stuck' {
  return phase === 'stuck' ? 'stuck' : 'done'
}

/** Unrolled Explore→Patch→Verify path: each Developer step is its own lane. */
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

  const rows = buildUnrolledPhaseRows(snap)
  const height = Math.max(ROW_H + 8, rows.length * ROW_H + 12)
  const liveLabel = rows.find((r) => r.live)?.stepLabel || snap.stepLabel || `Cycle ${snap.cycle ?? 1}`

  const drawBox = (
    rowKey: string,
    id: string,
    box: Box,
    node: UnrolledPhaseNode | undefined,
  ) => {
    const state = (node?.state ?? 'idle') as DevPhaseSegmentState | 'idle'
    const budget =
      node?.count != null && node?.max != null ? `${node.count}/${node.max}` : undefined
    return (
      <g
        key={`${rowKey}-${id}`}
        data-testid={`dev-phase-diagram-node-${rowKey}-${id}`}
        data-state={state}
      >
        {state === 'current' && (
          <rect
            x={box.x - 2}
            y={box.y - 2}
            width={box.w + 4}
            height={box.h + 4}
            rx={8}
            fill="none"
            stroke="rgb(56, 189, 248)"
            strokeWidth={1.5}
            opacity={0.7}
            className="animate-pulse"
          />
        )}
        <rect
          x={box.x}
          y={box.y}
          width={box.w}
          height={box.h}
          rx={7}
          fill={nodeFill(state)}
          stroke={nodeStroke(state)}
          strokeWidth={1.35}
        />
        <text
          x={box.x + box.w / 2}
          y={box.y + (budget ? box.h / 2 - 3 : box.h / 2 + 3)}
          fill={textFill(state)}
          fontSize={10}
          fontWeight={600}
          textAnchor="middle"
          fontFamily="ui-sans-serif, system-ui, sans-serif"
        >
          {node?.label ?? id}
        </text>
        {budget && (
          <text
            x={box.x + box.w / 2}
            y={box.y + box.h / 2 + 10}
            fill={textFill(state)}
            fontSize={8}
            textAnchor="middle"
            fontFamily="ui-monospace, monospace"
          >
            {budget}
          </text>
        )}
      </g>
    )
  }

  const hEdge = (
    key: string,
    from: Box,
    to: Box,
    active: boolean,
    edgeLabel?: string,
  ) => {
    const a = mid(from)
    const b = mid(to)
    const x1 = a.x + from.w / 2
    const x2 = b.x - to.w / 2
    const y = a.y
    return (
      <g key={key}>
        <line
          x1={x1}
          y1={y}
          x2={x2}
          y2={y}
          stroke={active ? 'rgb(56, 189, 248)' : 'rgb(88, 91, 112)'}
          strokeWidth={active ? 1.6 : 1.2}
          markerEnd={active ? 'url(#ah-phase-arrow-active)' : 'url(#ah-phase-arrow)'}
        />
        {edgeLabel && (
          <text
            x={(x1 + x2) / 2}
            y={y - 4}
            fill="rgb(108, 112, 134)"
            fontSize={7}
            textAnchor="middle"
            fontFamily="ui-monospace, monospace"
          >
            {edgeLabel}
          </text>
        )}
      </g>
    )
  }

  const renderRow = (row: UnrolledPhaseRow, idx: number) => {
    const layout = rowLayout(idx)
    const byId = Object.fromEntries(row.nodes.map((n) => [n.id, n])) as Record<
      string,
      UnrolledPhaseNode
    >
    const phase = row.phase
    const exploreOn =
      phase === 'explore' || (phase === 'stuck' && byId.explore?.state === 'stuck')
    const patchOn = phase === 'patch' || (phase === 'stuck' && byId.patch?.state === 'stuck')
    const verifyOn = phase === 'verify' || phase === 'done'
    const showStuck = phase === 'stuck' || byId.stuck?.state === 'stuck'
    const showDone = phase === 'done' || byId.done?.state === 'done' || (!row.live && phase !== 'stuck')

    const stuckFrom =
      phase === 'stuck' && byId.patch?.state === 'stuck' ? layout.patch : layout.explore
    const stuckActive = phase === 'stuck'

    return (
      <g key={row.key} data-testid={`dev-phase-row-${row.key}`} data-live={row.live ? 'true' : 'false'}>
        <text
          x={8}
          y={idx * ROW_H + 12}
          fill={row.live ? 'rgb(125, 211, 252)' : 'rgb(147, 153, 178)'}
          fontSize={9}
          fontFamily="ui-monospace, monospace"
        >
          {row.stepLabel}
        </text>
        {hEdge(
          `${row.key}-ep`,
          layout.explore,
          layout.patch,
          exploreOn || patchOn || verifyOn || showDone,
          'context',
        )}
        {hEdge(
          `${row.key}-pv`,
          layout.patch,
          layout.verify,
          patchOn || verifyOn || showDone,
          'write',
        )}
        {(showDone || phase === 'verify') &&
          hEdge(`${row.key}-vd`, layout.verify, layout.done, showDone || phase === 'verify', 'ok')}
        {showStuck && (
          <line
            x1={mid(stuckFrom).x}
            y1={stuckFrom.y + stuckFrom.h}
            x2={mid(layout.stuck).x}
            y2={layout.stuck.y}
            stroke={stuckActive ? 'rgb(251, 113, 133)' : 'rgb(88, 91, 112)'}
            strokeWidth={1.2}
            strokeDasharray="3 3"
            markerEnd="url(#ah-phase-arrow)"
          />
        )}
        {drawBox(row.key, 'explore', layout.explore, byId.explore)}
        {drawBox(row.key, 'patch', layout.patch, byId.patch)}
        {drawBox(row.key, 'verify', layout.verify, byId.verify)}
        {showStuck && drawBox(row.key, 'stuck', layout.stuck, byId.stuck)}
        {(showDone || phase === 'verify' || phase === 'done') &&
          drawBox(row.key, 'done', layout.done, byId.done)}
      </g>
    )
  }

  const crossRowLinks = rows.flatMap((row, idx) => {
    if (!row.connectsToNext || idx >= rows.length - 1) return []
    const fromLayout = rowLayout(idx)
    const toLayout = rowLayout(idx + 1)
    const term = terminalId(row.phase)
    const fromBox = fromLayout[term]
    const toBox = toLayout.explore
    const a = mid(fromBox)
    const b = mid(toBox)
    const nextLabel = rows[idx + 1].stepLabel
    const x1 = a.x
    const y1 = fromBox.y + fromBox.h
    const x2 = b.x
    const y2 = toBox.y
    const midY = (y1 + y2) / 2
    return [
      <g
        key={`link-${row.key}-to-${rows[idx + 1].key}`}
        data-testid="dev-phase-cross-cycle-link"
        data-from-cycle={row.cycle}
        data-to-cycle={rows[idx + 1].cycle}
      >
        <path
          d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
          fill="none"
          stroke="rgb(56, 189, 248)"
          strokeWidth={1.6}
          markerEnd="url(#ah-phase-arrow-active)"
        />
        <text
          x={(x1 + x2) / 2}
          y={midY - 2}
          fill="rgb(125, 211, 252)"
          fontSize={7}
          textAnchor="middle"
          fontFamily="ui-monospace, monospace"
        >
          {`→ ${nextLabel}`}
        </text>
      </g>,
    ]
  })

  return (
    <div className={className} data-testid="dev-phase-diagram">
      <p
        className="text-[10px] text-sky-200/90 font-mono mb-0.5"
        data-testid="dev-phase-diagram-step-label"
      >
        {liveLabel}
        {rows.length > 1 ? ` · ${rows.length} steps` : ''}
      </p>
      <svg
        viewBox={`0 0 360 ${height}`}
        className="w-full max-w-md h-auto"
        role="img"
        aria-label={`Dev phase path: ${rows.map((r) => r.stepLabel).join(' → ')}`}
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
          <marker
            id="ah-phase-arrow-active"
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L6,3 L0,6 Z" fill="rgb(56, 189, 248)" />
          </marker>
        </defs>
        {rows.map((row, idx) => renderRow(row, idx))}
        {crossRowLinks}
      </svg>
      {snap.label && (
        <p className="text-[10px] text-cat-overlay font-mono mt-1">{snap.label}</p>
      )}
      {snap.statusText && (
        <p
          className="text-[10px] text-cat-subtext leading-snug mt-0.5"
          data-testid="dev-phase-diagram-status"
        >
          {snap.statusText}
        </p>
      )}
    </div>
  )
}
