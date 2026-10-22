import { useCallback, useEffect, useRef, useState } from 'react'

const AUTOSAVE_DELAY_MS = 800

export type AutosaveState = 'idle' | 'saving' | 'saved' | 'error'

export interface StepAutosave {
  /** 'saving' also covers a change still waiting for its debounce. */
  state: AutosaveState
  error: string
  /** Save now if anything changed since the last save; rejects if the save fails. */
  flush: () => Promise<void>
}

/**
 * Saves a wizard step's config shortly after every change, so nothing depends
 * on leaving the step through Next, Back or a step chip.
 *
 * `payload` is what the step would save right now. The first payload after
 * `ready` is taken as what was loaded, so opening a step doesn't save it; after
 * that every change to the payload is saved once it has been still for 800 ms.
 * A pending change is saved on unmount, and closing or reloading the tab with
 * one pending asks the user first. Saves run one at a time, each sending the
 * newest payload.
 */
export function useStepAutosave<T>(
  payload: T,
  ready: boolean,
  persist: (payload: T) => Promise<void>,
): StepAutosave {
  const json = ready ? JSON.stringify(payload) : null
  const latest = useRef({ payload, json, persist })
  latest.current = { payload, json, persist }

  // The last payload known to be on the server; null until the step has loaded.
  const savedJson = useRef<string | null>(null)
  const chain = useRef<Promise<void>>(Promise.resolve())
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted = useRef(true)
  const [inFlight, setInFlight] = useState(0)
  const [savedOnce, setSavedOnce] = useState(false)
  const [error, setError] = useState('')

  const save = useCallback((): Promise<void> => {
    const run = chain.current.then(async () => {
      const { payload, json, persist } = latest.current
      if (json === null || savedJson.current === null || json === savedJson.current) return
      if (mounted.current) setInFlight(n => n + 1)
      try {
        await persist(payload)
        savedJson.current = json
        if (mounted.current) { setSavedOnce(true); setError('') }
      } catch (e: unknown) {
        if (mounted.current) setError(readDetail(e) || 'Failed to save changes')
        throw e
      } finally {
        if (mounted.current) setInFlight(n => n - 1)
      }
    })
    chain.current = run.catch(() => {})
    return run
  }, [])

  const flush = useCallback(() => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    return save()
  }, [save])

  // Take the first payload once loaded as the baseline.
  useEffect(() => {
    if (json !== null && savedJson.current === null) savedJson.current = json
  }, [json])

  // Debounced save after each change.
  useEffect(() => {
    if (json === null || savedJson.current === null || json === savedJson.current) return
    const t = setTimeout(() => {
      timer.current = null
      save().catch(() => {})
    }, AUTOSAVE_DELAY_MS)
    timer.current = t
    return () => clearTimeout(t)
  }, [json, save])

  // Save what's pending when the step unmounts (header ←, browser back, …).
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      save().catch(() => {})
    }
  }, [save])

  // Closing or reloading the tab can't wait for a save: start it, and ask.
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      const { json } = latest.current
      if (json === null || savedJson.current === null || json === savedJson.current) return
      save().catch(() => {})
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [save])

  const dirty = json !== null && savedJson.current !== null && json !== savedJson.current
  const state: AutosaveState =
    inFlight > 0 || (dirty && !error) ? 'saving'
      : error ? 'error'
        : savedOnce ? 'saved'
          : 'idle'

  return { state, error, flush }
}

function readDetail(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return (e as { message?: string })?.message ?? ''
}
