import { describe, expect, it } from 'vitest'
import type { SystemLog } from '../types'
import {
  MAX_CLIENT_LOG_ENTRIES,
  MAX_TERMINAL_OUTPUT_CHARS,
  appendTerminalOutput,
  capLogs,
} from './streamBuffers'

function log(text: string, timestamp = '0'): SystemLog {
  return { timestamp, source: 'System', type: 'info', text }
}

describe('capLogs', () => {
  it('keeps at most MAX_CLIENT_LOG_ENTRIES', () => {
    const base = Array.from({ length: MAX_CLIENT_LOG_ENTRIES }, (_, i) => log(`log-${i}`, String(i)))
    const next = capLogs(base, log('latest', 'new'))
    expect(next.length).toBe(MAX_CLIENT_LOG_ENTRIES)
    expect(next[next.length - 1]?.text).toBe('latest')
  })

  it('drops oldest when over cap', () => {
    let logs: SystemLog[] = []
    for (let i = 0; i < MAX_CLIENT_LOG_ENTRIES + 100; i++) {
      logs = capLogs(logs, log(`entry-${i}`, String(i)))
    }
    expect(logs.length).toBe(MAX_CLIENT_LOG_ENTRIES)
    expect(logs[0]?.text).toBe(`entry-${100}`)
  })
})

describe('appendTerminalOutput', () => {
  it('truncates to tail when exceeding max chars', () => {
    const chunk = 'x'.repeat(1000)
    let output = ''
    for (let i = 0; i < 100; i++) {
      output = appendTerminalOutput(output, chunk, 5000)
    }
    expect(output.length).toBeLessThanOrEqual(5000)
    expect(output.endsWith('x')).toBe(true)
  })

  it('returns prev when chunk is empty', () => {
    expect(appendTerminalOutput('hello', '')).toBe('hello')
  })

  it('defaults to MAX_TERMINAL_OUTPUT_CHARS', () => {
    const huge = 'a'.repeat(MAX_TERMINAL_OUTPUT_CHARS + 500)
    const result = appendTerminalOutput('', huge)
    expect(result.length).toBe(MAX_TERMINAL_OUTPUT_CHARS)
  })
})
