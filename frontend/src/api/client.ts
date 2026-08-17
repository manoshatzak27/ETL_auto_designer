import axios from 'axios'
import type { SourceFileContent, Project } from '../types'

const api = axios.create({ baseURL: '/api' })

// ---- Projects ----
export const listProjects = () => api.get('/projects/').then(r => r.data)
export const createProject = (name: string, description = '') =>
  api.post('/projects/', { name, description }).then(r => r.data)
export const getProject = (id: string) => api.get(`/projects/${id}`).then(r => r.data)
export const deleteProject = (id: string) => api.delete(`/projects/${id}`)
export const copyProject = (id: string) => api.post(`/projects/${id}/copy`).then(r => r.data)

// ---- Source upload ----
export const uploadSource = (projectId: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return api.post(`/projects/${projectId}/upload-source`, fd).then(r => r.data)
}

export interface UploadConflict {
  column: string
  reason: string
}

export interface UploadSourcesResult {
  project: Project
  conflicts: UploadConflict[]
}

export const uploadSources = (projectId: string, files: File[], keepMappings = true) => {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  fd.append('keep_mappings', String(keepMappings))
  return api.post<UploadSourcesResult>(`/projects/${projectId}/upload-sources`, fd).then(r => r.data)
}

export const deleteSourceFile = (projectId: string, index: number) =>
  api.delete(`/projects/${projectId}/source-files/${index}`).then(r => r.data)

export const downloadSourceFile = (projectId: string, filename: string) => {
  window.open(`/api/projects/${projectId}/source-files/${encodeURIComponent(filename)}/download`, '_blank')
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

// ---- Concept mapping (Concepts step) ----
export const getColumnValues = (projectId: string, filename?: string) =>
  api.get(`/projects/${projectId}/column-values`, { params: { ...(filename ? { filename } : {}) } }).then(r => r.data)

export const getConceptDecisions = (projectId: string) =>
  api.get(`/projects/${projectId}/concept-decisions`).then(r => r.data)

export const saveConceptDecisions = (projectId: string, decisions: Record<string, unknown>) =>
  api.post(`/projects/${projectId}/concept-decisions`, { decisions }).then(r => r.data)

export const generateMappingCsvs = (projectId: string) =>
  api.post(`/projects/${projectId}/generate-mapping-csvs`).then(r => r.data)

export const downloadMappingFiles = (projectId: string) => {
  window.open(`/api/projects/${projectId}/download-mapping-files`, '_blank')
}

export const downloadMappingSummary = (projectId: string) => {
  window.open(`/api/projects/${projectId}/download-mapping-summary`, '_blank')
}

export const lookupConceptDomain = (conceptId: number) =>
  api.get(`/projects/concept-lookup/domain?concept_id=${conceptId}`).then(r => r.data as { concept_id: number; domain_id: string | null; concept_name: string | null; found: boolean })

export const getSourcePreview = (projectId: string, rows = 5) =>
  api.get(`/projects/${projectId}/source-preview?rows=${rows}`).then(r => r.data)

export const getSourceFileContent = (projectId: string, filename: string, rows?: number) =>
  api
    .get<SourceFileContent>(`/projects/${projectId}/source-file-content`, { params: { filename, ...(rows ? { rows } : {}) } })
    .then(r => r.data)

export const updateSourceFileContent = (projectId: string, filename: string, columns: string[], rows: Record<string, string>[]) =>
  api
    .put(`/projects/${projectId}/source-file-content`, { columns, rows }, { params: { filename } })
    .then(r => r.data)

export interface OutputPreview {
  columns: string[]
  rows: Record<string, string>[]
  total_rows: number
}

export const getOutputPreview = (projectId: string, filename: string, rows = 20) =>
  api
    .get<OutputPreview>(`/projects/${projectId}/output-preview?filename=${encodeURIComponent(filename)}&rows=${rows}`)
    .then(r => r.data)

export const detectColumnType = (projectId: string, column: string, filename?: string) =>
  api.get(`/projects/${projectId}/detect-column-type`, { params: { column, ...(filename ? { filename } : {}) } }).then(r => r.data as { column: string; transform: string })

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
export const executeProject = (projectId: string, outputMode: 'basic' | 'detailed' = 'basic') =>
  api.post(`/projects/${projectId}/execute`, { output_mode: outputMode }).then(r => r.data)

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
  started_at: number
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
