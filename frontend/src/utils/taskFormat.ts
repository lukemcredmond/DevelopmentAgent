/** Safely format task fields that may be objects from LLM JSON. */
export function formatTaskText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>
    for (const key of ['description', 'text', 'criteria', 'title', 'summary']) {
      const nested = obj[key]
      if (nested != null && nested !== '') return formatTaskText(nested)
    }
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function formatAcceptanceCriteria(items: unknown[] | undefined): string[] {
  return (items ?? []).map((item) => formatTaskText(item).trim()).filter(Boolean)
}
