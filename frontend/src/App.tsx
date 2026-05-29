import { BrowserRouter, Routes, Route, useParams, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Step1Upload from './pages/wizard/Step1_Upload'
import LocationStep from './pages/wizard/Step2_Location'
import CareSiteStep from './pages/wizard/Step3_CareSite'
import ProviderStep from './pages/wizard/Step4_Provider'
import PersonStep from './pages/wizard/Step5_Person'
import VisitStep from './pages/wizard/Step6_Visit'
import ObsPeriodStep from './pages/wizard/Step7_ObsPeriod'
import ConceptMappingStep from './pages/wizard/Step9_ConceptMapping'
import StemTableStep from './pages/wizard/Step10_StemTable'
import DeathStep from './pages/wizard/Step8_Death'
import StepFinalize from './pages/wizard/StepFinalize'
import ChatPanel from './components/ChatPanel'
import { getProject } from './api/client'
import type { Project } from './types'
import { LEGACY_NUMERIC_SLUGS, type WizardSlug } from './wizard/steps'

// Map slugs → step components. Adding a new step? Register here and in
// frontend/src/wizard/steps.ts (the ALL_STEPS registry).
const STEP_COMPONENTS: Record<WizardSlug, React.ComponentType<{ project: Project; onUpdate: (p: Project) => void }>> = {
  source:       Step1Upload,
  location:     LocationStep,
  'care-site':  CareSiteStep,
  provider:     ProviderStep,
  person:       PersonStep,
  visit:        VisitStep,
  'obs-period': ObsPeriodStep,
  death:        DeathStep,
  concepts:     ConceptMappingStep,
  'stem-table': StemTableStep,
  finalize:     StepFinalize,
}

function ProjectWizard() {
  const { projectId, step } = useParams<{ projectId: string; step: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)

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
    <>
      <Comp project={project} onUpdate={update} />
      <ChatPanel project={project} onUpdate={update} />
    </>
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
