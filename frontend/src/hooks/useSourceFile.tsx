import { useState } from 'react'
import type { Project, SourceFile } from '../types'
import { FileText } from 'lucide-react'
import { useStepFileSelection } from '../contexts/StepFileSelectionContext'

interface FileSwitch<T> {
  getConfig: () => T
  setConfig: (saved: T | undefined) => void
}

interface UseSourceFileResult {
  cols: string[]
  filePicker: React.ReactNode
  selectedFile: SourceFile | null
}

export function useSourceFile<T = unknown>(
  project: Project,
  stepSlug: string,
  fileSwitch?: FileSwitch<T>,
): UseSourceFileResult {
  const store = useStepFileSelection()
  const files = project.source_files ?? []
  const [selectedIndex, setSelectedIndex] = useState(() => store.current.indices[stepSlug] ?? 0)

  if (files.length === 0) {
    return { cols: project.source_columns ?? [], filePicker: null, selectedFile: null }
  }

  if (files.length === 1) {
    return { cols: files[0].columns, filePicker: null, selectedFile: files[0] }
  }

  const safeIndex = Math.min(selectedIndex, files.length - 1)
  const cols = files[safeIndex]?.columns ?? []

  const filePicker = (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs text-muted-foreground font-medium">Source file for this step</p>
      <div className="flex flex-wrap gap-2">
        {files.map((f, idx) => (
          <button
            key={f.filename}
            onClick={() => {
              if (idx === safeIndex) return
              if (fileSwitch) {
                // Persist the current file's config into the long-lived store
                if (!store.current.configs[stepSlug]) store.current.configs[stepSlug] = {}
                store.current.configs[stepSlug][files[safeIndex].filename] = fileSwitch.getConfig()
                fileSwitch.setConfig(store.current.configs[stepSlug]?.[files[idx].filename] as T | undefined)
              }
              store.current.indices[stepSlug] = idx
              setSelectedIndex(idx)
            }}
            className={
              idx === safeIndex
                ? 'flex items-center gap-1.5 rounded-full border border-primary bg-primary/10 px-3 py-1 text-xs font-semibold text-primary transition-colors'
                : 'flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground transition-colors'
            }
          >
            <FileText className="size-3 flex-shrink-0" />
            {f.filename}
          </button>
        ))}
      </div>
    </div>
  )

  return { cols, filePicker, selectedFile: files[safeIndex] ?? null }
}
