import { describe, expect, it } from 'vitest'
import { EMPTY_BOARD, hasSprintWork } from './index'
import type { Task } from './index'

function task(partial: Partial<Task> & { id: string }): Task {
  return {
    title: 't',
    description: 'd',
    status: 'In Progress',
    ...partial,
  }
}

describe('hasSprintWork', () => {
  it('ignores In Progress cards that are latched after recovery', () => {
    const board = {
      ...EMPTY_BOARD,
      'In Progress': [
        task({
          id: 'latched',
          phaseCycleCapReached: true,
          latchedRecoveryAttempted: true,
        }),
      ],
    }
    expect(hasSprintWork(board)).toBe(false)
  })

  it('still counts latched cards that have not been recovered', () => {
    const board = {
      ...EMPTY_BOARD,
      'In Progress': [
        task({
          id: 'pending',
          phaseCycleCapReached: true,
          latchedRecoveryAttempted: false,
        }),
      ],
    }
    expect(hasSprintWork(board)).toBe(true)
  })
})
