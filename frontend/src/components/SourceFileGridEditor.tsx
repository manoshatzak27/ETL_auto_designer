// Spreadsheet-style grid for viewing/editing a source file's raw content in
// place. In full mode it fetches the entire file and lets the user edit
// cells, add/delete rows, and add/rename/delete columns, then writes it back
// to disk. In preview mode (previewRowLimit set) it's read-only and only
// fetches the first N rows, for a quick look at large files.

import { useEffect, useState } from 'react'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import { getSourceFileContent, updateSourceFileContent } from '../api/client'
import type { Project } from '../types'

interface Props {
  projectId: string
  filename: string
  onClose: () => void
  onSaved: (project: Project) => void
  previewRowLimit?: number
}

export default function SourceFileGridEditor({ projectId, filename, onClose, onSaved, previewRowLimit }: Props) {
  const isPreview = !!previewRowLimit
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<Record<string, string>[]>([])
  const [originalColumns, setOriginalColumns] = useState<string[]>([])
  const [totalRowCount, setTotalRowCount] = useState(0)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    getSourceFileContent(projectId, filename, previewRowLimit)
      .then(content => {
        if (cancelled) return
        setColumns(content.columns)
        setRows(content.rows)
        setOriginalColumns(content.columns)
        setTotalRowCount(content.row_count)
        setDirty(false)
      })
      .catch(() => { if (!cancelled) setError('Failed to load file content.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [projectId, filename, previewRowLimit])

  const setCell = (rowIdx: number, col: string, value: string) => {
    setRows(prev => prev.map((r, i) => (i === rowIdx ? { ...r, [col]: value } : r)))
    setDirty(true)
  }

  const addRow = () => {
    setRows(prev => [...prev, Object.fromEntries(columns.map(c => [c, '']))])
    setDirty(true)
  }

  const deleteRow = (rowIdx: number) => {
    setRows(prev => prev.filter((_, i) => i !== rowIdx))
    setDirty(true)
  }

  const addColumn = () => {
    const name = window.prompt('New column name:')
    if (name == null) return
    const trimmed = name.trim()
    if (!trimmed) return
    if (columns.includes(trimmed)) {
      window.alert(`Column "${trimmed}" already exists.`)
      return
    }
    setColumns(prev => [...prev, trimmed])
    setRows(prev => prev.map(r => ({ ...r, [trimmed]: '' })))
    setDirty(true)
  }

  const renameColumn = (col: string) => {
    const name = window.prompt('Rename column:', col)
    if (name == null) return
    const trimmed = name.trim()
    if (!trimmed || trimmed === col) return
    if (columns.includes(trimmed)) {
      window.alert(`Column "${trimmed}" already exists.`)
      return
    }
    setColumns(prev => prev.map(c => (c === col ? trimmed : c)))
    setRows(prev => prev.map(r => {
      const { [col]: value, ...rest } = r
      return { ...rest, [trimmed]: value ?? '' }
    }))
    setDirty(true)
  }

  const deleteColumn = (col: string) => {
    if (columns.length <= 1) {
      window.alert('At least one column is required.')
      return
    }
    setColumns(prev => prev.filter(c => c !== col))
    setRows(prev => prev.map(r => {
      const { [col]: _removed, ...rest } = r
      return rest
    }))
    setDirty(true)
  }

  const handleCancel = () => {
    if (dirty && !window.confirm('Discard unsaved changes?')) return
    onClose()
  }

  const handleSave = async () => {
    const removedOrRenamed = originalColumns.filter(c => !columns.includes(c))
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
      const updated = await updateSourceFileContent(projectId, filename, columns, rows)
      onSaved(updated)
      onClose()
    } catch {
      setError('Failed to save file. Please check for duplicate/empty column names and try again.')
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

  return (
    <div className="flex flex-col gap-3 min-h-0 flex-1">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-foreground truncate">{filename}</p>
        {!isPreview && (
          <button
            onClick={addColumn}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary"
          >
            <Plus className="w-3 h-3" /> Add column
          </button>
        )}
      </div>

      {isPreview && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
          Previewing the first {rows.length.toLocaleString()} of {totalRowCount.toLocaleString()} rows — read-only.
          Close this and choose "Open whole file" to edit.
        </p>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="border border-border rounded-lg overflow-auto flex-1 min-h-0 bg-card">
        <table className="text-xs min-w-full">
          <thead className="bg-muted sticky top-0 z-10 border-b border-border">
            <tr>
              {!isPreview && <th className="w-8" />}
              {columns.map(col => (
                <th key={col} className="text-left px-2 py-1.5 font-medium text-muted-foreground whitespace-nowrap font-mono">
                  {isPreview ? (
                    col
                  ) : (
                    <div className="flex items-center gap-1">
                      <input
                        value={col}
                        onClick={() => renameColumn(col)}
                        readOnly
                        title="Click to rename"
                        className="bg-transparent font-mono font-medium text-foreground w-24 cursor-pointer focus:outline-none focus:ring-1 focus:ring-ring rounded px-1"
                      />
                      <button
                        onClick={() => deleteColumn(col)}
                        className="text-muted-foreground hover:text-destructive"
                        title="Delete column"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-card' : 'bg-muted/40'}>
                {!isPreview && (
                  <td className="px-1 text-center">
                    <button
                      onClick={() => deleteRow(i)}
                      className="text-muted-foreground hover:text-destructive"
                      title="Delete row"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </td>
                )}
                {columns.map(col => (
                  <td key={col} className="px-1 py-0.5">
                    {isPreview ? (
                      <span className="block px-1 py-0.5 font-mono text-foreground truncate">{row[col] ?? ''}</span>
                    ) : (
                      <input
                        value={row[col] ?? ''}
                        onChange={e => setCell(i, col, e.target.value)}
                        className="w-full min-w-[6rem] bg-transparent px-1 py-0.5 font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-ring rounded"
                      />
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {!isPreview && (
            <button
              onClick={addRow}
              className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary"
            >
              <Plus className="w-3 h-3" /> Add row
            </button>
          )}
          <span className="text-xs text-muted-foreground">{rows.length.toLocaleString()} rows shown</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCancel}
            disabled={saving}
            className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            {isPreview ? 'Close' : 'Cancel'}
          </button>
          {!isPreview && (
            <button
              onClick={handleSave}
              disabled={saving || columns.length === 0}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md disabled:opacity-40 hover:bg-primary/90"
            >
              {saving && <Loader2 className="w-3 h-3 animate-spin" />}
              Save
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
