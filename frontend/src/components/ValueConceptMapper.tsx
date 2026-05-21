import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'

interface Props {
  label: string
  sourceValues: string[]
  mapping: Record<string, number>
  onChange: (mapping: Record<string, number>) => void
  hint?: string
}

export default function ValueConceptMapper({ label, sourceValues, mapping, onChange, hint }: Props) {
  const handleChange = (val: string, conceptId: string) => {
    const num = parseInt(conceptId, 10)
    const next = { ...mapping }
    if (!isNaN(num) && conceptId !== '') {
      next[val] = num
    } else {
      delete next[val]
    }
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Label>{label}</Label>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-muted-foreground">Source Value</th>
              <th className="text-left px-4 py-2 font-medium text-muted-foreground">OMOP Concept ID</th>
            </tr>
          </thead>
          <tbody>
            {sourceValues.map((val, i) => (
              <tr key={val} className={i % 2 === 0 ? 'bg-card' : 'bg-muted'}>
                <td className="px-4 py-2 font-mono text-foreground">{val}</td>
                <td className="px-4 py-2">
                  <Input
                    type="number"
                    value={mapping[val] ?? ''}
                    onChange={e => handleChange(val, e.target.value)}
                    placeholder="e.g. 8507"
                    className="h-8 py-1"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
