import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { listProjects, createProject, deleteProject } from '../api/client'
import type { ProjectSummary } from '../types'
import {
  Plus,
  Trash2,
  ChevronRight,
  Database,
  Clock,
  Search,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'

export default function Dashboard() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  const [showForm, setShowForm] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [nameError, setNameError] = useState('')

  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = () => {
    setLoading(true)
    listProjects()
      .then(setProjects)
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return projects
    return projects.filter(p => p.name.toLowerCase().includes(q))
  }, [projects, query])

  const openCreate = () => {
    setNewName('')
    setNameError('')
    setShowForm(true)
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    setNameError('')
    try {
      const p = await createProject(newName.trim())
      setShowForm(false)
      navigate(`/project/${p.id}/step/1`)
    } catch (err: any) {
      if (err?.response?.status === 409) {
        setNameError('This project name is already taken')
      } else {
        setNameError('Failed to create project')
      }
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteProject(deleteTarget.id)
      setDeleteTarget(null)
      load()
    } finally {
      setDeleting(false)
    }
  }

  const statusVariant = (s: string): 'success' | 'destructive' | 'muted' => {
    if (s === 'success') return 'success'
    if (s === 'error') return 'destructive'
    return 'muted'
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-background via-background to-secondary/60">
      {/* Header */}
      <header className="border-b border-border bg-card/60 px-8 py-5">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Database className="size-5" />
            </div>
            <div>
              <h1 className="text-3xl font-bold tracking-tight text-foreground">
                <span className="text-primary">OMOP</span> ETL Designer
              </h1>
              <p className="text-sm text-muted-foreground">
                Code-less ETL builder with AI-powered code generation
              </p>
            </div>
          </div>
          <Button onClick={openCreate}>
            <Plus /> New Project
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-8 py-8">
        {/* Toolbar */}
        <div className="mb-6 flex items-center gap-3">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search projects…"
              className="pl-9"
            />
          </div>
          <Badge variant="muted" className="px-3 py-1.5 text-sm">
            {filtered.length} {filtered.length === 1 ? 'project' : 'projects'}
          </Badge>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="mr-3 size-5 animate-spin" />
            Loading projects…
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="flex flex-col items-center py-20 text-center">
            <div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-secondary">
              <Database className="size-6 text-primary" />
            </div>
            <h2 className="text-lg font-semibold text-foreground">
              {query ? 'No matching projects' : 'No projects yet'}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {query
                ? 'Try a different search term'
                : 'Create a new project to start building your OMOP ETL'}
            </p>
            {!query && (
              <Button onClick={openCreate} className="mt-5">
                <Plus /> Create First Project
              </Button>
            )}
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div className="grid gap-3">
            {filtered.map(p => (
              <Card
                key={p.id}
                onClick={() => navigate(`/project/${p.id}/step/1`)}
                className="group flex cursor-pointer items-center gap-4 px-5 py-4 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-lg"
              >
                <div className="flex size-10 flex-shrink-0 items-center justify-center rounded-lg bg-secondary">
                  <Database className="size-5 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate text-lg font-bold text-primary">
                      {p.name}
                    </h3>
                    {p.last_execution_status && (
                      <Badge variant={statusVariant(p.last_execution_status)}>
                        {p.last_execution_status}
                      </Badge>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-4">
                    {p.source_filename && (
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {p.source_filename}
                      </span>
                    )}
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="size-3" />
                      {new Date(p.updated_at + 'Z').toLocaleDateString()} {new Date(p.updated_at + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={e => {
                      e.stopPropagation()
                      setDeleteTarget(p)
                    }}
                    className="text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-100"
                  >
                    <Trash2 />
                  </Button>
                  <ChevronRight className="size-4 text-muted-foreground transition-colors group-hover:text-primary" />
                </div>
              </Card>
            ))}
          </div>
        )}
      </main>

      {/* Create project dialog */}
      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="text-xl font-bold text-primary">New Project</DialogTitle>
            <DialogDescription>
              Give your ETL project a name to get started.
            </DialogDescription>
          </DialogHeader>
          <div>
            <Input
              autoFocus
              value={newName}
              onChange={e => {
                setNewName(e.target.value)
                setNameError('')
              }}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              placeholder="Project name…"
              className={nameError ? 'border-destructive focus-visible:ring-destructive' : ''}
            />
            {nameError && (
              <p className="mt-1.5 text-xs text-destructive">{nameError}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={creating || !newName.trim()}>
              {creating && <Loader2 className="animate-spin" />}
              {creating ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation dialog */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={open => !open && setDeleteTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete project</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete{' '}
              <span className="font-medium text-foreground">
                {deleteTarget?.name}
              </span>
              ? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting && <Loader2 className="animate-spin" />}
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
