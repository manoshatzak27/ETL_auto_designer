import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { WizardSlug } from '../wizard/steps'
import { AutoRunContext, IDLE, type AutoRunApi, type AutoRunState, type AutoRunStepReport } from './autoRun'

/**
 * "Auto-map all steps" from the Source step: visits each table step in turn,
 * where the step itself presses "Auto-match columns" and "Auto-fill concept
 * IDs" (see useAutoRunStep), saves, and hands back here to go to the next.
 */
export function AutoRunProvider({ projectId, children }: { projectId: string; children: React.ReactNode }) {
  const navigate = useNavigate()
  const [state, setState] = useState<AutoRunState>(IDLE)
  // Read by finishStep, which must navigate — not something to do inside a
  // state updater.
  const stateRef = useRef(state)
  useEffect(() => { stateRef.current = state }, [state])

  const go = useCallback((slug: WizardSlug) => navigate(`/project/${projectId}/step/${slug}`), [navigate, projectId])

  const start = useCallback((steps: { slug: WizardSlug; label: string }[]) => {
    if (steps.length === 0) return
    setState({ running: true, queue: steps, index: 0, reports: [], stopped: false })
    go(steps[0].slug)
  }, [go])

  const finishStep = useCallback((report: AutoRunStepReport) => {
    const prev = stateRef.current
    if (!prev.running || prev.queue[prev.index]?.slug !== report.slug) return
    const reports = [...prev.reports, report]
    const index = prev.index + 1
    const done = index >= prev.queue.length
    const next = done ? { ...prev, running: false, index, reports } : { ...prev, index, reports }
    stateRef.current = next
    setState(next)
    go(done ? 'source' : prev.queue[index].slug)
  }, [go])

  const stop = useCallback(() => {
    setState(prev => (prev.running ? { ...prev, running: false, stopped: true } : prev))
    go('source')
  }, [go])

  const dismiss = useCallback(() => setState(IDLE), [])

  const value = useMemo<AutoRunApi>(() => ({
    state,
    current: state.running ? state.queue[state.index]?.slug ?? null : null,
    start, finishStep, stop, dismiss,
  }), [state, start, finishStep, stop, dismiss])

  return <AutoRunContext.Provider value={value}>{children}</AutoRunContext.Provider>
}
