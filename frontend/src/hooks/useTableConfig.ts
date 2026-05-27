import { useEffect, useRef, useState } from 'react'
import { getTableConfig, updateTableConfig } from '../api/client'
import type { Project } from '../types'

interface UseTableConfigResult<T> {
  cfg: T
  setCfg: React.Dispatch<React.SetStateAction<T>>
  loaded: boolean
  loadError: string
  saving: boolean
  saveError: string
  save: (extra?: { extra_instructions?: string }) => Promise<Project | null>
}

/**
 * Shared loader/saver for the table-level wizard config endpoints.
 *
 * - Loads /projects/{id}/config/{table} on mount, merging into `defaults`.
 * - Surfaces load/save errors via `loadError` / `saveError` (no silent .catch).
 * - `save()` PATCHes /projects/{id}/config and returns the updated Project.
 */
export function useTableConfig<T extends Record<string, unknown>>(
  projectId: string,
  table: string,
  defaults: T,
  onProjectUpdate?: (p: Project) => void,
): UseTableConfigResult<T> {
  const defaultsRef = useRef(defaults)
  const [cfg, setCfg] = useState<T>(defaults)
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [extraInstructions, setExtraInstructions] = useState<string>('')
  void extraInstructions

  useEffect(() => {
    let cancelled = false
    setLoaded(false)
    setLoadError('')
    getTableConfig(projectId, table)
      .then((ex: Partial<T> & { extra_instructions?: string }) => {
        if (cancelled) return
        if (ex && Object.keys(ex).length > 0) {
          setCfg({ ...defaultsRef.current, ...ex })
          if (typeof ex.extra_instructions === 'string') setExtraInstructions(ex.extra_instructions)
        }
        setLoaded(true)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        const detail = readDetail(e)
        setLoadError(detail || 'Failed to load saved configuration')
        setLoaded(true)
      })
    return () => { cancelled = true }
  }, [projectId, table])

  const save = async (extra?: { extra_instructions?: string }): Promise<Project | null> => {
    setSaving(true)
    setSaveError('')
    try {
      const payload = extra ? { ...cfg, ...extra } : cfg
      const updated = await updateTableConfig(projectId, table, payload)
      onProjectUpdate?.(updated as Project)
      return updated as Project
    } catch (e: unknown) {
      setSaveError(readDetail(e) || 'Failed to save configuration')
      return null
    } finally {
      setSaving(false)
    }
  }

  return { cfg, setCfg, loaded, loadError, saving, saveError, save }
}

function readDetail(e: unknown): string {
  if (typeof e === 'object' && e !== null) {
    const anyE = e as { response?: { data?: { detail?: unknown } }; message?: string }
    const detail = anyE.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail)) return detail.map(d => (d as { msg?: string })?.msg ?? String(d)).join('; ')
    if (typeof anyE.message === 'string') return anyE.message
  }
  return ''
}
