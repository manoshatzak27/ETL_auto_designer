import type { Project } from '../types'
import type { ConfirmOptions } from '../contexts/confirm'

/** Generating stem_table also rebuilds these, which read stem_table.csv. */
export const STEM_DOMAIN_TABLES = ['measurement', 'observation', 'drug_exposure', 'procedure_occurrence', 'condition_occurrence']

/** The scripts generating `tables` rebuilds that hold a change applied from the chat. */
export function chatEditsOverwritten(project: Project, tables: string[]): string[] {
  const edited = new Set(project.chat_edited_tables ?? [])
  const rebuilt = tables.flatMap(t => (t === 'stem_table' ? [t, ...STEM_DOMAIN_TABLES] : [t]))
  return rebuilt.filter(t => edited.has(t))
}

/** The warning shown before regenerating scripts that hold chat changes. */
export function chatEditsWarning(tables: string[]): ConfirmOptions {
  const files = tables.map(t => `${t}.py`).join(', ')
  return {
    title: tables.length > 1 ? 'Overwrite changes made in the chat?' : `Overwrite ${files}'s chat changes?`,
    description: (
      <>
        <p>
          <span className="font-mono text-foreground">{files}</span> {tables.length > 1 ? 'hold' : 'holds'} changes
          applied from the AI chat. Regenerating builds the script again from the wizard settings, and those
          changes are lost.
        </p>
        <p>To keep a change, make it in the wizard settings or ask the chat for it again afterwards.</p>
      </>
    ),
    confirmLabel: 'Regenerate',
  }
}
