import { createContext, useContext, type ReactNode } from 'react'

/**
 * Ask the user to confirm an action that can't be undone.
 *
 *   const confirm = useConfirm()
 *   if (!(await confirm({ title: 'Delete file?', description: '…', confirmLabel: 'Delete' }))) return
 *
 * Resolves true when the user confirms, false on Cancel, Escape or a click
 * outside. The dialog itself is ConfirmProvider in components/ConfirmDialog.
 */
export interface ConfirmOptions {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** 'destructive' (the default) paints the confirm button red. */
  tone?: 'destructive' | 'default'
}

export type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

// Outside a provider (a component rendered on its own) fall back to the
// browser's dialog rather than skipping the question.
export const ConfirmContext = createContext<ConfirmFn>(
  async ({ title, description }) =>
    window.confirm(typeof description === 'string' ? `${title}\n\n${description}` : title),
)

export const useConfirm = () => useContext(ConfirmContext)
