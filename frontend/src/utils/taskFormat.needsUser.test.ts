import { describe, expect, it } from 'vitest'
import type { Task } from '../types'
import { needsUserCardPreview, needsUserEvidenceText } from './taskFormat'

function baseTask(over: Partial<Task> = {}): Task {
  return {
    id: 'TASK-23C1',
    title: 'Implement Export to storage location — core flow',
    description: 'Serialize meals to JSON.',
    status: 'Needs User',
    ...over,
  }
}

describe('needsUserCardPreview', () => {
  it('uses last-step why when userQuestion is empty', () => {
    const task = baseTask({
      userQuestion: null,
      lastStepOutcome: {
        taskId: 'TASK-23C1',
        agent: 'Developer',
        laneBefore: 'In Progress',
        laneAfter: 'In Progress',
        toolFailures: 0,
        ok: false,
        message: 'Dev step read files but made no edits.',
        stopReason: 'read_only_no_edits',
        whyCardStayed:
          'Developer read files but never called apply_patch/write_file on Export.',
      },
    })
    const preview = needsUserCardPreview(task)
    expect(preview).toContain('never called apply_patch')
    expect(preview.toLowerCase()).not.toContain('open the card')
    expect(needsUserEvidenceText(task)).toContain('never called apply_patch')
  })

  it('prefers a specific userQuestion over last-step copy', () => {
    const task = baseTask({
      userQuestion: 'Which file should Developer create first?',
      lastStepOutcome: {
        taskId: 'TASK-23C1',
        agent: 'Developer',
        laneBefore: 'In Progress',
        laneAfter: 'Needs User',
        toolFailures: 0,
        ok: false,
        message: 'stalled',
        whyCardStayed: 'read only',
      },
    })
    expect(needsUserCardPreview(task)).toBe('Which file should Developer create first?')
  })
})
