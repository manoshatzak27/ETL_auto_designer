import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

interface Props {
  log: string
}

export default function LogStream({ log }: Props) {
  const ref = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight
    }
  }, [log])

  if (!log) return null

  return (
    <pre
      ref={ref}
      className="bg-gray-950 text-green-400 rounded-lg p-4 text-xs overflow-auto max-h-[36rem] min-h-[16rem] font-mono leading-relaxed"
    >
      {log}
    </pre>
  )
}

// Outcome of a run: success, error or running.
export function RunStatusBadge({ status }: { status: string }) {
  if (!status) return null
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-medium w-fit',
        status === 'success' && 'bg-success/10 text-success',
        status === 'error' && 'bg-destructive/10 text-destructive',
        status === 'running' && 'bg-primary/10 text-primary',
      )}
    >
      <span
        className={cn(
          'w-2 h-2 rounded-full',
          status === 'success' && 'bg-success',
          status === 'error' && 'bg-destructive',
          status === 'running' && 'bg-primary animate-pulse',
        )}
      />
      {status === 'success' ? 'Completed successfully' : status === 'error' ? 'Execution failed' : 'Running…'}
    </div>
  )
}
