import { describe, expect, it } from 'vitest'
import { mergeAppStateIdentity } from './mergeAppState'
import { EMPTY_BOARD } from '../types'
import type { AppState } from '../types'

function state(partial: Partial<AppState>): AppState {
  return {
    projectId: 'p1',
    projectName: 'One',
    brief: '',
    workspaceDir: '.',
    skillsDir: '.',
    board: { ...EMPTY_BOARD },
    files: {},
    logs: [],
    availableSkills: [],
    assignedSkills: { po: [], dev: [], cr: [], qa: [] },
    models: { po: '', dev: '', cr: '', qa: '' },
    backupModels: { po: '', dev: '', cr: '', qa: '' },
    projectsList: [{ id: 'p1', name: 'One' }],
    ...partial,
  } as AppState
}

describe('mergeAppStateIdentity', () => {
  it('keeps prev projectsList when incoming list is empty', () => {
    const prev = state({
      projectsList: [
        { id: 'p1', name: 'One' },
        { id: 'p2', name: 'Two' },
      ],
    })
    const incoming = state({ projectsList: [] })
    const merged = mergeAppStateIdentity(prev, incoming)
    expect(merged.projectsList).toHaveLength(2)
    expect(merged.projectId).toBe('p1')
  })

  it('keeps prev projectId when incoming id is missing', () => {
    const prev = state({ projectId: 'keep-me' })
    const incoming = state({ projectId: '' })
    const merged = mergeAppStateIdentity(prev, incoming)
    expect(merged.projectId).toBe('keep-me')
  })

  it('keeps prev board on SSE when incoming board is empty', () => {
    const prev = state({
      board: {
        ...EMPTY_BOARD,
        Backlog: [{ id: 'T1', title: 'Card', description: 'd', status: 'Backlog' }],
      },
    })
    const incoming = state({ board: { ...EMPTY_BOARD } })
    const merged = mergeAppStateIdentity(prev, incoming, { preserveEmptyBoard: true })
    expect(merged.board.Backlog).toHaveLength(1)
  })
})
