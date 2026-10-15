// Spreadsheet-style grid for viewing/editing a source file's raw content in
// place. It only ever holds one page of rows from the backend; edits are
// tracked as a diff against the file (changed cells, deleted rows, appended
// rows, and the column list with renames/additions/removals) and saved with a
// PATCH that the backend applies while streaming through the file, so files
// of any size can be edited without loading them into the browser.

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, Plus, RotateCcw, Trash2 } from 'lucide-react'
import { getSourceFileContent, patchSourceFileContent } from '../api/client'
import type { Project, SourceFileEdits } from '../types'
import clsx from 'clsx'

const PAGE_SIZE = 500
// Fixed sizes (px) the windowing math relies on; rows and columns are pinned
// to them.
const ROW_HEIGHT = 28
const COL_WIDTH = 144
// The delete-button and row-number columns ahead of the data columns.
const DELETE_COL_WIDTH = 32
const ROW_NUM_COL_WIDTH = 64
const LEAD_WIDTH = DELETE_COL_WIDTH + ROW_NUM_COL_WIDTH
const OVERSCAN_ROWS = 10
const OVERSCAN_COLS = 3

interface Props {
  projectId: string
  filename: string
  onClose: () => void
  onSaved: (project: Project) => void
}

// `key` identifies a column for change tracking and survives renames: the
// original header name for existing columns, a generated id for new ones.
interface GridColumn {
  key: string
  name: string
  source: string | null
}

type RowValues = Record<string, string>

interface GridRowProps {
  rowIdx: number
  // Only the columns in view; the rest are stood in for by spacer cells.
  columns: GridColumn[]
  leftPad: number
  rightPad: number
  added: boolean
  deleted: boolean
  // File row as served (keyed by source column); undefined for added rows.
  original: RowValues | undefined
  // Edits to a file row, or an added row's values — keyed by column key.
  values: RowValues | undefined
  onCellChange: (rowIdx: number, col: GridColumn, value: string) => void
  onToggleDelete: (rowIdx: number) => void
}

// Memoized so typing in one cell only re-renders that row: every prop is
// referentially stable unless this row's own data changed.
const GridRow = memo(function GridRow({
  rowIdx, columns, leftPad, rightPad, added, deleted, original, values, onCellChange, onToggleDelete,
}: GridRowProps) {
  return (
    <tr
      style={{ height: ROW_HEIGHT }}
      className={clsx(
        rowIdx % 2 === 0 ? 'bg-card' : 'bg-muted/40',
        deleted && 'line-through opacity-50',
        added && 'bg-emerald-50/60',
      )}
    >
      <td className="px-1 text-center">
        <button
          onClick={() => onToggleDelete(rowIdx)}
          className="text-muted-foreground hover:text-destructive"
          title={deleted ? 'Restore row' : 'Delete row'}
        >
          {deleted ? <RotateCcw className="w-3 h-3" /> : <Trash2 className="w-3 h-3" />}
        </button>
      </td>
      <td className="px-2 text-right font-mono text-muted-foreground tabular-nums">
        {added ? 'new' : (rowIdx + 1).toLocaleString()}
      </td>
      {leftPad > 0 && <td />}
      {columns.map(col => {
        const edit = values?.[col.key]
        const value = edit ?? (!added && col.source ? original?.[col.source] : undefined) ?? ''
        return (
          <td key={col.key} className="px-1 py-0">
            <input
              value={value}
              onChange={e => onCellChange(rowIdx, col, e.target.value)}
              disabled={deleted}
              className={clsx(
                'w-full bg-transparent px-1 py-0.5 font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-ring rounded',
                !added && edit != null && 'bg-amber-100/70',
              )}
            />
          </td>
        )
      })}
      {rightPad > 0 && <td />}
    </tr>
  )
})

export default function SourceFileGridEditor({ projectId, filename, onClose, onSaved }: Props) {
  const [loading, setLoading] = useState(true)
  const [pageLoading, setPageLoading] = useState(false)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const [originalColumns, setOriginalColumns] = useState<string[]>([])
  const [columns, setColumns] = useState<GridColumn[]>([])
  const [fileRowCount, setFileRowCount] = useState(0)
  const [page, setPage] = useState(0)
  const [pageOffset, setPageOffset] = useState(0)
  // Keyed by original (source) column name, as served by the backend.
  const [pageRows, setPageRows] = useState<RowValues[]>([])

  // Keyed by file row index, then column key.
  const [cellEdits, setCellEdits] = useState<Record<number, RowValues>>({})
  const [deletedRows, setDeletedRows] = useState<Set<number>>(new Set())
  // Keyed by column key; appended after the file's last row.
  const [addedRows, setAddedRows] = useState<RowValues[]>([])
  const nextColumnId = useRef(0)
  const latestRequest = useRef(0)

  const totalRows = fileRowCount + addedRows.length
  const pageCount = Math.max(1, Math.ceil(totalRows / PAGE_SIZE))
  const pageStart = page * PAGE_SIZE
  const pageEnd = Math.min(pageStart + PAGE_SIZE, totalRows)

  const loadPage = (target: number, initial = false) => {
    const offset = target * PAGE_SIZE
    const requestId = ++latestRequest.current
    if (!initial && offset >= fileRowCount) {
      // Page holds only rows added in this session — nothing to fetch.
      setPage(target)
      setPageOffset(offset)
      setPageRows([])
      return
    }
    if (initial) setLoading(true)
    else setPageLoading(true)
    setError('')
    getSourceFileContent(projectId, filename, offset, PAGE_SIZE)
      .then(content => {
        if (requestId !== latestRequest.current) return
        if (initial) {
          setOriginalColumns(content.columns)
          setColumns(content.columns.map(c => ({ key: c, name: c, source: c })))
        }
        // row_count is a line count, which overcounts when quoted values span
        // lines — trust a short page as the real end of the file.
        const count = content.rows.length < PAGE_SIZE ? offset + content.rows.length : content.row_count
        setFileRowCount(initial ? count : Math.min(fileRowCount, count))
        setPage(target)
        setPageOffset(offset)
        setPageRows(content.rows)
      })
      .catch(() => { if (requestId === latestRequest.current) setError('Failed to load file content.') })
      .finally(() => {
        if (requestId !== latestRequest.current) return
        setLoading(false)
        setPageLoading(false)
      })
  }

  useEffect(() => {
    setCellEdits({})
    setDeletedRows(new Set())
    setAddedRows([])
    loadPage(0, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, filename])

  const originalValue = (rowIdx: number, col: GridColumn): string => {
    if (!col.source) return ''
    return pageRows[rowIdx - pageOffset]?.[col.source] ?? ''
  }

  const setCell = (rowIdx: number, col: GridColumn, value: string) => {
    if (rowIdx >= fileRowCount) {
      const addedIdx = rowIdx - fileRowCount
      setAddedRows(prev => prev.map((r, i) => (i === addedIdx ? { ...r, [col.key]: value } : r)))
      return
    }
    const unchanged = value === originalValue(rowIdx, col)
    setCellEdits(prev => {
      const rowEdits = { ...prev[rowIdx], [col.key]: value }
      if (unchanged) delete rowEdits[col.key]
      const next = { ...prev, [rowIdx]: rowEdits }
      if (!Object.keys(rowEdits).length) delete next[rowIdx]
      return next
    })
  }

  const addRow = () => {
    setAddedRows(prev => [...prev, {}])
    const lastPage = Math.floor(totalRows / PAGE_SIZE)
    if (lastPage !== page) loadPage(lastPage)
  }

  const toggleDeleteRow = (rowIdx: number) => {
    if (rowIdx >= fileRowCount) {
      const addedIdx = rowIdx - fileRowCount
      setAddedRows(prev => prev.filter((_, i) => i !== addedIdx))
      // Removing the only row on the last page would leave it empty.
      if (page > 0 && rowIdx === pageStart && rowIdx === totalRows - 1) loadPage(page - 1)
      return
    }
    setDeletedRows(prev => {
      const next = new Set(prev)
      if (next.has(rowIdx)) next.delete(rowIdx)
      else next.add(rowIdx)
      return next
    })
  }

  // Rows are memoized, so they get stable wrappers that always call the
  // latest handler instead of the handlers themselves (which close over
  // per-render state and change identity every render).
  const handlers = useRef({ setCell, toggleDeleteRow })
  handlers.current = { setCell, toggleDeleteRow }
  const onCellChange = useCallback(
    (rowIdx: number, col: GridColumn, value: string) => handlers.current.setCell(rowIdx, col, value),
    [],
  )
  const onToggleDelete = useCallback((rowIdx: number) => handlers.current.toggleDeleteRow(rowIdx), [])

  // Windowing: a page is up to PAGE_SIZE rows × N columns of inputs, far too
  // many to keep mounted, so only the rows and columns in view (plus
  // overscan) are rendered, with spacers standing in for the rest. State
  // holds the first visible row/column rather than raw scroll offsets, so
  // scrolling within the same row/column re-renders nothing.
  const scrollRef = useRef<HTMLDivElement>(null)
  const [firstRow, setFirstRow] = useState(0)
  const [firstCol, setFirstCol] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(600)
  const [viewportWidth, setViewportWidth] = useState(1200)
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    setFirstRow(Math.floor(el.scrollTop / ROW_HEIGHT))
    setFirstCol(Math.max(0, Math.floor((el.scrollLeft - LEAD_WIDTH) / COL_WIDTH)))
  }
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const observer = new ResizeObserver(() => {
      setViewportHeight(el.clientHeight)
      setViewportWidth(el.clientWidth)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [loading])
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0
    setFirstRow(0)
  }, [page])

  const colStart = Math.max(0, firstCol - OVERSCAN_COLS)
  const colEnd = Math.min(columns.length, firstCol + Math.ceil(viewportWidth / COL_WIDTH) + 1 + OVERSCAN_COLS)
  // Memoized so rows keep getting the same array (and stay memoized) until
  // the column window actually moves.
  const visibleColumns = useMemo(() => columns.slice(colStart, colEnd), [columns, colStart, colEnd])
  const leftPad = colStart * COL_WIDTH
  const rightPad = (columns.length - colEnd) * COL_WIDTH

  const nameTaken = (name: string, exceptKey?: string) =>
    columns.some(c => c.name === name && c.key !== exceptKey)

  const addColumn = () => {
    const name = window.prompt('New column name:')
    if (name == null) return
    const trimmed = name.trim()
    if (!trimmed) return
    if (nameTaken(trimmed)) {
      window.alert(`Column "${trimmed}" already exists.`)
      return
    }
    setColumns(prev => [...prev, { key: `__new_${nextColumnId.current++}`, name: trimmed, source: null }])
  }

  const renameColumn = (col: GridColumn) => {
    const name = window.prompt('Rename column:', col.name)
    if (name == null) return
    const trimmed = name.trim()
    if (!trimmed || trimmed === col.name) return
    if (nameTaken(trimmed, col.key)) {
      window.alert(`Column "${trimmed}" already exists.`)
      return
    }
    setColumns(prev => prev.map(c => (c.key === col.key ? { ...c, name: trimmed } : c)))
  }

  const deleteColumn = (col: GridColumn) => {
    if (columns.length <= 1) {
      window.alert('At least one column is required.')
      return
    }
    setColumns(prev => prev.filter(c => c.key !== col.key))
  }

  // Edits to deleted rows/columns are kept until save so undoing a delete
  // restores them; they're dropped here instead.
  const buildEdits = (): SourceFileEdits => {
    const nameByKey = new Map(columns.map(c => [c.key, c.name]))
    const toNames = (values: RowValues) => {
      const out: RowValues = {}
      for (const [key, value] of Object.entries(values)) {
        const name = nameByKey.get(key)
        if (name != null) out[name] = value
      }
      return out
    }
    const cell_edits: SourceFileEdits['cell_edits'] = {}
    for (const [row, values] of Object.entries(cellEdits)) {
      if (deletedRows.has(Number(row))) continue
      const named = toNames(values)
      if (Object.keys(named).length) cell_edits[Number(row)] = named
    }
    return {
      columns: columns.map(c => ({ source: c.source, name: c.name })),
      cell_edits,
      deleted_rows: [...deletedRows].sort((a, b) => a - b),
      added_rows: addedRows.map(toNames),
    }
  }

  const columnsChanged =
    columns.length !== originalColumns.length ||
    columns.some((c, i) => c.source !== originalColumns[i] || c.name !== originalColumns[i])
  const columnKeys = new Set(columns.map(c => c.key))
  const editedCellCount = Object.entries(cellEdits).reduce(
    (n, [row, r]) => (deletedRows.has(Number(row)) ? n : n + Object.keys(r).filter(k => columnKeys.has(k)).length),
    0,
  )
  const dirty = columnsChanged || editedCellCount > 0 || deletedRows.size > 0 || addedRows.length > 0

  const changeSummary = [
    editedCellCount && `${editedCellCount.toLocaleString()} cell${editedCellCount === 1 ? '' : 's'} edited`,
    deletedRows.size && `${deletedRows.size.toLocaleString()} row${deletedRows.size === 1 ? '' : 's'} deleted`,
    addedRows.length && `${addedRows.length.toLocaleString()} row${addedRows.length === 1 ? '' : 's'} added`,
    columnsChanged && 'columns changed',
  ].filter(Boolean).join(' · ')

  const handleCancel = () => {
    if (dirty && !window.confirm('Discard unsaved changes?')) return
    onClose()
  }

  const handleSave = async () => {
    const removedOrRenamed = originalColumns.filter(orig => !columns.some(c => c.source === orig && c.name === orig))
    if (removedOrRenamed.length > 0) {
      const proceed = window.confirm(
        'Renaming or removing columns here may leave stale mapping decisions on the ' +
        'Concepts step for: ' + removedOrRenamed.join(', ') + '.\n\n' +
        'You may need to revisit that step afterwards. Continue?',
      )
      if (!proceed) return
    }
    setSaving(true)
    setError('')
    try {
      const updated = await patchSourceFileContent(projectId, filename, buildEdits())
      onSaved(updated)
      onClose()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'Failed to save file. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-12 justify-center text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading file content…
      </div>
    )
  }

  const pageRowCount = Math.max(0, pageEnd - pageStart)
  const windowStart = Math.min(pageRowCount, Math.max(0, firstRow - OVERSCAN_ROWS))
  const windowEnd = Math.min(pageRowCount, firstRow + Math.ceil(viewportHeight / ROW_HEIGHT) + 1 + OVERSCAN_ROWS)
  const visibleRows = Array.from({ length: Math.max(0, windowEnd - windowStart) }, (_, i) => pageStart + windowStart + i)
  const colSpan = 2 + visibleColumns.length + (leftPad > 0 ? 1 : 0) + (rightPad > 0 ? 1 : 0)

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-foreground truncate">{filename}</p>
        <button
          onClick={addColumn}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary"
        >
          <Plus className="w-3 h-3" /> Add column
        </button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={clsx('border border-border rounded-lg overflow-auto flex-1 min-h-0 bg-card', pageLoading && 'opacity-50 pointer-events-none')}
      >
        <table className="text-xs table-fixed" style={{ width: LEAD_WIDTH + columns.length * COL_WIDTH }}>
          <colgroup>
            <col style={{ width: DELETE_COL_WIDTH }} />
            <col style={{ width: ROW_NUM_COL_WIDTH }} />
            {leftPad > 0 && <col style={{ width: leftPad }} />}
            {visibleColumns.map(col => <col key={col.key} style={{ width: COL_WIDTH }} />)}
            {rightPad > 0 && <col style={{ width: rightPad }} />}
          </colgroup>
          <thead className="bg-muted sticky top-0 z-10 border-b border-border">
            <tr>
              <th />
              <th className="px-2 py-1.5 text-right font-medium text-muted-foreground">#</th>
              {leftPad > 0 && <th />}
              {visibleColumns.map(col => (
                <th key={col.key} className="text-left px-2 py-1.5 font-medium text-muted-foreground whitespace-nowrap font-mono">
                  <div className="flex items-center gap-1">
                    <input
                      value={col.name}
                      onClick={() => renameColumn(col)}
                      readOnly
                      title={`${col.name} — click to rename`}
                      className="bg-transparent font-mono font-medium text-foreground flex-1 min-w-0 cursor-pointer focus:outline-none focus:ring-1 focus:ring-ring rounded px-1"
                    />
                    <button
                      onClick={() => deleteColumn(col)}
                      className="text-muted-foreground hover:text-destructive"
                      title="Delete column"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </th>
              ))}
              {rightPad > 0 && <th />}
            </tr>
          </thead>
          <tbody>
            {windowStart > 0 && (
              <tr style={{ height: windowStart * ROW_HEIGHT }}><td colSpan={colSpan} /></tr>
            )}
            {visibleRows.map(rowIdx => {
              const added = rowIdx >= fileRowCount
              return (
                <GridRow
                  key={rowIdx}
                  rowIdx={rowIdx}
                  columns={visibleColumns}
                  leftPad={leftPad}
                  rightPad={rightPad}
                  added={added}
                  deleted={deletedRows.has(rowIdx)}
                  original={added ? undefined : pageRows[rowIdx - pageOffset]}
                  values={added ? addedRows[rowIdx - fileRowCount] : cellEdits[rowIdx]}
                  onCellChange={onCellChange}
                  onToggleDelete={onToggleDelete}
                />
              )
            })}
            {windowEnd < pageRowCount && (
              <tr style={{ height: (pageRowCount - windowEnd) * ROW_HEIGHT }}><td colSpan={colSpan} /></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <button
            onClick={addRow}
            disabled={pageLoading}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary disabled:opacity-40"
          >
            <Plus className="w-3 h-3" /> Add row
          </button>
          <button
            onClick={() => loadPage(page - 1)}
            disabled={page === 0 || pageLoading}
            className="rounded-md border border-border p-1 text-foreground hover:bg-secondary disabled:opacity-40"
            title="Previous page"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <span className="text-xs text-muted-foreground tabular-nums">
            {totalRows === 0
              ? 'No rows'
              : `Rows ${(pageStart + 1).toLocaleString()}–${pageEnd.toLocaleString()} of ${totalRows.toLocaleString()} · page ${(page + 1).toLocaleString()} of ${pageCount.toLocaleString()}`}
          </span>
          <button
            onClick={() => loadPage(page + 1)}
            disabled={page >= pageCount - 1 || pageLoading}
            className="rounded-md border border-border p-1 text-foreground hover:bg-secondary disabled:opacity-40"
            title="Next page"
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
          {pageLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-2">
          {changeSummary && <span className="text-xs text-muted-foreground">{changeSummary}</span>}
          <button
            onClick={handleCancel}
            disabled={saving}
            className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !dirty || columns.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md disabled:opacity-40 hover:bg-primary/90"
          >
            {saving && <Loader2 className="w-3 h-3 animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
