import { AlertTriangle } from 'lucide-react'

interface Props {
  violations: Map<number, string | null>
  expectedDomain: string
}

export default function DomainWarning({ violations, expectedDomain }: Props) {
  if (violations.size === 0) return null
  return (
    <div
      className="flex flex-col gap-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
      role="alert"
    >
      <div className="flex items-center gap-1.5 font-medium">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        Wrong concept domain
      </div>
      <ul className="ml-5 list-disc space-y-0.5">
        {[...violations.entries()].map(([id, actual]) => (
          <li key={id}>
            Concept {id}:{' '}
            {actual == null
              ? 'not found in vocabulary'
              : `expected "${expectedDomain}", got "${actual}"`}
          </li>
        ))}
      </ul>
    </div>
  )
}
