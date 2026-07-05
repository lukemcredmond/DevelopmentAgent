import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import { useEffect, useRef, useState } from 'react'
import { runTerminal } from '../api/client'
import '@xterm/xterm/css/xterm.css'

interface TerminalPanelProps {
  workspaceDir: string
  hidden?: boolean
}

function terminalOutput(result: {
  output?: string
  exitCode?: number
  stdout?: string
  stderr?: string
  returncode?: number
}): { text: string; exitCode: number } {
  const text =
    result.output ??
    [result.stdout, result.stderr].filter(Boolean).join('\n').trim() ??
    '(no output)'
  const exitCode = result.exitCode ?? result.returncode ?? -1
  return { text, exitCode }
}

export default function TerminalPanel({ workspaceDir, hidden = false }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const [command, setCommand] = useState('')
  const [history, setHistory] = useState<string[]>([])

  const safeFit = () => {
    const container = containerRef.current
    const fit = fitRef.current
    if (!container || !fit || container.offsetWidth === 0 || container.offsetHeight === 0) {
      return
    }
    try {
      fit.fit()
    } catch {
      /* xterm not ready */
    }
  }

  useEffect(() => {
    if (!containerRef.current) return

    const term = new Terminal({
      theme: {
        background: '#11111b',
        foreground: '#cdd6f4',
        cursor: '#6366f1',
        selectionBackground: '#45475a',
      },
      fontFamily: 'ui-monospace, Consolas, monospace',
      fontSize: 12,
      cursorBlink: true,
    })

    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(containerRef.current)

    term.writeln('\x1b[1;34mAll Hands Terminal\x1b[0m — type a command below')
    term.writeln(`cwd: ${workspaceDir}`)
    term.writeln('')

    terminalRef.current = term
    fitRef.current = fit

    const raf = requestAnimationFrame(() => safeFit())
    const observer = new ResizeObserver(() => safeFit())
    observer.observe(containerRef.current)
    window.addEventListener('resize', safeFit)

    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
      window.removeEventListener('resize', safeFit)
      term.dispose()
      terminalRef.current = null
      fitRef.current = null
    }
  }, [workspaceDir])

  useEffect(() => {
    if (!hidden) {
      requestAnimationFrame(() => safeFit())
    }
  }, [hidden])

  const execute = async () => {
    const cmd = command.trim()
    if (!cmd || !terminalRef.current) return

    const term = terminalRef.current
    term.writeln(`\x1b[1;32m$\x1b[0m ${cmd}`)
    setHistory((h) => [...h, cmd])
    setCommand('')

    try {
      const result = await runTerminal({ command: cmd, cwd: workspaceDir })
      const { text, exitCode } = terminalOutput(result)
      if (text) {
        term.writeln(text.replace(/\n/g, '\r\n'))
      }
      term.writeln(`\x1b[90m[exit ${exitCode}]\x1b[0m`)
    } catch {
      term.writeln('\x1b[31mError: /api/terminal/run unavailable\x1b[0m')
    }
    term.writeln('')
  }

  return (
    <div
      className={`flex flex-col h-full bg-cat-base overflow-hidden ${hidden ? 'hidden' : ''}`}
    >
      <div className="px-4 py-2 border-b border-cat-surface1 shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Terminal
        </h3>
      </div>
      <div ref={containerRef} className="flex-1 min-h-0 p-1" />
      <div className="p-2 border-t border-cat-surface1 flex gap-2 shrink-0">
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void execute()}
          placeholder="Enter command…"
          className="flex-1 bg-cat-surface0 border border-cat-surface1 rounded px-3 py-1.5 text-xs font-mono text-white focus:outline-none"
        />
        <button
          type="button"
          onClick={() => void execute()}
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded text-xs"
        >
          Run
        </button>
        {history.length > 0 && (
          <button
            type="button"
            onClick={() => setCommand(history[history.length - 1] ?? '')}
            className="text-cat-subtext hover:text-white px-2 text-xs"
            title="Previous command"
          >
            ↑
          </button>
        )}
      </div>
    </div>
  )
}
