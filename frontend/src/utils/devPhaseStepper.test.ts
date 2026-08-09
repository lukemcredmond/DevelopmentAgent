import { describe, expect, it } from 'vitest'
import {
  buildDevPhaseSegments,
  diagramNodeStates,
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
    })
    expect(snap?.cycle).toBe(2)
    expect(snap?.statusText).toContain('budgets reset')

    const snake = parseDevPhaseSnapshot({
      phase: 'done',
      cycle: 1,
      status_text: 'Verify budget finished for this step (not board Done).',
      write_succeeded: true,
    })
    expect(snake?.statusText).toContain('not board Done')
    expect(snake?.cycle).toBe(1)
  })
})
