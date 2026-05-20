import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'

interface Props {
  label: string
  sourceColumns: string[]
  value: string
  onChange: (val: string) => void
  required?: boolean
  hint?: string
}

export default function FieldMapper({ label, sourceColumns, value, onChange, required, hint }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <Label>
        {label}
        {required && <span className="ml-1 text-destructive">*</span>}
      </Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      <Select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">— not mapped —</option>
        {sourceColumns.map(col => (
          <option key={col} value={col}>{col}</option>
        ))}
      </Select>
    </div>
  )
}
