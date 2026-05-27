import { AlertCircle } from 'lucide-react'

interface Props {
  message?: string
  className?: string
}

export default function ErrorBanner({ message, className = '' }: Props) {
  if (!message) return null
  return (
    <div
      className={
        'bg-red-50 border border-red-200 rounded-lg px-3 py-2 flex gap-2 text-sm text-red-700 ' +
        className
      }
      role="alert"
    >
      <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <span>{message}</span>
    </div>
  )
}
