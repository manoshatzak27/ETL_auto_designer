import { useEffect } from 'react'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import type { ColumnMatch } from '../api/client'
import { ColumnSuggestion } from './ColumnAutoMatch'

interface Props {
  label: string
  sourceColumns: string[]
  value: string
  onChange: (val: string) => void
  required?: boolean
  hint?: string
  disabled?: boolean
  /** An auto-match suggestion, shown while the field is empty. */
  suggestion?: ColumnMatch
  /** Take the suggestion; defaults to picking its column. Date fields pass
   *  their own so the suggested date format is set too. */
  onUseSuggestion?: () => void
}

export default function FieldMapper({ label, sourceColumns, value, onChange, required, hint, disabled, suggestion, onUseSuggestion }: Props) {
  useEffect(() => {
    if (value !== '' && !sourceColumns.includes(value)) {
      onChange('')
    }
  }, [value, sourceColumns])

  return (
    <div className="flex flex-col gap-1">
      <Label>
        {label}
        {required && <span className="ml-1 text-destructive text-base font-bold leading-none">*</span>}
      </Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      <Select value={sourceColumns.includes(value) ? value : ''} onChange={e => onChange(e.target.value)} disabled={disabled}>
        <option value="">— not mapped —</option>
        {sourceColumns.map(col => (
          <option key={col} value={col}>{col}</option>
        ))}
      </Select>
      {suggestion?.column && !value && !disabled && sourceColumns.includes(suggestion.column) && (
        <ColumnSuggestion suggestion={suggestion} onUse={onUseSuggestion ? () => onUseSuggestion() : onChange} />
      )}
    </div>
  )
}
