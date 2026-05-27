import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

const STEPS = [
  { label: 'Source',     short: '1',  scriptKey: null as string | null },
  { label: 'Location',   short: '2',  scriptKey: 'location' },
  { label: 'Care Site',  short: '3',  scriptKey: 'care_site' },
  { label: 'Provider',   short: '4',  scriptKey: 'provider' },
  { label: 'Person',     short: '5',  scriptKey: 'person' },
  { label: 'Visit',      short: '6',  scriptKey: 'visit_occurrence' },
  { label: 'Obs. Period',short: '7',  scriptKey: 'observation_period' },
  { label: 'Death',      short: '8',  scriptKey: 'death' },
  { label: 'Concepts',   short: '9',  scriptKey: null as string | null },
  { label: 'Stem Table', short: '10', scriptKey: 'stem_table' },
  { label: 'Generate',   short: '11', scriptKey: null as string | null },
  { label: 'Load DB',    short: '12', scriptKey: null as string | null },
]

interface Props {
  projectId?: string
  projectName: string
  currentStep: number
  generatedScripts?: Record<string, string>
  sourceUploaded?: boolean
  hasMappingFiles?: boolean
  children: ReactNode
  onNext?: () => void
  onBack?: () => void
  nextLabel?: string
  nextDisabled?: boolean
  saving?: boolean
}

export default function WizardLayout({
  projectId,
  projectName,
  currentStep,
  generatedScripts,
  sourceUploaded,
  hasMappingFiles,
  children,
  onNext,
  onBack,
  nextLabel = 'Next',
  nextDisabled,
  saving,
}: Props) {
  const navigate = useNavigate()

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
            {projectName}
          </h1>
          <p className="text-xs text-muted-foreground">
            <span className="font-semibold text-primary">OMOP</span> ETL Designer
          </p>
        </div>
      </header>

      {/* Step progress */}
      <div className="px-6 pt-5">
        <div className="flex items-stretch gap-2 pb-3">
          {STEPS.map((step, i) => {
            const idx = i + 1
            const active = idx === currentStep
            const done = (() => {
              if (idx === 1) return !!sourceUploaded
              if (idx === 9) return !!hasMappingFiles
              if (step.scriptKey) return !!(generatedScripts?.[step.scriptKey])
              return false
            })()
            return (
              <button
                key={step.label}
                onClick={() => projectId && navigate(`/project/${projectId}/step/${idx}`)}
                disabled={!projectId}
                className={clsx(
                  'flex flex-1 min-w-0 items-center justify-center gap-2 rounded-xl border px-2 py-2 text-sm font-medium shadow-sm transition-all',
                  active && 'border-primary bg-primary text-primary-foreground shadow-md',
                  !active && done && 'border-border bg-card text-secondary-foreground hover:-translate-y-0.5 hover:shadow-md',
                  !active && !done && 'border-border bg-card text-muted-foreground hover:-translate-y-0.5 hover:shadow-md',
                  !projectId && 'cursor-not-allowed',
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
                  {!active && done ? '✓' : step.short}
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
