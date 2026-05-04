import { BrowserRouter, Routes, Route, useParams, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Step1Upload from './pages/wizard/Step1_Upload'
import Step2ConceptMapping from './pages/wizard/Step2_ConceptMapping'
import Step3Person from './pages/wizard/Step2_Person'
import Step4Visit from './pages/wizard/Step3_Visit'
import Step5ObsPeriod from './pages/wizard/Step4_ObsPeriod'
import Step6StemTable from './pages/wizard/Step5_StemTable'
import Step7Death from './pages/wizard/Step6_Death'
import Step8Generate from './pages/wizard/Step7_Generate'
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
      case '1': return <Step1Upload project={project} onUpdate={update} />
      case '2': return <Step2ConceptMapping project={project} onUpdate={update} />
      case '3': return <Step3Person project={project} onUpdate={update} />
      case '4': return <Step4Visit project={project} onUpdate={update} />
      case '5': return <Step5ObsPeriod project={project} onUpdate={update} />
      case '6': return <Step6StemTable project={project} onUpdate={update} />
      case '7': return <Step7Death project={project} onUpdate={update} />
      case '8': return <Step8Generate project={project} onUpdate={update} />
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
