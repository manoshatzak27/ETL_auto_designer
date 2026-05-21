/**
 * ScriptGenerator — generates and previews a single OMOP table's Python script
 * inline within a wizard step. Placed at the bottom of each table mapping step
 * so the user can generate, review, and optionally regenerate before moving on.
 */
import { useState, useEffect, useRef } from 'react'
import { generateTableScript } from '../api/client'
import type { Project } from '../types'
import {
  Sparkles, RefreshCw, Copy, Check,
  ChevronDown, ChevronUp, CheckCircle, FileCode2,
} from 'lucide-react'
import clsx from 'clsx'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

interface Props {
  project: Project
  table: string                            // e.g. "person"
  onUpdate: (p: Project) => void
  /** Called before every generate/regenerate to flush unsaved form state to the backend */
  beforeGenerate?: () => Promise<void>
  /** Extra classes for the generate/regenerate button (e.g. a fixed width for uniform rows) */
  buttonClassName?: string
}

export default function ScriptGenerator({ project, table, onUpdate, beforeGenerate, buttonClassName }: Props) {
  const scripts: Record<string, string> = project.generated_scripts || {}
  const script = scripts[table] || null

  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const prevScriptRef = useRef<string | null>(script)
  const previewRef = useRef<HTMLDivElement>(null)

  // Auto-expand and scroll into view when a new script arrives
  useEffect(() => {
    if (script && script !== prevScriptRef.current) {
      prevScriptRef.current = script
      setOpen(true)
      setTimeout(() => previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80)
    }
  }, [script])

  const handleGenerate = async () => {
    setGenerating(true)
    setError('')
    try {
      // Flush unsaved form state (extra_instructions, mappings, etc.) before generating
      if (beforeGenerate) await beforeGenerate()
      const updated = await generateTableScript(project.id, table)
      onUpdate(updated)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setError(err?.response?.data?.detail || `Failed to generate ${table}.py`)
    } finally {
      setGenerating(false)
    }
  }

  const handleCopy = async () => {
    if (!script) return
    await navigator.clipboard.writeText(script)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const lineCount = script ? script.split('\n').length : 0

  return (
    <div ref={previewRef} className="flex flex-col gap-3">
      {/* Generator card */}
      <Card className={clsx(
        'overflow-hidden p-0',
        script ? 'border-green-200' : 'border-border',
      )}>
        {/* Header */}
        <div className={clsx(
          'flex items-center gap-3 px-5 py-4',
          script ? 'bg-green-50' : 'bg-secondary/60',
        )}>
          <div className={clsx(
            'w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0',
            script ? 'bg-green-100' : 'bg-accent',
          )}>
            {generating
              ? <RefreshCw className="w-4 h-4 text-primary animate-spin" />
              : script
                ? <CheckCircle className="w-4 h-4 text-green-500" />
                : <FileCode2 className="w-4 h-4 text-primary" />
            }
          </div>

          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-foreground font-mono">{table}.py</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {generating
                ? 'GPT-4o is writing the transformation script…'
                : script
                  ? `Generated · ${lineCount} lines · based on VOLABIOS reference`
                  : 'Click Generate to create the Python ETL script for this table'
              }
            </p>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {script && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOpen(o => !o)}
                className="text-xs h-7 px-2.5"
              >
                {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                {open ? 'Collapse' : 'View code'}
              </Button>
            )}

            <Button
              variant={script ? 'outline' : 'default'}
              size="sm"
              onClick={handleGenerate}
              disabled={generating}
              className={clsx('text-xs h-7 px-4', buttonClassName)}
            >
              {generating
                ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Generating…</>
                : <><Sparkles className="w-3.5 h-3.5 text-amber-500" />{script ? 'Regenerate' : `Generate ${table}.py`}</>
              }
            </Button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="px-5 py-3 bg-destructive/10 border-t border-destructive/30 text-xs text-destructive">
            {error}
          </div>
        )}

        {/* Code preview */}
        {open && script && (
          <div className="border-t border-border">
            {/* Toolbar */}
            <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400 font-mono">{table}.py</span>
                <span className="text-xs text-gray-600">·</span>
                <span className="text-xs text-gray-500">{lineCount} lines</span>
              </div>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 px-2 py-1 rounded hover:bg-gray-700 transition-colors"
              >
                {copied
                  ? <><Check className="w-3 h-3 text-green-400" /><span className="text-green-400">Copied!</span></>
                  : <><Copy className="w-3 h-3" />Copy</>
                }
              </button>
            </div>

            {/* Code with line numbers */}
            <div className="bg-gray-950 overflow-auto max-h-[520px]">
              <table className="w-full text-xs font-mono border-collapse">
                <tbody>
                  {script.split('\n').map((line, i) => (
                    <tr key={i} className="hover:bg-gray-800/40">
                      <td className="select-none text-right pr-4 pl-3 py-0.5 text-gray-600 w-10 border-r border-gray-800 align-top leading-5">
                        {i + 1}
                      </td>
                      <td className="pl-4 pr-4 py-0.5 text-gray-100 whitespace-pre align-top leading-5">
                        {line || ' '}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      {/* Hint when not yet generated */}
      {!script && !generating && (
        <p className="text-xs text-muted-foreground px-1">
          You can also skip and generate all scripts at once from the final step.
        </p>
      )}
    </div>
  )
}
