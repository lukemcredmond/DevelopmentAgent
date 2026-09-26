import { describe, expect, it } from 'vitest'
import type { Board, Task } from '../types'
import { buildDependencyGraph, buildHierarchyForest, stallChips } from './cardTree'

function task(partial: Partial<Task> & Pick<Task, 'id' | 'title'>): Task {
  return {
    description: '',
    status: 'Backlog',
    ...partial,
  }
}

describe('buildHierarchyForest', () => {
  it('roots feature lane cards and nests children', () => {
    const board: Board = {
      Features: [
        task({ id: 'F1', title: 'Meal planner', workType: 'feature', status: 'Features' }),
      ],
      Backlog: [
        task({
          id: 'C1',
          title: 'Child impl',
          featureId: 'F1',
          workType: 'implementation',
        }),
      ],
      Blocked: [],
      'In Progress': [],
      'Needs PO': [],
      'Needs User': [],
      QA: [],
      Done: [],
      'Code Review': [],
    }
    const { roots, unlinked } = buildHierarchyForest(board)
    expect(roots).toHaveLength(1)
    expect(roots[0].task.id).toBe('F1')
    expect(roots[0].children.map((c) => c.task.id)).toContain('C1')
    expect(unlinked).toHaveLength(0)
  })

  it('puts orphan implementation in Unlinked', () => {
    const board: Board = {
      Features: [],
      Backlog: [task({ id: 'O1', title: 'Orphan', workType: 'implementation' })],
      Blocked: [],
      'In Progress': [],
      'Needs PO': [],
      'Needs User': [],
      QA: [],
      Done: [],
      'Code Review': [],
    }
    const { unlinked } = buildHierarchyForest(board)
    expect(unlinked).toHaveLength(1)
    expect(unlinked[0].task.id).toBe('O1')
  })
})

describe('buildDependencyGraph', () => {
  it('builds edges from blockedBy', () => {
    const board: Board = {
      Features: [],
      Backlog: [
        task({ id: 'A', title: 'Blocker' }),
        task({ id: 'B', title: 'Blocked task', blockedBy: ['A'] }),
      ],
      Blocked: [],
      'In Progress': [],
      'Needs PO': [],
      'Needs User': [],
      QA: [],
      Done: [],
      'Code Review': [],
    }
    const graph = buildDependencyGraph(board)
    expect(graph.edges).toEqual([{ fromId: 'A', toId: 'B', outcomeStatus: undefined }])
    expect(graph.roots.some((r) => r.task.id === 'A')).toBe(true)
  })
})

describe('stallChips', () => {
  it('includes defer and force patch', () => {
    const chips = stallChips(
      task({
        id: 'X',
        title: 't',
        forcePatchNextDevStep: true,
        devDeferredUntilStep: 12,
        phaseCycleCapReached: true,
      }),
    )
    expect(chips).toContain('forcePatch')
    expect(chips.some((c) => c.startsWith('defer→'))).toBe(true)
    expect(chips).toContain('phaseCap')
  })
})
