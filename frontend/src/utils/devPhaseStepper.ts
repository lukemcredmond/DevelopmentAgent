/** Helpers for Dev Explore → Patch → Verify phase stepper. */

export type DevPhaseName = 'explore' | 'patch' | 'verify' | 'stuck' | 'done'

export interface DevPhaseGraphSnapshot {
  phase: DevPhaseName | string
  label?: string
  exploreCount?: number
  exploreMax?: number
  patchCount?: number
  patchMax?: number
  verifyCount?: number
  verifyMax?: number
  writeSucceeded?: boolean
  /** 1-based Developer step cycle on this card for the phase graph. */
  cycle?: number
  /** Human-readable what this phase means / why budgets reset. */
  statusText?: string
  /** Short cycle caption e.g. "Cycle 2" or "Cycle 2 · AC 1". */
  stepLabel?: string
  /** Prior step outcome when budgets reset e.g. "Verify Done". */
  priorSummary?: string
}

export type DevPhaseSegmentState = 'done' | 'current' | 'upcoming' | 'stuck'

export interface DevPhaseSegment {
  id: 'explore' | 'patch' | 'verify'
  label: string
  count: number
  max: number
  state: DevPhaseSegmentState
}

const PHASE_ORDER = ['explore', 'patch', 'verify'] as const

/** Normalize camel/snake API payloads into a snapshot. */
export function parseDevPhaseSnapshot(raw: unknown): DevPhaseGraphSnapshot | null {
  if (!raw || typeof raw !== 'object') return null
  const d = raw as Record<string, unknown>
  const phase = String(d.phase ?? '').toLowerCase()
  if (!phase) return null
  const cycleRaw = Number(d.cycle ?? 0)
  const statusRaw = d.statusText ?? d.status_text
  const stepLabelRaw = d.stepLabel ?? d.step_label
  const priorRaw = d.priorSummary ?? d.prior_summary
  return {
    phase,
    label: d.label != null ? String(d.label) : undefined,
    exploreCount: Number(d.exploreCount ?? d.explore_count ?? 0) || 0,
    exploreMax: Number(d.exploreMax ?? d.explore_max ?? 3) || 3,
    patchCount: Number(d.patchCount ?? d.patch_count ?? 0) || 0,
    patchMax: Number(d.patchMax ?? d.patch_max ?? 4) || 4,
    verifyCount: Number(d.verifyCount ?? d.verify_count ?? 0) || 0,
    verifyMax: Number(d.verifyMax ?? d.verify_max ?? 2) || 2,
    writeSucceeded: Boolean(d.writeSucceeded ?? d.write_succeeded),
    cycle: cycleRaw > 0 ? cycleRaw : undefined,
    statusText: statusRaw != null && String(statusRaw).trim() ? String(statusRaw) : undefined,
    stepLabel: stepLabelRaw != null && String(stepLabelRaw).trim() ? String(stepLabelRaw) : undefined,
    priorSummary: priorRaw != null && String(priorRaw).trim() ? String(priorRaw) : undefined,
  }
}

/** Best-effort parse of a label like "Explore 2/3" when only the string is present. */
export function snapshotFromPhaseLabel(label: string | null | undefined): DevPhaseGraphSnapshot | null {
  if (!label) return null
  const m = label.trim().match(/^(Explore|Patch|Verify|Stuck|Done)\s*(?:(\d+)\s*\/\s*(\d+))?/i)
  if (!m) return null
  const name = m[1].toLowerCase()
  const count = m[2] != null ? Number(m[2]) : 0
  const max = m[3] != null ? Number(m[3]) : name === 'explore' ? 3 : name === 'patch' ? 4 : 2
  const snap: DevPhaseGraphSnapshot = {
    phase: name,
    label: label.trim(),
    exploreCount: 0,
    exploreMax: 3,
    patchCount: 0,
    patchMax: 4,
    verifyCount: 0,
    verifyMax: 2,
  }
  if (name === 'explore') {
    snap.exploreCount = count
    snap.exploreMax = max
  } else if (name === 'patch') {
    snap.patchCount = count
    snap.patchMax = max
  } else if (name === 'verify') {
    snap.verifyCount = count
    snap.verifyMax = max
  }
  return snap
}

export function resolveDevPhaseSnapshot(args: {
  snapshot?: unknown
  label?: string | null
}): DevPhaseGraphSnapshot | null {
  return parseDevPhaseSnapshot(args.snapshot) ?? snapshotFromPhaseLabel(args.label)
}

export function buildDevPhaseSegments(snap: DevPhaseGraphSnapshot): DevPhaseSegment[] {
  const phase = String(snap.phase || 'explore').toLowerCase()
  let currentIdx = PHASE_ORDER.indexOf(phase as (typeof PHASE_ORDER)[number])
  if (phase === 'done') currentIdx = 2
  if (phase === 'stuck') {
    if (!snap.writeSucceeded && (snap.patchCount ?? 0) > 0) currentIdx = 1
    else currentIdx = 0
  }

  return PHASE_ORDER.map((id, idx) => {
    const count =
      id === 'explore'
        ? snap.exploreCount ?? 0
        : id === 'patch'
          ? snap.patchCount ?? 0
          : snap.verifyCount ?? 0
    const max =
      id === 'explore'
        ? snap.exploreMax ?? 3
        : id === 'patch'
          ? snap.patchMax ?? 4
          : snap.verifyMax ?? 2
    let state: DevPhaseSegmentState = 'upcoming'
    if (phase === 'stuck') {
      if (idx < currentIdx) state = 'done'
      else if (idx === currentIdx) state = 'stuck'
      else state = 'upcoming'
    } else if (phase === 'done') {
      state = 'done'
    } else if (idx < currentIdx) {
      state = 'done'
    } else if (idx === currentIdx) {
      state = 'current'
    }
    return {
      id,
      label: id === 'explore' ? 'Explore' : id === 'patch' ? 'Patch' : 'Verify',
      count,
      max,
      state,
    }
  })
}

export function segmentClassName(state: DevPhaseSegmentState, compact?: boolean): string {
  const base = compact
    ? 'text-[9px] px-1 py-0.5 rounded border font-mono'
    : 'text-[10px] px-1.5 py-0.5 rounded border font-mono'
  if (state === 'current') {
    return `${base} border-sky-500/50 text-sky-100 bg-sky-950/50`
  }
  if (state === 'done') {
    return `${base} border-emerald-500/30 text-emerald-200/80 bg-emerald-950/20`
  }
  if (state === 'stuck') {
    return `${base} border-rose-500/50 text-rose-200 bg-rose-950/40`
  }
  return `${base} border-cat-surface1 text-cat-overlay bg-transparent`
}

export type DiagramNodeId = 'explore' | 'patch' | 'verify' | 'stuck' | 'done'

export interface DiagramNodeState {
  id: DiagramNodeId
  label: string
  state: DevPhaseSegmentState | 'idle'
  count?: number
  max?: number
}

export interface DoneExploreLoopMeta {
  /** Edge is sky-highlighted (restart in progress). */
  active: boolean
  /** Dashed when teaching next-step at Done, or idle first cycle. */
  dashed: boolean
  /** Short label drawn on the Done→Explore edge. */
  edgeLabel: string
  /** Caption above the diagram. */
  caption: string
}

/** Label / highlight for the Done → Explore cycle-restart edge. */
export function doneExploreLoopMeta(snap: DevPhaseGraphSnapshot): DoneExploreLoopMeta {
  const phase = String(snap.phase || '').toLowerCase()
  const cycle = Number(snap.cycle ?? 0) || 1
  const stepLabel = (snap.stepLabel || '').trim() || `Cycle ${cycle}`
  const prior = (snap.priorSummary || '').trim()
  const restartLive = cycle > 1 && phase !== 'done' && phase !== 'stuck'

  if (restartLive) {
    return {
      active: true,
      dashed: false,
      edgeLabel: prior ? `${stepLabel} · after ${prior}` : stepLabel,
      caption: stepLabel,
    }
  }
  if (phase === 'done') {
    return {
      active: false,
      dashed: true,
      edgeLabel: 'next step → Explore',
      caption: stepLabel,
    }
  }
  return {
    active: false,
    dashed: true,
    edgeLabel: 'next step',
    caption: stepLabel,
  }
}

/** Node highlight map for the SVG state-machine diagram. */
export function diagramNodeStates(snap: DevPhaseGraphSnapshot): DiagramNodeState[] {
  const segments = buildDevPhaseSegments(snap)
  const phase = String(snap.phase || 'explore').toLowerCase()
  const byId = Object.fromEntries(segments.map((s) => [s.id, s])) as Record<
    'explore' | 'patch' | 'verify',
    DevPhaseSegment
  >

  const endState = (id: 'stuck' | 'done'): DevPhaseSegmentState | 'idle' => {
    if (id === 'stuck' && phase === 'stuck') return 'stuck'
    if (id === 'done' && phase === 'done') return 'done'
    return 'idle'
  }

  return [
    {
      id: 'explore',
      label: 'Explore',
      state: byId.explore?.state ?? 'upcoming',
      count: byId.explore?.count,
      max: byId.explore?.max,
    },
    {
      id: 'patch',
      label: 'Patch',
      state: byId.patch?.state ?? 'upcoming',
      count: byId.patch?.count,
      max: byId.patch?.max,
    },
    {
      id: 'verify',
      label: 'Verify',
      state: byId.verify?.state ?? 'upcoming',
      count: byId.verify?.count,
      max: byId.verify?.max,
    },
    { id: 'stuck', label: 'Stuck', state: endState('stuck') },
    { id: 'done', label: 'Done', state: endState('done') },
  ]
}
