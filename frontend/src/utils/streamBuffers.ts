import type { SystemLog } from '../types'

/** Match backend MAX_LOG_ENTRIES */
export const MAX_CLIENT_LOG_ENTRIES = 500

export const MAX_TERMINAL_OUTPUT_CHARS = 65536

export function capLogs(prev: SystemLog[], log: SystemLog): SystemLog[] {
  return [...prev, log].slice(-MAX_CLIENT_LOG_ENTRIES)
}

export function appendTerminalOutput(
  prev: string,
  chunk: string,
  maxChars = MAX_TERMINAL_OUTPUT_CHARS,
): string {
  if (!chunk) return prev
  const combined = prev + chunk
  if (combined.length <= maxChars) return combined
  return combined.slice(-maxChars)
}
