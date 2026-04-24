import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { ChevronRight, ArrowLeft } from 'lucide-react'

const STEPS = [
  { label: 'Source', short: '1' },
  { label: 'Concepts', short: '2' },
  { label: 'Person', short: '3' },
  { label: 'Visit', short: '4' },
  { label: 'Obs. Period', short: '5' },
  { label: 'Stem Table', short: '6' },
  { label: 'Death', short: '7' },
  { label: 'Generate', short: '8' },
]

interface Props {
  projectId?: string
  projectName: string
  currentStep: number
  children: ReactNode
  onNext?: () => void
  onBack?: () => void
  nextLabel?: string
  nextDisabled?: boolean
  saving?: boolean
}

export default function WizardLayout({
  projectName,
  currentStep,
  children,
  onNext,
  onBack,
  nextLabel = 'Next',
  nextDisabled,
  saving,
}: Props) {
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-4">
        <button onClick={() => navigate('/')} className="text-gray-400 hover:text-gray-600">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-lg font-semibold text-gray-900">{projectName}</h1>
          <p className="text-xs text-gray-500">OMOP ETL Designer</p>
        </div>
      </header>

      {/* Step progress */}
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <div className="flex items-center gap-1 overflow-x-auto">
          {STEPS.map((step, i) => {
            const idx = i + 1
            const done = idx < currentStep
            const active = idx === currentStep
            return (
              <div key={step.label} className="flex items-center gap-1 flex-shrink-0">
                <div
                  className={clsx(
                    'flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium transition-colors',
                    active && 'bg-blue-600 text-white',
                    done && 'bg-green-100 text-green-700',
                    !active && !done && 'text-gray-400',
                  )}
                >
                  <span
                    className={clsx(
                      'w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold',
                      active && 'bg-white text-blue-600',
                      done && 'bg-green-500 text-white',
                      !active && !done && 'bg-gray-200 text-gray-500',
                    )}
                  >
                    {done ? '✓' : step.short}
                  </span>
                  <span className="hidden sm:inline">{step.label}</span>
                </div>
                {i < STEPS.length - 1 && <ChevronRight className="w-4 h-4 text-gray-300 flex-shrink-0" />}
              </div>
            )
          })}
        </div>
      </div>

      {/* Content */}
      <main className="flex-1 px-6 py-8 max-w-4xl mx-auto w-full">
        {children}
      </main>

      {/* Footer navigation */}
      <footer className="bg-white border-t border-gray-200 px-6 py-4 flex justify-between items-center">
        <button
          onClick={onBack}
          disabled={!onBack}
          className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Back
        </button>
        {onNext && (
          <button
            onClick={onNext}
            disabled={nextDisabled || saving}
            className="px-6 py-2 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {saving && <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
            {nextLabel}
          </button>
        )}
      </footer>
    </div>
  )
}
