import type { ExecutionTableRow } from '../api/client'
import { cn } from '@/lib/utils'

const STATUS_LABEL: Record<ExecutionTableRow['status'], { label: string; className: string }> = {
  ok:      { label: 'OK',       className: 'bg-success/10 text-success' },
  failed:  { label: 'Failed',   className: 'bg-destructive/10 text-destructive' },
  not_run: { label: 'Not run',  className: 'bg-muted text-muted-foreground' },
  running: { label: 'Running…', className: 'bg-primary/10 text-primary' },
  unknown: { label: '—',        className: 'text-muted-foreground' },
}

const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())

// Per-table counts of the last pipeline run. "Dropped" counts the rows the
// scripts reported as skipped/dropped (WARNING lines); "INFO" counts the
// empty/unmapped-field notes on rows that were written.
// Clicking a row shows that table's section of the log (again to show all).
export default function ExecutionStatsTable({ stats, selected, onSelect, executing }: {
  stats: ExecutionTableRow[]
  executing: boolean
  selected: string
  onSelect: (table: string) => void
}) {
  const ran = (s: ExecutionTableRow) => s.status === 'ok' || s.status === 'failed'
  // Sum of a column over the rows that have it; null (shown as —) when none do.
  const total = (key: 'rows_written' | 'dropped' | 'warnings' | 'info') => {
    // Only tables that ran: one waiting or running has no counts yet.
    const values = stats.filter(ran).map(s => s[key]).filter((v): v is number => v !== null)
    return values.length ? values.reduce((sum, v) => sum + v, 0) : null
  }
  // A table still to come in the run in progress reads "Waiting", not "Not run".
  const statusLabel = (s: ExecutionTableRow) =>
    executing && s.status === 'not_run' ? { label: 'Waiting', className: STATUS_LABEL.not_run.className } : STATUS_LABEL[s.status]

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2">Table</th>
            <th className="text-left font-medium px-3 py-2">Status</th>
            <th className="text-right font-medium px-3 py-2">Rows written</th>
            <th className="text-right font-medium px-3 py-2">Dropped rows</th>
            <th className="text-right font-medium px-3 py-2">Warnings</th>
            <th className="text-right font-medium px-3 py-2">INFO</th>
          </tr>
        </thead>
        <tbody>
          {stats.map(s => (
            <tr
              key={s.table}
              onClick={() => ran(s) && onSelect(s.table)}
              className={cn(
                'border-t border-border',
                ran(s) && 'cursor-pointer hover:bg-muted/50',
                selected === s.table && 'bg-primary/10',
              )}
            >
              <td className="px-3 py-1.5 font-mono text-xs">{s.table}</td>
              <td className="px-3 py-1.5">
                <span className={cn('text-xs font-medium px-2 py-0.5 rounded-full', statusLabel(s).className)}>
                  {statusLabel(s).label}
                </span>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">{ran(s) ? fmt(s.rows_written) : '—'}</td>
              <td className={cn('px-3 py-1.5 text-right tabular-nums', (s.dropped ?? 0) > 0 && 'text-red-700 font-medium')}>{ran(s) ? fmt(s.dropped) : '—'}</td>
              <td className={cn('px-3 py-1.5 text-right tabular-nums', (s.warnings ?? 0) > 0 && 'text-amber-700 font-medium')}>{ran(s) ? fmt(s.warnings) : '—'}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{ran(s) ? fmt(s.info) : '—'}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-border bg-muted/30 font-medium">
            <td className="px-3 py-1.5" colSpan={2}>Total</td>
            <td className="px-3 py-1.5 text-right tabular-nums">{fmt(total('rows_written'))}</td>
            <td className="px-3 py-1.5 text-right tabular-nums">{fmt(total('dropped'))}</td>
            <td className="px-3 py-1.5 text-right tabular-nums">{fmt(total('warnings'))}</td>
            <td className="px-3 py-1.5 text-right tabular-nums">{fmt(total('info'))}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}
