// Small read-only preview of an OMOP CSV (or any CSV with a header row).
// Used on the Finalize step to let users peek at the top N rows of each
// generated output without downloading the file.

import { Download, Loader2 } from 'lucide-react'

interface Props {
  columns: string[]
  rows: Record<string, string>[]
  totalRows: number
  filename: string
  projectId: string
  loading?: boolean
  error?: string
}

export default function OmopTablePreview({
  columns,
  rows,
  totalRows,
  filename,
  projectId,
  loading,
  error,
}: Props) {
  const downloadUrl = `/api/projects/${projectId}/download/${encodeURIComponent(filename)}`

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 justify-center text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading preview…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
        {error}
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-muted-foreground italic">
        File is empty.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="border border-border rounded-lg overflow-auto max-h-[420px] bg-card">
        <table className="text-xs min-w-full">
          <thead className="bg-muted sticky top-0 z-10 border-b border-border">
            <tr>
              {columns.map(c => (
                <th
                  key={c}
                  className="text-left px-3 py-2 font-medium text-muted-foreground whitespace-nowrap font-mono"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className={i % 2 === 0 ? 'bg-card' : 'bg-muted/40'}
              >
                {columns.map(c => {
                  const v = row[c]
                  return (
                    <td
                      key={c}
                      className="px-3 py-1.5 whitespace-nowrap text-foreground font-mono"
                    >
                      {v === '' || v == null
                        ? <span className="text-muted-foreground italic">—</span>
                        : v}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-xs text-muted-foreground px-1">
        <span>
          Top <strong>{rows.length}</strong> of <strong>{totalRows.toLocaleString()}</strong> rows
        </span>
        <a
          href={downloadUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 text-primary hover:text-primary/80 font-medium"
        >
          <Download className="w-3 h-3" /> Download full CSV
        </a>
      </div>
    </div>
  )
}
