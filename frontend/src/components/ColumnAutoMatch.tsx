import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Bot, Loader2, RefreshCw, Wand2 } from 'lucide-react'
import type { ColumnMatch } from '../api/client'
import type { ColumnAutoMatch, ColumnTarget } from '../hooks/useColumnAutoMatch'

/** Whether the AI fallback is set up, with a click to check again. */
export function AiFallbackBadge({ autoMatch }: { autoMatch: ColumnAutoMatch }) {
  const { llm, checking } = autoMatch
  const [variant, text, title] =
    !llm ? ['muted', 'AI fallback: checking…', 'Checking whether the AI fallback is available'] as const
    : llm.status === 'ready' ? ['success', `AI fallback ready · ${llm.model}`, 'Fields the name and value checks cannot settle are sent to the AI model, with a few sample values of each column. Click to check again.'] as const
    : llm.status === 'not_configured' ? ['muted', 'AI fallback off', `${llm.detail ?? 'Set OPENAI_API_KEY in backend/.env'}, then click to check again.`] as const
    : ['destructive', 'AI fallback error', `${llm.detail ?? 'The AI model could not be reached'}. Click to check again.`] as const
  return (
    <button type="button" onClick={() => autoMatch.checkLlm(true)} disabled={checking} title={title} className="disabled:opacity-60">
      <Badge variant={variant}>
        {checking ? <Loader2 className="size-3 animate-spin" /> : <Bot className="size-3" />}
        {text}
        {!checking && llm && <RefreshCw className="size-3 opacity-60" />}
      </Badge>
    </button>
  )
}

/** The "Auto-match columns" button for a step header, with the AI fallback's
 *  status and switch beside it. */
export function ColumnAutoMatchControls({ autoMatch, targets, filenames, exclude }: {
  autoMatch: ColumnAutoMatch
  targets: ColumnTarget[]
  filenames: string[]
  exclude: Iterable<string>
}) {
  const unmapped = targets.filter(t => !t.current).length
  return (
    <div className="flex flex-col items-end gap-1.5">
      <Button
        variant="outline"
        // A failure is shown by ColumnAutoMatchSummary.
        onClick={() => { autoMatch.run({ filenames, targets, exclude }).catch(() => {}) }}
        disabled={autoMatch.running || unmapped === 0}
        title={unmapped === 0
          ? 'Every field on this page is already mapped'
          : `Find the source column for the ${unmapped} unmapped field${unmapped === 1 ? '' : 's'}`}
      >
        {autoMatch.running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
        Auto-match columns
      </Button>
      <div className="flex items-center gap-2">
        <AiFallbackBadge autoMatch={autoMatch} />
        <label
          className={`flex items-center gap-1 text-xs select-none ${autoMatch.llmReady ? 'text-foreground cursor-pointer' : 'text-muted-foreground cursor-not-allowed'}`}
          title={autoMatch.llmReady ? 'Ask the AI model about fields the name and value checks cannot settle' : 'The AI fallback is not available'}
        >
          <input
            type="checkbox"
            checked={autoMatch.useLlm && autoMatch.llmReady}
            disabled={!autoMatch.llmReady}
            onChange={e => autoMatch.setUseLlm(e.target.checked)}
            className="w-3.5 h-3.5 accent-primary"
          />
          Use AI
        </label>
      </div>
    </div>
  )
}

/** What the last auto-match run did. */
export function ColumnAutoMatchSummary({ autoMatch }: { autoMatch: ColumnAutoMatch }) {
  const { summary, error } = autoMatch
  if (!summary && !error) return null
  if (error) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>
    )
  }
  const s = summary!
  const aiFilled = s.filled.filter(f => f.ai).length
  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm text-foreground">
      <p>
        Auto-match: {s.filled.length} field{s.filled.length === 1 ? '' : 's'} filled
        {s.suggested > 0 && `, ${s.suggested} suggested`}
        {s.missing > 0 && `, ${s.missing} not found`}.
        {s.aiUsed && ` AI fallback used${aiFilled ? ` — ${aiFilled} filled by AI` : ''}.`}
      </p>
      {s.filled.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
          {s.filled.map(f => (
            <li key={f.label}>
              {f.label} ← <span className="font-mono text-foreground">{f.column}</span>
              {f.ai && <span className="ml-1 text-primary">(AI)</span>}
              {f.dateFormat && <span className="ml-1">· date format set to <code className="text-foreground">{f.dateFormat}</code></span>}
            </li>
          ))}
        </ul>
      )}
      {s.filled.filter(f => f.warning).map(f => (
        <p key={f.label} className="mt-1 text-xs text-amber-700 dark:text-amber-400">{f.label}: {f.warning}</p>
      ))}
      {s.aiError && (
        <p className="mt-1 text-xs text-destructive">AI fallback unavailable: {s.aiError} — name and value checks only.</p>
      )}
      <p className="mt-1 text-xs text-muted-foreground">
        Fields already mapped were left as they were. Check the filled columns below; suggestions are
        shown under their field.
      </p>
    </div>
  )
}

/** A suggested column under a field, with a button to take it. */
export function ColumnSuggestion({ suggestion, onUse, showFile }: {
  suggestion: ColumnMatch
  onUse: (column: string) => void
  /** Name the file too, where a field can come from any of several. */
  showFile?: boolean
}) {
  if (!suggestion.column) return null
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" title={suggestion.reason}>
        <Wand2 className="size-3 text-primary" />
        Suggested:
        <span className="font-mono text-foreground">{suggestion.column}</span>
        {showFile && suggestion.filename && <span>in {suggestion.filename}</span>}
        <span>· {Math.round(suggestion.score * 100)}%</span>
        {suggestion.source === 'llm' && <Badge variant="default" className="px-1.5 py-0">AI</Badge>}
        <button
          type="button"
          onClick={() => onUse(suggestion.column!)}
          className="rounded px-1.5 py-0.5 font-medium text-primary hover:bg-primary/10"
        >
          Use
        </button>
        {suggestion.date_format && (
          <span>· sets the date format to <code className="text-foreground">{suggestion.date_format}</code></span>
        )}
      </div>
      {suggestion.warning && (
        <p className="text-xs text-amber-700 dark:text-amber-400">{suggestion.warning}</p>
      )}
    </div>
  )
}
