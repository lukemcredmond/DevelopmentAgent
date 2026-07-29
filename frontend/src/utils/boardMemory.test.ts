import { describe, expect, it } from 'vitest'
import {
  CLIENT_TASK_FILES_CAP,
  CLIENT_TRANSCRIPT_CAP,
  mergeTaskHistory,
  trimBoardHistory,
  trimTaskHistory,
} from './boardMemory'
import type { Task } from '../types'

function task(partial: Partial<Task> & { id: string }): Task {
  return {
    title: 't',
    description: '',
    acceptanceCriteria: [],
    transcript: [],
    ...partial,
  } as Task
}

describe('boardMemory', () => {
  it('trims long transcripts to CLIENT_TRANSCRIPT_CAP', () => {
    const t = task({
      id: '1',
      transcript: Array.from({ length: CLIENT_TRANSCRIPT_CAP + 20 }, (_, i) => ({
        role: 'assistant',
        content: `m${i}`,
      })),
    })
    const trimmed = trimTaskHistory(t)
    expect(trimmed.transcript?.length).toBe(CLIENT_TRANSCRIPT_CAP)
    expect(trimmed.transcriptTruncated).toBe(true)
    expect(trimmed.transcript?.[0]).toEqual(
      expect.objectContaining({ content: `m${20}` }),
    )
  })

  it('mergeTaskHistory prefers incoming slim over longer client copy', () => {
    const existing = task({
      id: '1',
      transcript: Array.from({ length: 80 }, (_, i) => ({
        role: 'assistant',
        content: `old${i}`,
      })),
    })
    const incoming = task({
      id: '1',
      transcriptTruncated: true,
      transcript: Array.from({ length: 5 }, (_, i) => ({
        role: 'assistant',
        content: `new${i}`,
      })),
    })
    const merged = mergeTaskHistory(incoming, existing)
    expect(merged.transcript?.length).toBe(5)
    expect(merged.transcript?.[0]).toEqual(expect.objectContaining({ content: 'new0' }))
  })

  it('trims task.files to CLIENT_TASK_FILES_CAP', () => {
    const t = task({
      id: '1',
      files: Array.from({ length: CLIENT_TASK_FILES_CAP + 10 }, (_, i) => ({
        path: `f${i}.ts`,
        action: 'touched',
      })),
    })
    const trimmed = trimTaskHistory(t)
    expect(trimmed.files?.length).toBe(CLIENT_TASK_FILES_CAP)
  })

  it('trimBoardHistory caps every lane', () => {
    const board = trimBoardHistory({
      Backlog: [
        task({
          id: 'a',
          transcript: Array.from({ length: 60 }, (_, i) => ({
            role: 'user',
            content: String(i),
          })),
        }),
      ],
      'In Progress': [],
      'Code Review': [],
      QA: [],
      Done: [],
      'Needs User': [],
      'Needs PO': [],
      'Pending Approval': [],
      Blocked: [],
      Refinement: [],
    })
    expect(board.Backlog[0].transcript?.length).toBe(CLIENT_TRANSCRIPT_CAP)
  })
})
