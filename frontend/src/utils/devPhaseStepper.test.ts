import { describe, expect, it } from 'vitest'
import {
  buildDevPhaseSegments,
  buildUnrolledPhaseRows,
  diagramNodeStates,
  doneExploreLoopMeta,
  parseDevPhaseSnapshot,
  resolveDevPhaseSnapshot,
  snapshotFromPhaseLabel,
} from './devPhaseStepper'

describe('devPhaseStepper helpers', () => {
  it('parses structured snapshot', () => {
    const snap = parseDevPhaseSnapshot({
      phase: 'patch',
      label: 'Patch 1/4',
      exploreCount: 3,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
    })
    expect(snap?.phase).toBe('patch')
    const segs = buildDevPhaseSegments(snap!)
    expect(segs.map((s) => s.state)).toEqual(['done', 'current', 'upcoming'])
    expect(segs[0].count).toBe(3)
    expect(segs[1].count).toBe(1)
  })

  it('highlights verify as current', () => {
    const segs = buildDevPhaseSegments({
      phase: 'verify',
      exploreCount: 2,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
      verifyCount: 1,
      verifyMax: 2,
      writeSucceeded: true,
    })
    expect(segs.map((s) => s.state)).toEqual(['done', 'done', 'current'])
  })

  it('marks explore stuck when explore budget exhausted', () => {
    const segs = buildDevPhaseSegments({
      phase: 'stuck',
      exploreCount: 3,
      exploreMax: 3,
      patchCount: 0,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
      writeSucceeded: false,
    })
    expect(segs[0].state).toBe('stuck')
    expect(segs[1].state).toBe('upcoming')
  })

  it('marks patch stuck when patch attempts failed', () => {
    const segs = buildDevPhaseSegments({
      phase: 'stuck',
      exploreCount: 2,
      exploreMax: 3,
      patchCount: 4,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
      writeSucceeded: false,
    })
    expect(segs.map((s) => s.state)).toEqual(['done', 'stuck', 'upcoming'])
  })

  it('falls back from label Explore 2/3', () => {
    const snap = snapshotFromPhaseLabel('Explore 2/3')
    expect(snap?.phase).toBe('explore')
    expect(snap?.exploreCount).toBe(2)
    expect(snap?.exploreMax).toBe(3)
    expect(resolveDevPhaseSnapshot({ label: 'Verify 1/2' })?.phase).toBe('verify')
  })

  it('marks all done when phase is done', () => {
    const segs = buildDevPhaseSegments({
      phase: 'done',
      exploreCount: 1,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
      verifyCount: 2,
      verifyMax: 2,
      writeSucceeded: true,
    })
    expect(segs.every((s) => s.state === 'done')).toBe(true)
  })

  it('diagramNodeStates mirrors segment states and end nodes', () => {
    const nodes = diagramNodeStates({
      phase: 'patch',
      exploreCount: 2,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
    })
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]))
    expect(byId.explore?.state).toBe('done')
    expect(byId.patch?.state).toBe('current')
    expect(byId.verify?.state).toBe('upcoming')
    expect(byId.stuck?.state).toBe('idle')
    expect(byId.done?.state).toBe('idle')
    expect(byId.patch?.count).toBe(1)
    expect(byId.patch?.max).toBe(4)
  })

  it('diagramNodeStates marks stuck and done terminals', () => {
    const stuck = diagramNodeStates({
      phase: 'stuck',
      exploreCount: 3,
      exploreMax: 3,
      patchCount: 0,
      patchMax: 4,
      writeSucceeded: false,
    })
    expect(stuck.find((n) => n.id === 'stuck')?.state).toBe('stuck')
    expect(stuck.find((n) => n.id === 'explore')?.state).toBe('stuck')

    const done = diagramNodeStates({
      phase: 'done',
      exploreCount: 1,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
      verifyCount: 1,
      verifyMax: 2,
      writeSucceeded: true,
    })
    expect(done.find((n) => n.id === 'done')?.state).toBe('done')
    expect(done.filter((n) => ['explore', 'patch', 'verify'].includes(n.id)).every((n) => n.state === 'done')).toBe(
      true,
    )
  })

  it('parses cycle and statusText from camel or snake case', () => {
    const snap = parseDevPhaseSnapshot({
      phase: 'explore',
      exploreCount: 0,
      exploreMax: 3,
      patchCount: 0,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
      cycle: 2,
      statusText: 'New Developer step — budgets reset',
      stepLabel: 'Cycle 2 · AC 1',
      priorSummary: 'Verify Done',
    })
    expect(snap?.cycle).toBe(2)
    expect(snap?.statusText).toContain('budgets reset')
    expect(snap?.stepLabel).toBe('Cycle 2 · AC 1')
    expect(snap?.priorSummary).toBe('Verify Done')

    const snake = parseDevPhaseSnapshot({
      phase: 'done',
      cycle: 1,
      status_text: 'Verify budget finished for this step (not board Done).',
      write_succeeded: true,
      step_label: 'Cycle 1',
      prior_summary: '',
    })
    expect(snake?.statusText).toContain('not board Done')
    expect(snake?.cycle).toBe(1)
    expect(snake?.stepLabel).toBe('Cycle 1')
  })

  it('doneExploreLoopMeta labels Done→Explore restart and teach edge', () => {
    const restart = doneExploreLoopMeta({
      phase: 'explore',
      cycle: 2,
      stepLabel: 'Cycle 2',
      priorSummary: 'Verify Done',
      exploreCount: 0,
      exploreMax: 3,
    })
    expect(restart.active).toBe(true)
    expect(restart.dashed).toBe(false)
    expect(restart.edgeLabel).toBe('Cycle 2 · after Verify Done')
    expect(restart.caption).toBe('Cycle 2')

    const atDone = doneExploreLoopMeta({
      phase: 'done',
      cycle: 1,
      stepLabel: 'Cycle 1',
      exploreCount: 1,
      exploreMax: 3,
      verifyCount: 2,
      verifyMax: 2,
      writeSucceeded: true,
    })
    expect(atDone.active).toBe(false)
    expect(atDone.dashed).toBe(true)
    expect(atDone.edgeLabel).toBe('next step → Explore')
    expect(atDone.caption).toBe('Cycle 1')

    const first = doneExploreLoopMeta({
      phase: 'patch',
      cycle: 1,
      stepLabel: 'Cycle 1',
      exploreCount: 2,
      exploreMax: 3,
      patchCount: 1,
      patchMax: 4,
    })
    expect(first.active).toBe(false)
    expect(first.edgeLabel).toBe('next step')
  })

  it('buildUnrolledPhaseRows stacks history then live; Done connects to next Explore', () => {
    const rows = buildUnrolledPhaseRows({
      phase: 'explore',
      cycle: 2,
      stepLabel: 'Cycle 2',
      priorSummary: 'Verify Done',
      exploreCount: 0,
      exploreMax: 3,
      patchCount: 0,
      patchMax: 4,
      verifyCount: 0,
      verifyMax: 2,
      cycleHistory: [
        {
          cycle: 1,
          stepLabel: 'Cycle 1',
          terminalPhase: 'done',
          exploreCount: 1,
          patchCount: 1,
          verifyCount: 2,
          writeSucceeded: true,
        },
      ],
    })
    expect(rows).toHaveLength(2)
    expect(rows[0].live).toBe(false)
    expect(rows[0].phase).toBe('done')
    expect(rows[0].connectsToNext).toBe(true)
    expect(rows[0].nodes.find((n) => n.id === 'done')?.state).toBe('done')
    expect(rows[1].live).toBe(true)
    expect(rows[1].stepLabel).toBe('Cycle 2')
    expect(rows[1].phase).toBe('explore')
    expect(rows[1].connectsToNext).toBe(false)
    expect(rows[1].nodes.find((n) => n.id === 'explore')?.state).toBe('current')
    // Path is Cycle1 Done → Cycle2 Explore (new Explore), not back to Cycle1 Explore
    expect(rows[0].cycle).toBe(1)
    expect(rows[1].cycle).toBe(2)
  })

  it('parses cycleHistory from snapshot payload', () => {
    const snap = parseDevPhaseSnapshot({
      phase: 'explore',
      cycle: 2,
      cycleHistory: [
        {
          cycle: 1,
          stepLabel: 'Cycle 1',
          terminalPhase: 'done',
          exploreCount: 2,
          patchCount: 1,
          verifyCount: 2,
          writeSucceeded: true,
        },
      ],
    })
    expect(snap?.cycleHistory).toHaveLength(1)
    expect(snap?.cycleHistory?.[0].terminalPhase).toBe('done')
  })

  it('parses rewindCount and attaches to live unrolled row', () => {
    const snap = parseDevPhaseSnapshot({
      phase: 'patch',
      cycle: 2,
      rewindCount: 2,
      lastRewindDetail: 'Context rewind ×2 — removed 3 message(s)',
      cycleHistory: [
        {
          cycle: 1,
          stepLabel: 'Cycle 1',
          terminalPhase: 'done',
        },
      ],
    })
    expect(snap?.rewindCount).toBe(2)
    expect(snap?.lastRewindDetail).toContain('rewind')
    const rows = buildUnrolledPhaseRows(snap!)
    expect(rows).toHaveLength(2)
    expect(rows[1].live).toBe(true)
    expect(rows[1].rewindCount).toBe(2)
    expect(rows[0].rewindCount).toBeUndefined()
  })
})
