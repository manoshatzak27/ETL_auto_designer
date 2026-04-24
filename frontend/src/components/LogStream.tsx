import { useEffect, useRef } from 'react'
import clsx from 'clsx'

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
          className={clsx(
            'inline-flex items-center gap-2 px-3 py-1 rounded-full text-sm font-medium w-fit',
            status === 'success' && 'bg-green-100 text-green-800',
            status === 'error' && 'bg-red-100 text-red-800',
            status === 'running' && 'bg-blue-100 text-blue-800',
          )}
        >
          <span
            className={clsx(
              'w-2 h-2 rounded-full',
              status === 'success' && 'bg-green-500',
              status === 'error' && 'bg-red-500',
              status === 'running' && 'bg-blue-500 animate-pulse',
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
