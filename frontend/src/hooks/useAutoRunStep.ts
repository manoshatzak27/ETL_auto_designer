import { useEffect, useRef, useState } from 'react'
import { useAutoRun } from '../contexts/autoRun'
import type { WizardSlug } from '../wizard/steps'
import type { ColumnAutoMatch, ColumnAutoMatchSummary, ColumnTarget } from './useColumnAutoMatch'
import type { AutoFillTarget, ConceptAutoFill } from './useConceptAutoFill'

/** Give up on a step whose data never finishes loading. */
const READY_TIMEOUT_MS = 30_000

export interface AutoRunStepOptions {
  slug: WizardSlug
  label: string
  /** The step has loaded its config and the active file's column values. */
  ready: boolean
  /** The files to go through, in order; empty for a step with one view. */
  files: string[]
  activeFile: string
  switchFile?: (filename: string) => void
  autoMatch: ColumnAutoMatch
  match: { filenames: string[]; targets: ColumnTarget[]; exclude: Iterable<string> }
  autoFill?: ConceptAutoFill
  fillTargets?: AutoFillTarget[]
  save: () => Promise<void>
}

type Phase = 'idle' | 'match' | 'fill' | 'save'

function describeMatch(s: ColumnAutoMatchSummary | null): string {
  if (!s) return 'columns: every field was already mapped'
  const parts = [`${s.filled.length} filled`]
  if (s.suggested) parts.push(`${s.suggested} suggested`)
  if (s.missing) parts.push(`${s.missing} not found`)
  const ai = s.aiUsed ? ' (AI used)' : s.aiError ? ` (AI unavailable: ${s.aiError})` : ''
  const formats = s.filled.filter(f => f.dateFormat).map(f => `${f.label} → ${f.dateFormat}`)
  return `columns: ${parts.join(', ')}${ai}` + (formats.length ? ` · date format set: ${formats.join(', ')}` : '')
}

/**
 * The step's part of "Auto-map all steps": when auto-run reaches this step,
 * press "Auto-match columns", then "Auto-fill concept IDs", then save — for
 * each of the step's files — and hand over to the next step.
 *
 * Each phase starts on a fresh render, so it sees what the previous one did:
 * the concept targets exist only once their columns are mapped, and the save
 * must include the filled value maps. The phase only ever changes when an
 * async action finishes.
 */
export function useAutoRunStep(o: AutoRunStepOptions) {
  const { current, finishStep } = useAutoRun()
  const active = current === o.slug
  // 'match' — match the current file once it is ready; 'fill' — fill its
  // concepts; 'save' — save it, then move to the next file or step.
  const [phase, setPhase] = useState<Phase>('idle')
  const [, setTick] = useState(0)
  const busy = useRef(false)
  const plan = useRef({ files: [] as string[], pos: 0, lines: [] as string[], waitingSince: 0 })

  // While waiting for data, re-render every second to check the timeout.
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [active])

  useEffect(() => {
    if (!active || busy.current) return
    const p = plan.current
    const prefix = () => (p.files.length > 1 ? `${p.files[p.pos]}: ` : '')

    // Every action below is async; `busy` keeps renders in between from
    // starting it twice, and its last callback picks the next phase.
    const act = (action: () => Promise<Phase | 'done'>, onError: (e: Error) => Phase | 'done' = () => 'done') => {
      busy.current = true
      action()
        .catch(onError)
        .then(next => {
          busy.current = false
          if (next === 'done') {
            setPhase('idle')
            finishStep({ slug: o.slug, label: o.label, lines: p.lines })
          } else {
            setPhase(next)
          }
        })
    }
    const fail = (error: string) => {
      busy.current = true
      Promise.resolve().then(() => {
        busy.current = false
        setPhase('idle')
        finishStep({ slug: o.slug, label: o.label, lines: p.lines, error })
      })
    }
    const ready = (ok: boolean): boolean => {
      if (ok) { p.waitingSince = 0; return true }
      if (!p.waitingSince) p.waitingSince = Date.now()
      else if (Date.now() - p.waitingSince > READY_TIMEOUT_MS) fail('the step did not finish loading')
      return false
    }
    const match = () => act(async () => {
      try {
        p.lines.push(prefix() + describeMatch(await o.autoMatch.run(o.match)))
      } catch (e) {
        p.lines.push(`${prefix()}columns: ${(e as Error).message}`)
      }
      return 'fill'
    })
    const save = () => act(async () => {
      await o.save()
      if (p.pos + 1 >= p.files.length) return 'done'
      p.pos += 1
      o.switchFile?.(p.files[p.pos])
      return 'match'
    }, e => { p.lines.push(`could not save: ${e.message}`); return 'done' })

    if (phase === 'idle') {
      if (!ready(o.ready && o.autoMatch.llm !== null)) return
      const files = o.files.length > 0
        ? [o.activeFile, ...o.files.filter(f => f !== o.activeFile)].filter(Boolean)
        : [o.activeFile]
      // In place: the callbacks of this pass hold `p`.
      Object.assign(p, { files: files.length ? files : [o.activeFile], pos: 0, lines: [], waitingSince: 0 })
      match()
    } else if (phase === 'match') {
      if (ready(o.ready && o.activeFile === p.files[p.pos])) match()
    } else if (phase === 'fill') {
      const targets = o.fillTargets ?? []
      if (!o.autoFill || targets.length === 0) { save(); return }
      if (!ready(o.autoFill.health !== null)) return
      if (!o.autoFill.health?.available) {
        p.lines.push(`${prefix()}concepts: skipped — ${o.autoFill.health?.detail || 'concept matcher unavailable'}`)
        save()
        return
      }
      const autoFill = o.autoFill
      act(async () => {
        try {
          p.lines.push(`${prefix()}concepts: ${(await autoFill.run(targets)).join('; ')}`)
        } catch (e) {
          p.lines.push(`${prefix()}concepts: ${(e as Error).message}`)
        }
        return 'save'
      })
    } else {
      save()
    }
  }, [active, phase, o, finishStep])
}
