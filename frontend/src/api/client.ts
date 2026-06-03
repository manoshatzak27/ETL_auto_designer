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

export const lookupConceptDomain = (conceptId: number) =>
  api.get(`/projects/concept-lookup/domain?concept_id=${conceptId}`).then(r => r.data as { concept_id: number; domain_id: string | null; found: boolean })

export const getSourcePreview = (projectId: string, rows = 5) =>
  api.get(`/projects/${projectId}/source-preview?rows=${rows}`).then(r => r.data)

export interface OutputPreview {
  columns: string[]
  rows: Record<string, string>[]
  total_rows: number
}

export const getOutputPreview = (projectId: string, filename: string, rows = 20) =>
  api
    .get<OutputPreview>(`/projects/${projectId}/output-preview?filename=${encodeURIComponent(filename)}&rows=${rows}`)
    .then(r => r.data)

export const detectColumnType = (projectId: string, column: string) =>
  api.get(`/projects/${projectId}/detect-column-type?column=${encodeURIComponent(column)}`).then(r => r.data as { column: string; transform: string })

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

export const conceptSearch = (
  projectId: string,
  query: string,
  topK = 20,
  useReranker = false,
) =>
  api
    .post(
      `/projects/${projectId}/concept-search?query=${encodeURIComponent(query)}` +
        `&top_k=${topK}&use_reranker=${useReranker ? 'true' : 'false'}`,
    )
    .then(r => r.data)

export const getApiHealth = () =>
  api.get<{ status: string; openai_configured: boolean }>('/health').then(r => r.data)

export const updateProjectSettings = (
  projectId: string,
  payload: { custom_vocabulary_id?: string; name?: string; description?: string },
) => api.patch(`/projects/${projectId}`, payload).then(r => r.data)

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

// ---- OMOP Postgres load ----
export interface ClinicalSchemaInfo {
  name: string
  ddl_applied: boolean
  person_rows: number
}

export interface DbHealth {
  configured: boolean
  connected: boolean
  ddl_applied: boolean
  schemas: string[]
  vocab_schema: string
  vocab_schema_ready: boolean
  vocab_rows: number
  clinical_schemas: ClinicalSchemaInfo[]
  error: string
}

export interface TableLoadStatus {
  table: string
  status: string
  rows: number
  elapsed: number
  error: string
}

export interface LoadStatus {
  project_id: string
  overall: string
  schema: string
  started_at: number
  finished_at: number
  log: string
  tables: TableLoadStatus[]
}

export interface VocabFileStatus {
  file: string
  table: string
  status: string
  rows: number
  elapsed: number
  error: string
}

export interface VocabLoadStatus {
  schema: string
  overall: string
  started_at: number
  finished_at: number
  log: string
  files: VocabFileStatus[]
}

export interface VocabBundleInfo {
  path: string
  exists: boolean
  detected_files: string[]
  total_size_bytes: number
}

export const getDbHealth = () => api.get<DbHealth>('/db-health').then(r => r.data)

export const loadDatabase = (
  projectId: string,
  payload: {
    schema_mode: 'shared' | 'project'
    schema_name?: string
    truncate: boolean
    apply_indices?: boolean
  },
) => api.post(`/projects/${projectId}/load-database`, payload).then(r => r.data)

export const getLoadStatus = (projectId: string) =>
  api.get<LoadStatus>(`/projects/${projectId}/load-status`).then(r => r.data)

export const loadVocabulary = (payload: { bundle_path: string }) =>
  api.post('/load-vocabulary', payload).then(r => r.data)

export const getVocabStatus = () =>
  api.get<VocabLoadStatus>('/vocab-status').then(r => r.data)

export const getVocabBundleInfo = (path = '/vocab') =>
  api
    .get<VocabBundleInfo>(`/vocab-bundle-info?path=${encodeURIComponent(path)}`)
    .then(r => r.data)

export default api
