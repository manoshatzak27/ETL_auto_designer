import { useState } from 'react'
import { ChevronDown, ChevronUp, Sparkles } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'

interface Props {
  value: string
  onChange: (v: string) => void
  tableName: string
}

export default function ExtraInstructions({ value, onChange, tableName }: Props) {
  const [open, setOpen] = useState(!!value)

  return (
    <div className="rounded-xl border border-dashed border-border overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-secondary/60 hover:bg-accent transition-colors"
      >
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-amber-500" />
          <span className="text-sm font-medium text-secondary-foreground">
            Extra instructions for AI
            {value && <span className="ml-2 text-xs font-normal text-muted-foreground">(added)</span>}
          </span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-muted-foreground" /> : <ChevronDown className="w-4 h-4 text-muted-foreground" />}
      </button>

      {open && (
        <div className="bg-card px-5 py-4">
          <p className="text-sm text-muted-foreground mb-2">
            Describe any custom transformation logic, special cases, or constraints for the{' '}
            <code className="bg-muted px-1 rounded">{tableName}</code> script.
            This text is injected verbatim into the AI prompt.
          </p>
          <Textarea
            value={value}
            onChange={e => onChange(e.target.value)}
            rows={4}
            placeholder={`e.g. "Only include patients with a valid diagnosis date. Set race_concept_id to 8527 (White) for all patients from cohort A."`}
            className="font-mono resize-y"
          />
          <p className="text-xs text-muted-foreground mt-1.5">
            {value.length} characters · Will be included under "EXTRA INSTRUCTIONS FROM USER" in the AI prompt
          </p>
        </div>
      )}
    </div>
  )
}
