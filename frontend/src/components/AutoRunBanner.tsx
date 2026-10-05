import { Loader2, Square } from 'lucide-react'
import { useAutoRun } from '../contexts/autoRun'

/** Progress of "Auto-map all steps", shown on every step while it runs. */
export default function AutoRunBanner() {
  const { state, stop } = useAutoRun()
  if (!state.running) return null
  const step = state.queue[state.index]
  return (
    <div className="fixed top-3 left-1/2 z-50 -translate-x-1/2 flex items-center gap-3 rounded-full border border-primary/40 bg-card px-4 py-2 text-sm shadow-lg">
      <Loader2 className="size-4 animate-spin text-primary" />
      <span>
        Auto-mapping <span className="font-semibold text-foreground">{step?.label}</span>
        <span className="text-muted-foreground"> ({state.index + 1} of {state.queue.length})</span>
      </span>
      <button
        type="button"
        onClick={stop}
        className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium text-destructive hover:bg-destructive/10"
      >
        <Square className="size-3" /> Stop
      </button>
    </div>
  )
}
