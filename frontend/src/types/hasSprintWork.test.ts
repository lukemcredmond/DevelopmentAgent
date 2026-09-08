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
  it('does not count exhausted latched In Progress as work', () => {
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

  it('counts latched Needs PO until a park has been attempted', () => {
    const board = {
      ...EMPTY_BOARD,
      'Needs PO': [
        task({
          id: 'npo',
          status: 'Needs PO',
          phaseCycleCapReached: true,
        }),
      ],
    }
    expect(hasSprintWork(board)).toBe(true)
  })

  it('does not count latched Needs PO after park or auto-skip', () => {
    const boarded = {
      ...EMPTY_BOARD,
      'Needs PO': [
        task({
          id: 'npo-done',
          status: 'Needs PO',
          phaseCycleCapReached: true,
          latchedRecoveryAttempted: true,
        }),
      ],
    }
    expect(hasSprintWork(boarded)).toBe(false)
    const skipped = {
      ...EMPTY_BOARD,
      'Needs PO': [
        task({
          id: 'npo-skip',
          status: 'Needs PO',
          poAutoSkip: true,
        }),
      ],
    }
    expect(hasSprintWork(skipped)).toBe(false)
  })

  it('counts runnable Needs PO cards', () => {
    const board = {
      ...EMPTY_BOARD,
      'Needs PO': [
        task({
          id: 'npo-ready',
          status: 'Needs PO',
        }),
      ],
    }
    expect(hasSprintWork(board)).toBe(true)
  })
})
