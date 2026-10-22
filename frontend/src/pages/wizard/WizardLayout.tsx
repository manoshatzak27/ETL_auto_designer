import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { AlertCircle, ArrowLeft, Check, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import ConceptMatcherIndicator from '../../components/ConceptMatcherIndicator'
import type { StepAutosave } from '../../hooks/useStepAutosave'
import type { Project } from '../../types'
import { getActiveSteps, type WizardSlug } from '../../wizard/steps'

interface Props {
  project: Project
  currentSlug: WizardSlug
  children: ReactNode
  onNext?: () => void
  onBack?: () => void
  /** Runs before leaving the step; defaults to `autosave.flush`. A rejection keeps the user on the step. */
  onBeforeStepChange?: () => Promise<void>
  /** The step's autosave, shown in the header. */
  autosave?: StepAutosave
  nextLabel?: string
  nextDisabled?: boolean
  saving?: boolean
  /** Overrides the content column's max-width class (default max-w-4xl). */
  contentWidthClassName?: string
}

export default function WizardLayout({
  project,
  currentSlug,
  children,
  onNext,
  onBack,
  onBeforeStepChange,
  autosave,
  nextLabel = 'Next',
  nextDisabled,
  saving,
  contentWidthClassName = 'max-w-4xl',
}: Props) {
  const navigate = useNavigate()
  const activeSteps = getActiveSteps(project)

  const beforeLeave = onBeforeStepChange ?? autosave?.flush
  // A failed save keeps the user here; the step or the header shows the error.
  const leave = async (go: () => void) => {
    try {
      await beforeLeave?.()
    } catch {
      return
    }
    go()
  }

  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-background via-background to-secondary/60">
      {/* Header */}
      <header className="flex items-center gap-3 border-b border-border bg-card/60 px-6 py-4">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Back to projects"
          onClick={() => leave(() => navigate('/'))}
          className="text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft />
        </Button>
        <div>
          <h1 className="text-lg font-bold tracking-tight text-primary">
            {project.name}
          </h1>
          <p className="text-xs text-muted-foreground">
            <span className="font-semibold text-primary">OMOP</span> ETL Designer
          </p>
        </div>
        <div className="ml-auto flex items-center gap-4">
          {autosave && <AutosaveStatus autosave={autosave} />}
          <ConceptMatcherIndicator />
        </div>
      </header>

      {/* Step progress */}
      <div className="px-6 pt-5">
        <div className="flex items-stretch gap-2 pb-3">
          {activeSteps.map(step => {
            const active = step.slug === currentSlug
            const done = !active && step.isComplete(project)
            return (
              <button
                key={step.slug}
                onClick={() => leave(() => navigate(`/project/${project.id}/step/${step.slug}`))}
                className={clsx(
                  'flex flex-1 min-w-0 items-center justify-center gap-2 rounded-xl border px-2 py-2 text-sm font-medium shadow-sm transition-all',
                  active && 'border-primary bg-primary text-primary-foreground shadow-md',
                  !active && done && 'border-border bg-card text-secondary-foreground hover:-translate-y-0.5 hover:shadow-md',
                  !active && !done && 'border-border bg-card text-muted-foreground hover:-translate-y-0.5 hover:shadow-md',
                )}
              >
                <span
                  className={clsx(
                    'flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold',
                    active && 'bg-card text-primary',
                    !active && done && 'bg-primary text-primary-foreground',
                    !active && !done && 'bg-muted text-muted-foreground',
                  )}
                >
                  {done ? '✓' : step.short}
                </span>
                <span className="hidden sm:inline">{step.label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Content */}
      <main className={clsx('mx-auto w-full flex-1 px-6 py-8', contentWidthClassName)}>
        {children}
      </main>

      {/* Footer navigation */}
      <footer className="flex items-center justify-between border-t border-border bg-card/60 px-6 py-4">
        <Button
          variant="outline"
          onClick={() => leave(() => onBack?.())}
          disabled={!onBack}
        >
          Back
        </Button>
        {onNext && (
          <Button onClick={onNext} disabled={nextDisabled || saving}>
            {saving && <Loader2 className="animate-spin" />}
            {nextLabel}
          </Button>
        )}
      </footer>
    </div>
  )
}

function AutosaveStatus({ autosave }: { autosave: StepAutosave }) {
  const { state, error, flush } = autosave
  if (state === 'idle') return null
  if (state === 'error') {
    return (
      <span role="alert" className="flex items-center gap-1.5 text-xs text-destructive" title={error}>
        <AlertCircle className="h-3.5 w-3.5" />
        Save failed
        <button type="button" onClick={() => flush().catch(() => {})} className="font-medium underline underline-offset-2">
          Retry
        </button>
      </span>
    )
  }
  return (
    <span aria-live="polite" className="flex items-center gap-1.5 text-xs text-muted-foreground">
      {state === 'saving'
        ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />Saving…</>
        : <><Check className="h-3.5 w-3.5" />All changes saved</>}
    </span>
  )
}
