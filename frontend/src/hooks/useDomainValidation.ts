import { useState, useEffect, useRef, useMemo } from 'react'
import { lookupConceptDomain } from '../api/client'

export function useDomainValidation(
  conceptIds: number[],
  expectedDomain: string,
): Map<number, string | null> {
  const [violations, setViolations] = useState<Map<number, string | null>>(new Map())
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  const idsKey = useMemo(
    () => [...new Set(conceptIds.filter(id => id > 0))].sort((a, b) => a - b).join(','),
    [conceptIds],
  )

  useEffect(() => {
    const ids = idsKey ? idsKey.split(',').map(Number) : []
    clearTimeout(timerRef.current)
    if (ids.length === 0) { setViolations(new Map()); return }
    timerRef.current = setTimeout(() => {
      Promise.all(
        ids.map(id =>
          lookupConceptDomain(id)
            .then(r => ({ id, found: r.found, domain: r.domain_id }))
            .catch(() => null),
        ),
      ).then(results => {
        const v = new Map<number, string | null>()
        for (const r of results) {
          if (!r) continue
          if (!r.found) {
            v.set(r.id, null)
          } else if (r.domain && r.domain.toLowerCase() !== expectedDomain.toLowerCase()) {
            v.set(r.id, r.domain)
          }
        }
        setViolations(v)
      })
    }, 600)
    return () => clearTimeout(timerRef.current)
  }, [idsKey, expectedDomain])

  return violations
}
