import { useState } from 'react'
import { Check, ChevronRight, Copy, Loader2, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { useConceptMatcherStatus, type ConceptMatcherApi } from '../contexts/conceptMatcher'

const LOOK: Record<ConceptMatcherApi['status'], { variant: BadgeProps['variant']; dot: string; label: string }> = {
  ready:          { variant: 'success',     dot: 'bg-success',          label: 'ready' },
  starting:       { variant: 'warning',     dot: 'bg-amber-500',        label: 'starting' },
  unavailable:    { variant: 'destructive', dot: 'bg-destructive',      label: 'unavailable' },
  not_configured: { variant: 'destructive', dot: 'bg-destructive',      label: 'not configured' },
  checking:       { variant: 'muted',       dot: 'bg-muted-foreground', label: 'checking…' },
}

/** A shell command with a copy button. */
function CopyableCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard blocked (insecure origin); the text can still be selected */ }
  }
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-muted px-2 py-1">
      <code className="flex-1 select-all font-mono text-xs text-foreground">{command}</code>
      <button
        type="button"
        onClick={copy}
        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
        title="Copy"
      >
        {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  )
}

/** The hint and its copyable commands — also used by the Concepts step's notice. */
export function ConceptMatcherFix({ hint, commands }: { hint: string; commands: string[] }) {
  return (
    <div className="space-y-1.5">
      {hint && <p>{hint}</p>}
      {commands.length > 0 && (
        <>
          <p className="text-xs text-muted-foreground">From the ETL designer's directory:</p>
          {commands.map(c => <CopyableCommand key={c} command={c} />)}
        </>
      )}
    </div>
  )
}

/** "Concept matcher: ready / starting / unavailable" in the wizard header;
 *  clicking it explains the status and how to fix it. */
export default function ConceptMatcherIndicator() {
  const matcher = useConceptMatcherStatus()
  const { status, health, checking } = matcher
  const [showRaw, setShowRaw] = useState(false)
  const look = LOOK[status]

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" className="rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Badge variant={look.variant} className="cursor-pointer gap-1.5">
            <span className={clsx('size-2 rounded-full', look.dot, status === 'starting' && 'animate-pulse')} />
            Concept matcher: {look.label}
          </Badge>
        </button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Concept matcher
            <Badge variant={look.variant}>{look.label}</Badge>
          </DialogTitle>
          <DialogDescription>
            Powers "Load concepts", "Auto-fill concept IDs" and concept search.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {matcher.reason && <p className="font-medium text-foreground">{matcher.reason}</p>}

          {status === 'ready' && health && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">Vocabulary</dt>
              <dd>{health.vocabulary_version ?? 'unknown'}</dd>
              <dt className="text-muted-foreground">Concepts</dt>
              <dd>{health.concepts != null ? health.concepts.toLocaleString() : 'unknown'}</dd>
            </dl>
          )}

          {status !== 'ready' && <ConceptMatcherFix hint={matcher.hint} commands={matcher.commands} />}

          {matcher.raw && (
            <div>
              <button
                type="button"
                onClick={() => setShowRaw(v => !v)}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ChevronRight className={clsx('size-3 transition-transform', showRaw && 'rotate-90')} />
                Details
              </button>
              {showRaw && (
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-2 font-mono text-xs text-muted-foreground">
                  {matcher.raw}
                </pre>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
          <span className="text-xs text-muted-foreground">
            {matcher.lastChecked
              ? `Last checked ${matcher.lastChecked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
              : 'Not checked yet'}
          </span>
          <Button variant="outline" size="sm" onClick={matcher.recheck} disabled={checking}>
            {checking ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            Re-check
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
