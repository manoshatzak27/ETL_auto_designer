import { BrowserRouter, Routes, Route, useParams, Navigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { GenerationProvider } from './context/GenerationContext'
import { StepFileSelectionContext } from './contexts/StepFileSelectionContext'
import Dashboard from './pages/Dashboard'
import SourceStep from './pages/wizard/SourceStep'
import LocationStep from './pages/wizard/LocationStep'
import CareSiteStep from './pages/wizard/CareSiteStep'
import ProviderStep from './pages/wizard/ProviderStep'
import PersonStep from './pages/wizard/PersonStep'
import VisitStep from './pages/wizard/VisitStep'
import ObsPeriodStep from './pages/wizard/ObsPeriodStep'
import ConceptsStep from './pages/wizard/ConceptsStep'
import StemTableStep from './pages/wizard/StemTableStep'
import DeathStep from './pages/wizard/DeathStep'
import FinalizeStep from './pages/wizard/FinalizeStep'
import ChatPanel from './components/ChatPanel'
import { getProject } from './api/client'
import type { Project } from './types'
import { LEGACY_NUMERIC_SLUGS, type WizardSlug } from './wizard/steps'

// Map slugs → step components. Adding a new step? Register here and in
// frontend/src/wizard/steps.ts (the ALL_STEPS registry).
const STEP_COMPONENTS: Record<WizardSlug, React.ComponentType<{ project: Project; onUpdate: (p: Project) => void }>> = {
  source:       SourceStep,
  location:     LocationStep,
  'care-site':  CareSiteStep,
  provider:     ProviderStep,
  person:       PersonStep,
  visit:        VisitStep,
  'obs-period': ObsPeriodStep,
  death:        DeathStep,
  concepts:     ConceptsStep,
  'stem-table': StemTableStep,
  finalize:     FinalizeStep,
}

function ProjectWizard() {
  const { projectId, step } = useParams<{ projectId: string; step: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const fileSelections = useRef({ indices: {} as Record<string, number>, configs: {} as Record<string, Record<string, unknown>> })

  useEffect(() => {
    if (!projectId) return
    getProject(projectId)
      .then(setProject)
      .finally(() => setLoading(false))
  }, [projectId])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <span className="w-8 h-8 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin" />
      </div>
    )
  }

  if (!project) {
    return <Navigate to="/" replace />
  }

  // Legacy numeric URLs (/step/1 … /step/12) redirect to their slug equivalent.
  if (step && step in LEGACY_NUMERIC_SLUGS) {
    return <Navigate to={`/project/${projectId}/step/${LEGACY_NUMERIC_SLUGS[step]}`} replace />
  }

  const Comp = step ? STEP_COMPONENTS[step as WizardSlug] : undefined
  if (!Comp) {
    return <Navigate to={`/project/${projectId}/step/source`} replace />
  }

  const update = (p: Project) => setProject(p)

  return (
    <StepFileSelectionContext.Provider value={fileSelections}>
      <GenerationProvider>
        <Comp project={project} onUpdate={update} />
        <ChatPanel project={project} onUpdate={update} />
      </GenerationProvider>
    </StepFileSelectionContext.Provider>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/project/:projectId/step/:step" element={<ProjectWizard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
