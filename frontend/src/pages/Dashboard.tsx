import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { listProjects, createProject, deleteProject } from '../api/client'
import type { ProjectSummary } from '../types'
import { Plus, Trash2, ChevronRight, Database, Clock } from 'lucide-react'
import clsx from 'clsx'

export default function Dashboard() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [showForm, setShowForm] = useState(false)

  const load = () => {
    setLoading(true)
    listProjects()
      .then(setProjects)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    const p = await createProject(newName.trim())
    setCreating(false)
    setNewName('')
    setShowForm(false)
    navigate(`/project/${p.id}/step/1`)
  }

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (!confirm('Delete this project?')) return
    await deleteProject(id)
    load()
  }

  const statusColor = (s: string) => {
    if (s === 'success') return 'text-green-600 bg-green-50'
    if (s === 'error') return 'text-red-600 bg-red-50'
    return 'text-gray-500 bg-gray-100'
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-8 py-5">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">OMOP ETL Designer</h1>
            <p className="text-sm text-gray-500 mt-0.5">Code-less ETL builder with AI-powered code generation</p>
          </div>
          <button
            onClick={() => setShowForm(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" /> New Project
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-8 py-8">
        {/* New project form */}
        {showForm && (
          <div className="bg-white border border-blue-200 rounded-xl p-5 mb-6 flex items-center gap-3">
            <input
              autoFocus
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="Project name…"
              className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={handleCreate}
              disabled={creating || !newName.trim()}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => setShowForm(false)} className="text-sm text-gray-400 hover:text-gray-600">Cancel</button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-20 text-gray-400">
            <span className="w-6 h-6 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin mr-3" />
            Loading projects…
          </div>
        )}

        {!loading && projects.length === 0 && (
          <div className="text-center py-20">
            <Database className="w-12 h-12 text-gray-300 mx-auto mb-4" />
            <h2 className="text-lg font-medium text-gray-600">No projects yet</h2>
            <p className="text-sm text-gray-400 mt-1">Create a new project to start building your OMOP ETL</p>
            <button
              onClick={() => setShowForm(true)}
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
            >
              Create First Project
            </button>
          </div>
        )}

        <div className="grid gap-4">
          {projects.map(p => (
            <div
              key={p.id}
              onClick={() => navigate(`/project/${p.id}/step/1`)}
              className="bg-white border border-gray-200 rounded-xl p-5 flex items-center gap-4 cursor-pointer hover:border-blue-300 hover:shadow-sm transition-all group"
            >
              <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center flex-shrink-0">
                <Database className="w-5 h-5 text-blue-500" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-gray-900 truncate">{p.name}</h3>
                  {p.last_execution_status && (
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', statusColor(p.last_execution_status))}>
                      {p.last_execution_status}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-4 mt-1">
                  {p.source_filename && (
                    <span className="text-xs text-gray-500 font-mono truncate max-w-xs">{p.source_filename}</span>
                  )}
                  <span className="flex items-center gap-1 text-xs text-gray-400">
                    <Clock className="w-3 h-3" />
                    {new Date(p.updated_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={e => handleDelete(e, p.id)}
                  className="p-1.5 text-gray-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
                <ChevronRight className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
