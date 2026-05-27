import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Project } from '../../types'
import { getActiveSteps, type WizardSlug } from '../../wizard/steps'

interface Props {
  project: Project
  currentSlug: WizardSlug
  children: ReactNode
  onNext?: () => void
  onBack?: () => void
  nextLabel?: string
  nextDisabled?: boolean
  saving?: boolean
}

export default function WizardLayout({
  project,
  currentSlug,
  children,
  onNext,
  onBack,
  nextLabel = 'Next',
  nextDisabled,
  saving,
}: Props) {
  const navigate = useNavigate()
  const activeSteps = getActiveSteps(project)

  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-background via-background to-secondary/60">
      {/* Header */}
      <header className="flex items-center gap-3 border-b border-border bg-card/60 px-6 py-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => navigate('/')}
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
                onClick={() => navigate(`/project/${project.id}/step/${step.slug}`)}
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
      <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-8">
        {children}
      </main>

      {/* Footer navigation */}
      <footer className="flex items-center justify-between border-t border-border bg-card/60 px-6 py-4">
        <Button variant="outline" onClick={onBack} disabled={!onBack}>
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
