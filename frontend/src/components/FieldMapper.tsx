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
  // A mapped column the file no longer has (renamed or removed in the grid
  // editor, or gone from a re-uploaded version). Kept and flagged rather than
  // cleared, so the user sees what was mapped and picks its replacement.
  const missing = value !== '' && !sourceColumns.includes(value)

  return (
    <div className="flex flex-col gap-1">
      <Label>
        {label}
        {required && <span className="ml-1 text-destructive text-base font-bold leading-none">*</span>}
      </Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      <Select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className={missing ? 'rounded-lg ring-2 ring-amber-400' : undefined}
      >
        <option value="">— not mapped —</option>
        {missing && <option value={value}>{`⚠ ${value} (not in this file)`}</option>}
        {sourceColumns.map(col => (
          <option key={col} value={col}>{col}</option>
        ))}
      </Select>
      {missing && (
        <p className="text-xs text-amber-700">
          <span className="font-mono">{value}</span> is no longer in this file, so the generated script
          won't find it. Choose another column, or "— not mapped —".
        </p>
      )}
      {suggestion?.column && !value && !disabled && sourceColumns.includes(suggestion.column) && (
        <ColumnSuggestion suggestion={suggestion} onUse={onUseSuggestion ? () => onUseSuggestion() : onChange} />
      )}
    </div>
  )
}
