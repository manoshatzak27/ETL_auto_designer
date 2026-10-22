/** The dialog behind useConfirm() (contexts/confirm). */
import { useCallback, useRef, useState, type ReactNode } from 'react'
import {
  Dialog, DialogContent, DialogHeader, DialogFooter, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ConfirmContext, type ConfirmFn, type ConfirmOptions } from '../contexts/confirm'

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const resolveRef = useRef<((ok: boolean) => void) | null>(null)

  const confirm = useCallback<ConfirmFn>(opts => {
    // A second request while one is open answers the first with "no".
    resolveRef.current?.(false)
    setOptions(opts)
    return new Promise<boolean>(resolve => { resolveRef.current = resolve })
  }, [])

  const close = (ok: boolean) => {
    resolveRef.current?.(ok)
    resolveRef.current = null
    setOptions(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={!!options} onOpenChange={open => { if (!open) close(false) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{options?.title}</DialogTitle>
            {options?.description && (
              <DialogDescription asChild>
                <div className="flex flex-col gap-2">{options.description}</div>
              </DialogDescription>
            )}
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => close(false)}>
              {options?.cancelLabel ?? 'Cancel'}
            </Button>
            <Button
              variant={options?.tone === 'default' ? 'default' : 'destructive'}
              onClick={() => close(true)}
            >
              {options?.confirmLabel ?? 'Confirm'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ConfirmContext.Provider>
  )
}
