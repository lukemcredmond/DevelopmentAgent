import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

describe('Needs User option contrast', () => {
  it('sets explicit light text on option labels', () => {
    const src = readFileSync(fileURLToPath(new URL('./TaskDetailModal.tsx', import.meta.url)), 'utf8')
    const chooseOne = src.indexOf('Choose one')
    expect(chooseOne).toBeGreaterThan(0)
    const block = src.slice(chooseOne, chooseOne + 1800)
    expect(block).toContain('opt.label')
    expect(block).toContain('text-white')
    expect(block).toContain('text-amber-100')
    expect(block).not.toMatch(/<span>\s*<span className="font-bold text-amber-200/)
  })
})
