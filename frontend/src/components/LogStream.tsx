import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

interface Props {
  log: string
  status: string
}

export default function LogStream({ log, status }: Props) {
  const ref = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight
    }
  }, [log])

  if (!log && !status) return null

  return (
    <div className="flex flex-col gap-2">
      {status && (
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
      )}
      {log && (
        <pre
          ref={ref}
          className="bg-gray-950 text-green-400 rounded-lg p-4 text-xs overflow-auto max-h-64 font-mono leading-relaxed"
        >
          {log}
        </pre>
      )}
    </div>
  )
}
