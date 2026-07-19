import { describe, expect, it } from 'vitest'
import { mergeToolHistory } from './mergeToolHistory'
import type { ToolExecutionEvent } from '../types'

function ev(partial: Partial<ToolExecutionEvent> & { id: string; timestamp: string }): ToolExecutionEvent {
  return {
    toolName: 'read_file',
    toolArgs: {},
    status: 'ok',
    toolSuccess: true,
    toolOutput: '',
    durationMs: 1,
    agent: 'Developer',
    agentId: 'dev',
    source: 'sprint',
    ...partial,
  }
}

describe('mergeToolHistory', () => {
  it('returns chronological order (oldest last) for newestFirst VirtualScrollList', () => {
    const prev = [
      ev({ id: '2', timestamp: '2026-01-01 12:00:00', toolName: 'grep' }),
      ev({ id: '1', timestamp: '2026-01-01 10:00:00', toolName: 'read_file' }),
    ]
    const incoming = [ev({ id: '3', timestamp: '2026-01-01 13:00:00', toolName: 'write_file' })]
    const merged = mergeToolHistory(prev, incoming)
    expect(merged.map((e) => e.id)).toEqual(['1', '2', '3'])
    const displayed = [...merged].reverse()
    expect(displayed[0].id).toBe('3')
  })
})
