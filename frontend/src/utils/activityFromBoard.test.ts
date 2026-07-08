import { describe, expect, it } from 'vitest'
import {
  hydrateActivityFromBoard,
  MAX_DECISION_TAIL,
  MAX_TRANSCRIPT_TAIL,
} from './activityFromBoard'
import type { Board, TaskTranscriptEntry } from '../types'

function makeTranscript(n: number): TaskTranscriptEntry[] {
  return Array.from({ length: n }, (_, i) => ({
    role: 'assistant',
    agent: 'Developer',
    content: `entry-${i}`,
    timestamp: `2026-01-01 12:${String(i).padStart(2, '0')}:00`,
  }))
}

describe('hydrateActivityFromBoard', () => {
  it('caps transcript and decision scan per task', () => {
    const board: Board = {
      Backlog: [
        {
          id: 'T-1',
          title: 'Task',
          description: '',
          status: 'Backlog',
          transcript: makeTranscript(MAX_TRANSCRIPT_TAIL + 10),
          decisions: Array.from({ length: MAX_DECISION_TAIL + 5 }, (_, i) => ({
            agent: 'Developer',
            type: 'refinement_dev',
            summary: `decision-${i}`,
            timestamp: `2026-01-02 10:${String(i).padStart(2, '0')}:00`,
          })),
        },
      ],
    }
    const events = hydrateActivityFromBoard(board)
    const transcriptEvents = events.filter((e) => e.kind === 'transcript')
    const decisionEvents = events.filter((e) => e.kind === 'decision')
    expect(transcriptEvents.length).toBeLessThanOrEqual(MAX_TRANSCRIPT_TAIL)
    expect(decisionEvents.length).toBeLessThanOrEqual(MAX_DECISION_TAIL)
    expect(transcriptEvents.some((e) => e.content.includes(`entry-${MAX_TRANSCRIPT_TAIL + 9}`))).toBe(
      true,
    )
    expect(transcriptEvents.some((e) => e.content.includes('entry-0'))).toBe(false)
  })
})
