import { BrowserRouter, Routes, Route, useParams, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Step1Upload from './pages/wizard/Step1_Upload'
import PersonStep from './pages/wizard/Step2_Person'
import VisitStep from './pages/wizard/Step3_Visit'
import ObsPeriodStep from './pages/wizard/Step4_ObsPeriod'
import LocationStep from './pages/wizard/Step5_Location'
import CareSiteStep from './pages/wizard/Step6_CareSite'
import ProviderStep from './pages/wizard/Step7_Provider'
import ConceptMappingStep from './pages/wizard/Step9_ConceptMapping'
import StemTableStep from './pages/wizard/Step10_StemTable'
import DeathStep from './pages/wizard/Step8_Death'
import GenerateStep from './pages/wizard/Step11_Generate'
import ChatPanel from './components/ChatPanel'
import { getProject } from './api/client'
import type { Project } from './types'

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

  const update = (p: Project) => setProject(p)

  const stepEl = (() => {
    switch (step) {
      case '1':  return <Step1Upload project={project} onUpdate={update} />
      case '2':  return <PersonStep project={project} onUpdate={update} />
      case '3':  return <VisitStep project={project} onUpdate={update} />
      case '4':  return <ObsPeriodStep project={project} onUpdate={update} />
      case '5':  return <LocationStep project={project} onUpdate={update} />
      case '6':  return <CareSiteStep project={project} onUpdate={update} />
      case '7':  return <ProviderStep project={project} onUpdate={update} />
      case '8':  return <DeathStep project={project} onUpdate={update} />
      case '9':  return <ConceptMappingStep project={project} onUpdate={update} />
      case '10': return <StemTableStep project={project} onUpdate={update} />
      case '11': return <GenerateStep project={project} onUpdate={update} />
      default:  return <Navigate to={`/project/${projectId}/step/1`} replace />
    }
  })()

  return (
    <>
      {stepEl}
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
