/**
 * DateFormatPreview — shows, under a date format input, how that format reads
 * the mapped date columns: how many rows parse, a few values with the date they
 * become, the values that don't parse, and a format that reads more of them.
 *
 * The backend parses with the same strptime call the generated scripts use, so
 * what the preview shows is what a run would do.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import { previewDateFormat, type DateColumnPreview } from '../api/client'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface Props {
  projectId: string
  format: string
  /** The date columns read with `format`; empty column names are ignored. */
  columns: { column: string; filename?: string | null }[]
  /** A date of birth may be a year only. */
  allowYear?: boolean
  /** Switch to a suggested format; without it the suggestion is only shown. */
  onUseFormat?: (format: string) => void
}

const DEBOUNCE_MS = 350

export default function DateFormatPreview({ projectId, format, columns, allowYear, onUseFormat }: Props) {
  const refs = dedupe(columns.filter(c => c.column))
  // A stable key, so a new array with the same columns doesn't refetch.
  const key = JSON.stringify([format, refs, !!allowYear])
  const [result, setResult] = useState<{ key: string; columns: DateColumnPreview[] } | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (refs.length === 0 || !format) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      previewDateFormat(projectId, format, refs, allowYear, ctrl.signal)
        .then(data => { setResult({ key, columns: data.columns }); setError(false) })
        .catch(e => { if (!ctrl.signal.aborted && e?.name !== 'CanceledError') setError(true) })
    }, DEBOUNCE_MS)
    return () => { clearTimeout(timer); ctrl.abort() }
    // `key` covers projectId-independent inputs; refs is rebuilt every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key])

  if (!format) return null
  if (refs.length === 0) {
    return <p className="mt-1.5 text-xs text-muted-foreground">Map a date column to preview how this format reads it.</p>
  }
  if (error && !result) {
    return <p className="mt-1.5 text-xs text-muted-foreground">Couldn't load the preview.</p>
  }
  if (!result) {
    return (
      <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="size-3 animate-spin" /> Checking the date values…
      </p>
    )
  }

  const pending = result.key !== key
  const shown = result.columns.filter(c => !c.missing)
  if (shown.length === 0) return null

  return (
    <div className={cn('mt-2 flex flex-col gap-2 transition-opacity', pending && 'opacity-60')} aria-live="polite">
      {shown.map(c => <ColumnPreview key={`${c.filename}:${c.column}`} preview={c} format={format} onUseFormat={onUseFormat} />)}
    </div>
  )
}

function ColumnPreview({ preview: c, format, onUseFormat }: {
  preview: DateColumnPreview
  format: string
  onUseFormat?: (format: string) => void
}) {
  const rows = c.rows ?? 0
  const parsed = c.parsed_rows ?? 0
  const allRead = !c.format_error && rows > 0 && parsed === rows
  const n = (v: number) => v.toLocaleString()

  return (
    <div className={cn(
      'rounded-md border px-3 py-2 text-xs',
      allRead ? 'border-border bg-muted/40' : 'border-amber-300 bg-amber-50 dark:bg-amber-950/30',
    )}>
      <p className={cn('flex items-center gap-1.5 font-medium', allRead ? 'text-foreground' : 'text-amber-800 dark:text-amber-300')}>
        {allRead
          ? <CheckCircle2 className="size-3.5 shrink-0 text-green-600" />
          : <AlertTriangle className="size-3.5 shrink-0" />}
        <span className="font-mono">{c.column}</span>
        <span className="font-normal text-muted-foreground">·</span>
        {c.format_error
          ? <span>Invalid format: {c.format_error}</span>
          : rows === 0
            ? <span className="font-normal text-muted-foreground">no values in this column</span>
            : <span>{allRead ? `all ${n(rows)} rows read` : `${n(parsed)} of ${n(rows)} rows read`}</span>}
      </p>

      {!!c.examples?.length && (
        <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
          {c.examples.map(ex => (
            <span key={ex.value}>
              <code className="text-foreground">{ex.value}</code> → <code className="text-foreground">{ex.parsed}</code>
            </span>
          ))}
        </p>
      )}

      {!!c.failures?.length && (
        <p className="mt-1 text-amber-800 dark:text-amber-300">
          Can't be read with <code>{format}</code>:{' '}
          {c.failures.map((v, i) => (
            <span key={v}>{i > 0 && ', '}<code className="rounded bg-background/70 px-1">{v}</code></span>
          ))}
          {rows - parsed > c.failures.length && ' …'}
          {' '}These rows get no date from this column.
        </p>
      )}

      {c.suggestion && (
        <p className="mt-1.5 flex flex-wrap items-center gap-2 text-foreground">
          <span>
            <code className="font-semibold">{c.suggestion.format}</code> reads {n(c.suggestion.parsed_rows)} of {n(rows)} rows.
          </span>
          {onUseFormat && (
            <Button type="button" size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={() => onUseFormat(c.suggestion!.format)}>
              Use {c.suggestion.format}
            </Button>
          )}
        </p>
      )}

      {c.ambiguous && (
        <p className="mt-1 text-amber-800 dark:text-amber-300">
          Every value also reads with day and month swapped, so the data can't tell which comes first. Check the examples above are the right dates.
        </p>
      )}
    </div>
  )
}

function dedupe(refs: { column: string; filename?: string | null }[]) {
  const seen = new Set<string>()
  return refs
    .map(r => ({ column: r.column, filename: r.filename || null }))
    .filter(r => {
      const k = `${r.filename}\0${r.column}`
      if (seen.has(k)) return false
      seen.add(k)
      return true
    })
}
