import { useState } from 'react'
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react'

interface Props {
  value: string
  onChange: (v: string) => void
  tableName: string
}

export default function ExtraInstructions({ value, onChange, tableName }: Props) {
  const [open, setOpen] = useState(!!value)

  return (
    <div className="border border-dashed border-blue-200 rounded-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-blue-50 hover:bg-blue-100 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-blue-500" />
          <span className="text-sm font-medium text-blue-800">
            Extra instructions for AI
            {value && <span className="ml-2 text-xs font-normal text-blue-500">(added)</span>}
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-blue-400" /> : <ChevronDown className="w-4 h-4 text-blue-400" />}
      </button>

      {open && (
        <div className="bg-white px-5 py-4">
          <p className="text-xs text-gray-500 mb-2">
            Describe any custom transformation logic, special cases, or constraints for the{' '}
            <code className="bg-gray-100 px-1 rounded">{tableName}</code> script.
            This text is injected verbatim into the AI prompt.
          </p>
          <textarea
            value={value}
            onChange={e => onChange(e.target.value)}
            rows={4}
            placeholder={`e.g. "Only include patients with a valid diagnosis date. Set race_concept_id to 8527 (White) for all patients from cohort A."`}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-gray-400 mt-1.5">
            {value.length} characters · Will be included under "EXTRA INSTRUCTIONS FROM USER" in the AI prompt
          </p>
        </div>
      )}
    </div>
  )
}
