import { describe, expect, it } from 'vitest'
import type { SystemLog } from '../types'
import { isSprintProgressActive } from '../components/SprintProgressBar'
import { mapCardProgress, mapSprintProgress } from '../utils/sprintProgress'
import { mergeLogsPreservingLive } from '../utils/streamBuffers'

function log(text: string, timestamp = '0'): SystemLog {
  return { timestamp, source: 'System', type: 'info', text }
}

describe('mergeLogsPreservingLive', () => {
  it('keeps prior logs when incoming is empty', () => {
    const prev = [log('a', '1'), log('b', '2')]
    expect(mergeLogsPreservingLive(prev, [])).toBe(prev)
    expect(mergeLogsPreservingLive(prev, undefined)).toBe(prev)
  })

  it('keeps prior logs when incoming is shorter', () => {
    const prev = [log('a', '1'), log('b', '2'), log('c', '3')]
    const incoming = [log('x', '9')]
    expect(mergeLogsPreservingLive(prev, incoming)).toBe(prev)
  })

  it('accepts incoming when it is at least as long as prior logs', () => {
    const prev = [log('a', '1')]
    const incoming = [log('a', '1'), log('b', '2')]
    expect(mergeLogsPreservingLive(prev, incoming)).toEqual(incoming)
  })
})

describe('isSprintProgressActive', () => {
  it('is active when sprintRunning is true without progress', () => {
    expect(
      isSprintProgressActive({
        planRunActive: false,
        sprintRunning: true,
        progress: null,
      }),
    ).toBe(true)
  })

  it('is inactive when sprint is idle with no progress', () => {
    expect(
      isSprintProgressActive({
        planRunActive: false,
        sprintRunning: false,
        progress: null,
      }),
    ).toBe(false)
  })

  it('stays active for in-flight sprint_step progress', () => {
    expect(
      isSprintProgressActive({
        planRunActive: false,
        sprintRunning: false,
        progress: {
          phase: 'sprint_step',
          step: 2,
          maxSteps: 20,
          agent: 'Developer',
          taskId: 'T-1',
          taskTitle: 'Work',
          lane: 'In Progress',
        },
      }),
    ).toBe(true)
  })
})

describe('mapCardProgress', () => {
  it('maps snake_case card_progress fields', () => {
    const mapped = mapCardProgress({
      subtasks_done: 2,
      subtasks_total: 5,
      stuck_loops: 1,
      gates_remaining: ['QA', 'Done'],
    })
    expect(mapped?.subtasksDone).toBe(2)
    expect(mapped?.subtasksTotal).toBe(5)
    expect(mapped?.stuckLoops).toBe(1)
    expect(mapped?.gatesRemaining).toEqual(['QA', 'Done'])
  })
})

describe('mapSprintProgress', () => {
  it('maps unknown phase to po_plan', () => {
    const mapped = mapSprintProgress({ phase: 'mystery', step: 3, maxSteps: 10 })
    expect(mapped.phase).toBe('po_plan')
    expect(mapped.step).toBe(3)
    expect(mapped.maxSteps).toBe(10)
  })

  it('maps snake_case backend fields', () => {
    const mapped = mapSprintProgress({
      phase: 'sprint_step',
      step: 1,
      max_steps: 15,
      task_id: 'T-2',
      task_title: 'Fix bug',
      status: 'starting',
    })
    expect(mapped.phase).toBe('sprint_step')
    expect(mapped.maxSteps).toBe(15)
    expect(mapped.taskId).toBe('T-2')
    expect(mapped.taskTitle).toBe('Fix bug')
    expect(mapped.status).toBe('starting')
  })

  it('maps ollama wait and phase cycle cap fields', () => {
    const mapped = mapSprintProgress({
      phase: 'sprint_step',
      step: 2,
      maxSteps: 20,
      ollamaWaitSec: 120,
      ollamaWaitMaxSec: 900,
      phaseCycleCapReached: true,
    })
    expect(mapped.ollamaWaitSec).toBe(120)
    expect(mapped.ollamaWaitMaxSec).toBe(900)
    expect(mapped.phaseCycleCapReached).toBe(true)
  })
})
