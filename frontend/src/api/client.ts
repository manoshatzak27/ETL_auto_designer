import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// ---- Projects ----
export const listProjects = () => api.get('/projects/').then(r => r.data)
export const createProject = (name: string, description = '') =>
  api.post('/projects/', { name, description }).then(r => r.data)
export const getProject = (id: string) => api.get(`/projects/${id}`).then(r => r.data)
export const deleteProject = (id: string) => api.delete(`/projects/${id}`)

// ---- Source upload ----
export const uploadSource = (projectId: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post(`/projects/${projectId}/upload-source`, fd).then(r => r.data)
}

export const uploadMappingCsv = (projectId: string, mappingType: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api
    .post(`/projects/${projectId}/upload-mapping?mapping_type=${mappingType}`, fd)
    .then(r => r.data)
}

export const loadMappingsFromDir = (projectId: string, directory: string) =>
  api.post(`/projects/${projectId}/load-mappings-from-dir`, { directory }).then(r => r.data)

// ---- Concept mapping (Step 2) ----
export const getColumnValues = (projectId: string) =>
  api.get(`/projects/${projectId}/column-values`).then(r => r.data)

export const getConceptDecisions = (projectId: string) =>
  api.get(`/projects/${projectId}/concept-decisions`).then(r => r.data)

export const saveConceptDecisions = (projectId: string, decisions: Record<string, unknown>) =>
  api.post(`/projects/${projectId}/concept-decisions`, { decisions }).then(r => r.data)

export const generateMappingCsvs = (projectId: string) =>
  api.post(`/projects/${projectId}/generate-mapping-csvs`).then(r => r.data)

export const getSourcePreview = (projectId: string, rows = 5) =>
  api.get(`/projects/${projectId}/source-preview?rows=${rows}`).then(r => r.data)

// ---- ETL Config ----
export const updateTableConfig = (projectId: string, table: string, config: unknown) =>
  api.patch(`/projects/${projectId}/config`, { table, config }).then(r => r.data)

export const getTableConfig = (projectId: string, table: string) =>
  api.get(`/projects/${projectId}/config/${table}`).then(r => r.data)

// ---- Code generation ----
export const generateCode = (projectId: string) =>
  api.post(`/projects/${projectId}/generate`, {}).then(r => r.data)

export const generateTableScript = (projectId: string, table: string) =>
  api.post(`/projects/${projectId}/generate/${table}`).then(r => r.data)

export const conceptSearch = (projectId: string, query: string, topK = 20) =>
  api
    .post(`/projects/${projectId}/concept-search?query=${encodeURIComponent(query)}&top_k=${topK}`)
    .then(r => r.data)

// ---- Execution ----
export const executeProject = (projectId: string) =>
  api.post(`/projects/${projectId}/execute`).then(r => r.data)

export const getExecutionLog = (projectId: string) =>
  api.get(`/projects/${projectId}/execution-log`).then(r => r.data)

export const downloadOutput = (projectId: string, filename: string) => {
  window.open(`/api/projects/${projectId}/download/${filename}`, '_blank')
}

// ---- AI Chat ----
export const getChatHistory = (projectId: string) =>
  api.get(`/projects/${projectId}/chat`).then(r => r.data)

export const sendChatMessage = (projectId: string, message: string, table: string) =>
  api.post(`/projects/${projectId}/chat`, { message, table }).then(r => r.data)

export const clearChatHistory = (projectId: string) =>
  api.delete(`/projects/${projectId}/chat`).then(r => r.data)

export default api
