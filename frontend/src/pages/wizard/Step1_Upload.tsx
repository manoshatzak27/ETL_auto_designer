import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadSource } from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import { UploadCloud, FileText } from 'lucide-react'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

export default function Step1Upload({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const handleFile = async (file: File) => {
    setUploading(true)
    setError('')
    try {
      const updated = await uploadSource(project.id, file)
      onUpdate(updated)
    } catch (e: unknown) {
      setError('Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [project.id])

  const hasSource = !!project.source_filename

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={1}
      onNext={() => navigate(`/project/${project.id}/step/2`)}
      nextDisabled={!hasSource}
      nextLabel="Next: Location →"
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Upload Source Dataset</h2>
          <p className="text-sm text-gray-500 mt-1">
            Upload your flat source CSV file. The system will auto-detect the delimiter and encoding.
          </p>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors cursor-pointer ${
            dragging ? 'border-blue-400 bg-blue-50' : 'border-gray-300 bg-white hover:border-gray-400'
          }`}
          onClick={() => document.getElementById('file-input')?.click()}
        >
          <UploadCloud className="w-10 h-10 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600 font-medium">Drop your CSV file here or click to browse</p>
          <p className="text-xs text-gray-400 mt-1">Supports CSV, TSV, TXT with any delimiter</p>
          <input
            id="file-input"
            type="file"
            accept=".csv,.tsv,.txt"
            className="hidden"
            onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </div>

        {uploading && (
          <div className="flex items-center gap-2 text-sm text-blue-600">
            <span className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
            Uploading and analysing schema…
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        {/* Schema preview */}
        {hasSource && (
          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <FileText className="w-5 h-5 text-green-500" />
              <span className="font-medium text-gray-900">{project.source_filename}</span>
            </div>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500">Delimiter</p>
                <p className="font-mono font-semibold">{project.source_delimiter === '\t' ? 'TAB' : project.source_delimiter || 'auto'}</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500">Encoding</p>
                <p className="font-mono font-semibold">{project.source_encoding}</p>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500">Rows</p>
                <p className="font-semibold">{project.source_row_count.toLocaleString()}</p>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-2">Detected columns ({project.source_columns.length})</p>
              <div className="flex flex-wrap gap-1.5">
                {project.source_columns.map(col => (
                  <span key={col} className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded text-xs font-mono">
                    {col}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </WizardLayout>
  )
}
