import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getConceptMatcherHealth, type ConceptMatcherHealth } from '../api/client'
import { ConceptMatcherContext, type ConceptMatcherApi } from './conceptMatcher'

/** Re-check often while the matcher isn't usable, so a fix shows up by itself… */
const POLL_NOT_READY_MS = 30_000
/** …and rarely once it is, just to notice it going away. */
const POLL_READY_MS = 5 * 60_000

/**
 * Polls the concept matcher's health for the whole wizard, and re-checks as
 * soon as the tab regains focus — the usual moment after running a fix in a
 * terminal.
 */
export function ConceptMatcherProvider({ children }: { children: React.ReactNode }) {
  const [health, setHealth] = useState<ConceptMatcherHealth | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  // True from the start: the first check is begun by the effect below.
  const [checking, setChecking] = useState(true)
  const inFlight = useRef(false)

  /** Start a check unless one is running; false when one already was. */
  const check = useCallback(() => {
    if (inFlight.current) return false
    inFlight.current = true
    getConceptMatcherHealth()
      .catch((e: unknown): ConceptMatcherHealth => ({
        // The endpoint never fails on the matcher's account, so this is the
        // backend itself not answering.
        status: 'unavailable',
        reason: 'The backend could not be reached to check the concept matcher.',
        hint: 'Check that the backend container is running.',
        commands: ['docker compose up -d backend'],
        raw: e instanceof Error ? e.message : String(e),
      }))
      .then(h => {
        inFlight.current = false
        setHealth(h)
        setLastChecked(new Date())
        setChecking(false)
      })
    return true
  }, [])

  const recheck = useCallback(() => { if (check()) setChecking(true) }, [check])

  // The next check, counted from the last answer — a manual or on-focus
  // re-check restarts the wait.
  useEffect(() => {
    if (!health) return
    const t = setTimeout(recheck, health.status === 'ready' ? POLL_READY_MS : POLL_NOT_READY_MS)
    return () => clearTimeout(t)
  }, [health, lastChecked, recheck])

  useEffect(() => {
    check()
    const onFocus = () => { if (document.visibilityState === 'visible') recheck() }
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [check, recheck])

  const value = useMemo<ConceptMatcherApi>(() => ({
    status: health?.status ?? 'checking',
    reason: health?.reason ?? '',
    hint: health?.hint ?? '',
    commands: health?.commands ?? [],
    raw: health?.raw ?? null,
    health, lastChecked, checking, recheck,
  }), [health, lastChecked, checking, recheck])

  return <ConceptMatcherContext.Provider value={value}>{children}</ConceptMatcherContext.Provider>
}
