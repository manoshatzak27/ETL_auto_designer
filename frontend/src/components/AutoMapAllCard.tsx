import { CheckCircle2, AlertTriangle, Wand2, X } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useAutoRun } from '../contexts/autoRun'
import { getActiveSteps, type WizardSlug } from '../wizard/steps'
import type { Project } from '../types'

/** The table steps that have "Auto-match columns" / "Auto-fill concept IDs". */
const AUTO_STEPS = new Set<WizardSlug>(['location', 'care-site', 'provider', 'person', 'visit', 'obs-period', 'death'])

/** "Auto-map all steps" on the Source step, and the report of the last run. */
export default function AutoMapAllCard({ project, disabled }: { project: Project; disabled: boolean }) {
  const { state, start, dismiss } = useAutoRun()
  const steps = getActiveSteps(project).filter(s => AUTO_STEPS.has(s.slug))

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <Wand2 className="size-5 text-primary flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-semibold text-foreground">Auto-map all steps</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Goes through {steps.map(s => s.label).join(', ')} and, on each, presses{' '}
              <span className="font-semibold">Auto-match columns</span> and{' '}
              <span className="font-semibold">Auto-fill concept IDs</span> for every selected file, then saves.
              Fields already mapped are left as they are; review each step afterwards.
            </p>
          </div>
        </div>
        <Button
          onClick={() => start(steps.map(s => ({ slug: s.slug, label: s.label })))}
          disabled={disabled || state.running || steps.length === 0}
          title={disabled ? 'Upload a source file first' : undefined}
        >
          <Wand2 className="w-4 h-4" />
          Auto-map all steps
        </Button>
      </div>

      {!state.running && state.reports.length > 0 && (
        <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <p className="font-medium text-foreground">
              {state.stopped
                ? `Stopped after ${state.reports.length} of ${state.queue.length} steps.`
                : `Auto-mapped ${state.reports.length} step${state.reports.length === 1 ? '' : 's'}.`}
            </p>
            <button type="button" onClick={dismiss} className="text-muted-foreground hover:text-foreground" title="Dismiss">
              <X className="size-4" />
            </button>
          </div>
          <ul className="mt-2 flex flex-col gap-2">
            {state.reports.map(r => (
              <li key={r.slug}>
                <p className="flex items-center gap-1.5 font-medium text-foreground">
                  {r.error
                    ? <AlertTriangle className="size-3.5 text-destructive" />
                    : <CheckCircle2 className="size-3.5 text-success" />}
                  {r.label}
                </p>
                <ul className="ml-5 text-xs text-muted-foreground">
                  {r.lines.map((line, i) => <li key={i}>{line}</li>)}
                  {r.error && <li className="text-destructive">{r.error}</li>}
                </ul>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            Matches that were only suggested were not applied, and the suggestions are not kept — open
            the step and press Auto-match columns again to see them under their fields.
          </p>
        </div>
      )}
    </Card>
  )
}
