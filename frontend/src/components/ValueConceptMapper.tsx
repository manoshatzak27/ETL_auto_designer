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
        <label className="text-sm font-medium text-gray-700">{label}</label>
        {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
      </div>
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-gray-600">Source Value</th>
              <th className="text-left px-4 py-2 font-medium text-gray-600">OMOP Concept ID</th>
            </tr>
          </thead>
          <tbody>
            {sourceValues.map((val, i) => (
              <tr key={val} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                <td className="px-4 py-2 font-mono text-gray-800">{val}</td>
                <td className="px-4 py-2">
                  <input
                    type="number"
                    value={mapping[val] ?? ''}
                    onChange={e => handleChange(val, e.target.value)}
                    placeholder="e.g. 8507"
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
