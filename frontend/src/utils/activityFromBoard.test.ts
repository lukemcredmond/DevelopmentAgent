import { describe, expect, it } from 'vitest'
import {
  hydrateActivityFromBoard,
  mergeActivityEvents,
  MAX_DECISION_TAIL,
  MAX_TRANSCRIPT_TAIL,
} from './activityFromBoard'
import type { ActivityEvent, Board, TaskTranscriptEntry } from '../types'

/** Mirror VirtualScrollList newestFirst: chronological input → newest at index 0. */
function displayNewestFirst<T>(chronological: T[]): T[] {
  return [...chronological].reverse()
}

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

  it('returns chronological order (oldest last) so newestFirst UI puts latest on top', () => {
    const board: Board = {
      Backlog: [
        {
          id: 'T-1',
          title: 'Task',
          description: '',
          status: 'Backlog',
          transcript: [
            {
              role: 'assistant',
              agent: 'Developer',
              content: 'older',
              timestamp: '2026-01-01 10:00:00',
            },
            {
              role: 'assistant',
              agent: 'Developer',
              content: 'newer',
              timestamp: '2026-01-01 11:00:00',
            },
          ],
          decisions: [],
        },
      ],
    }
    const events = hydrateActivityFromBoard(board)
    expect(events[0].content).toBe('older')
    expect(events[events.length - 1].content).toBe('newer')
    const displayed = displayNewestFirst(events)
    expect(displayed[0].content).toBe('newer')
  })
})

describe('mergeActivityEvents', () => {
  it('keeps chronological feed and newest on top after newestFirst reverse', () => {
    const hydrated: ActivityEvent[] = [
      {
        taskId: 'T-1',
        taskTitle: 'A',
        kind: 'transcript',
        role: 'assistant',
        agent: 'Developer',
        content: 'mid',
        timestamp: '2026-01-01 10:30:00',
      },
    ]
    const live: ActivityEvent[] = [
      {
        taskId: 'T-1',
        taskTitle: 'A',
        kind: 'transcript',
        role: 'assistant',
        agent: 'Developer',
        content: 'late',
        timestamp: '2026-01-01 11:00:00',
      },
      {
        taskId: 'T-1',
        taskTitle: 'A',
        kind: 'transcript',
        role: 'assistant',
        agent: 'Developer',
        content: 'early',
        timestamp: '2026-01-01 10:00:00',
      },
    ]
    const merged = mergeActivityEvents(hydrated, live)
    expect(merged.map((e) => e.content)).toEqual(['early', 'mid', 'late'])
    expect(displayNewestFirst(merged)[0].content).toBe('late')
  })
})
