import { Button } from '@/components/ui/button'
import { Loader2, Sparkles } from 'lucide-react'
import type { AutoFillTarget, ConceptAutoFill } from '../hooks/useConceptAutoFill'

/** The "Auto-fill concept IDs" button for a step header. `fields` names the
 *  step's concept fields for the tooltip shown when there is nothing to fill. */
export function ConceptAutoFillButton({ autoFill, targets, fields }: {
  autoFill: ConceptAutoFill
  targets: AutoFillTarget[]
  fields: string
}) {
  const unavailable = autoFill.health?.available === false
  return (
    <Button
      // A failure is shown by ConceptAutoFillSummary.
      onClick={() => { autoFill.run(targets).catch(() => {}) }}
      disabled={autoFill.running || targets.length === 0 || unavailable}
      title={
        unavailable
          ? `Concept matcher unavailable — ${autoFill.health?.detail || 'the service is not running'}`
          : targets.length === 0
            ? `Map a column for ${fields} first`
            : `Fill in standard concept IDs for the ${fields} values`
      }
    >
      {autoFill.running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
      Auto-fill concept IDs
    </Button>
  )
}

/** What the last auto-fill run did, one line per field. */
export function ConceptAutoFillSummary({ autoFill }: { autoFill: ConceptAutoFill }) {
  if (!autoFill.summary && !autoFill.error) return null
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${autoFill.error ? 'border-destructive/50 bg-destructive/10 text-destructive' : 'border-primary/30 bg-primary/5 text-foreground'}`}>
      {autoFill.error ?? (
        <>
          <ul className="space-y-0.5">
            {autoFill.summary!.map(line => <li key={line}>{line}</li>)}
          </ul>
          <p className="mt-1 text-xs text-muted-foreground">
            Values already mapped were left as they were. Check the filled IDs below; use the
            search icon beside any value to pick a different concept.
          </p>
        </>
      )}
    </div>
  )
}
