import { describe, expect, it } from 'vitest'
import { formatToolCallLine, llmCollapsedPreview } from './flowLlmPreview'

describe('llmCollapsedPreview', () => {
  it('prefers error text when present', () => {
    expect(
      llmCollapsedPreview({
        error: '429 rate limit',
        responseContent: 'ignored',
        toolCalls: [{ name: 'x' }],
      }),
    ).toBe('429 rate limit')
  })

  it('shows assistant text when no tools', () => {
    expect(llmCollapsedPreview({ responseContent: 'Hello world', toolCalls: [] })).toBe('Hello world')
  })

  it('summarizes a single tool call with path hint', () => {
    expect(
      llmCollapsedPreview({
        responseContent: '',
        toolCalls: [{ name: 'read_file', arguments: '{"path":"src/a.ts"}' }],
      }),
    ).toBe('→ read_file(src/a.ts)')
  })

  it('summarizes multiple tool calls', () => {
    const preview = llmCollapsedPreview({
      responseContent: '',
      toolCalls: [
        { name: 'read_file', arguments: '{"path":"a"}' },
        { name: 'list_dir', arguments: '{"path":"."}' },
        { name: 'grep', arguments: '{}' },
      ],
    })
    expect(preview).toContain('→ read_file(a)')
    expect(preview).toContain('list_dir(.)')
    expect(preview).toContain('+1 more')
  })

  it('shows empty turn when nothing logged', () => {
    expect(llmCollapsedPreview({})).toBe('(empty model turn)')
  })
})

describe('formatToolCallLine', () => {
  it('handles OpenAI function wrapper shape', () => {
    expect(
      formatToolCallLine({
        function: { name: 'run_command', arguments: '{"command":"npm test"}' },
      }),
    ).toBe('run_command(npm test)')
  })
})
