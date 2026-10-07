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
  api.get(`/projects/concept-lookup/domain?concept_id=${conceptId}`).then(r => r.data as { concept_id: number; domain_id: string | null; concept_name: string | null; standard_concept: string | null; invalid_reason: string | null; found: boolean; vocab_available: boolean })

const INVALID_REASON_LABEL: Record<string, string> = { D: 'deleted', U: 'upgraded (replaced by another concept)' }

// Human-readable form of an OMOP invalid_reason ('D' / 'U').
export const invalidReasonLabel = (reason: string) => INVALID_REASON_LABEL[reason] ?? `invalid_reason '${reason}'`

// Gate for the manual "Set" buttons: a typed concept id is only accepted when it
// exists in the loaded vocabulary, is valid (invalid_reason null) and is standard.
// Returns an error message to show (and block the set), or null when it's fine.
export const standardConceptError = (
  conceptId: number,
  res: { found: boolean; standard_concept: string | null; invalid_reason: string | null; vocab_available: boolean },
): string | null => {
  if (!res.vocab_available) return "Couldn't verify this concept — load the OMOP vocabulary first, then try again."
  if (!res.found) return `Concept ${conceptId} doesn't exist in the loaded vocabulary.`
  if (res.invalid_reason) return `Concept ${conceptId} is invalid — ${invalidReasonLabel(res.invalid_reason)}.`
  if (res.standard_concept !== 'S') return `Concept ${conceptId} is not a standard concept — use its standard equivalent.`
  return null
}

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

export const getGenerateProgress = (projectId: string, table: string) =>
  api.get(`/projects/${projectId}/generate/${table}/progress`)
    .then(r => r.data as { active: boolean; used: number; limit: number; content: string })

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

// ---- Column descriptions (the project's data dictionary) ----

export interface DescriptionUploadResult {
  descriptions: Record<string, string>
  matched: string[]
  unmatched: { name: string; description: string; table: string }[]
  headers: { name: string; description: string; table: string | null }
  missing: string[]
}

export const getColumnDescriptions = (projectId: string) =>
  api
    .get<{ descriptions: Record<string, string> }>(`/projects/${projectId}/column-descriptions`)
    .then(r => r.data.descriptions)

export const putColumnDescriptions = (projectId: string, descriptions: Record<string, string>) =>
  api
    .put<{ descriptions: Record<string, string> }>(
      `/projects/${projectId}/column-descriptions`, { descriptions },
    )
    .then(r => r.data.descriptions)

export const uploadColumnDescriptions = (projectId: string, file: File, replace = false) => {
  const fd = new FormData()
  fd.append('file', file)
  return api
    .post<DescriptionUploadResult>(
      `/projects/${projectId}/column-descriptions/upload?replace=${replace ? 'true' : 'false'}`, fd,
    )
    .then(r => r.data)
}

export const downloadDescriptionsTemplate = (projectId: string) => {
  window.open(`/api/projects/${projectId}/column-descriptions/template`, '_blank')
}

// ---- Bulk concept matching (staged OMOP matching pipeline) ----

export interface ConceptMatchRequestColumn {
  name: string
  description?: string | null
  table?: string | null
}

/** One of the pipeline's ranked alternatives, best first. */
export interface ConceptMatchCandidate {
  concept_id: number
  concept_name: string
  domain_id: string | null
  vocabulary_id: string | null
  concept_class_id: string | null
  concept_code: string | null
  score: number
}

export interface ConceptMatchResult {
  column_name: string
  source_table: string | null
  status: 'auto_accept' | 'review' | 'unmapped' | 'manual' | 'error'
  confidence: number
  concept_id: number | null
  concept_name: string | null
  domain_id: string | null
  vocabulary_id: string | null
  concept_class_id: string | null
  concept_code: string | null
  decision_reason: string
  ambiguous: boolean
  error: string | null
  candidates: ConceptMatchCandidate[]
}

export const getConceptMatcherHealth = () =>
  api
    .get<{ available: boolean; detail?: string | null; vocabulary_version?: string; concepts?: number; embeddings?: boolean }>(
      '/projects/concept-matcher/health',
    )
    .then(r => r.data)

export const matchConcepts = (projectId: string, columns: ConceptMatchRequestColumn[]) =>
  api
    .post<{ run_id: number; results: ConceptMatchResult[] }>(
      `/projects/${projectId}/match-concepts`,
      { columns },
    )
    .then(r => r.data)

/** One column's value-level run: every distinct value matched, all of them
 *  forced into the single domain the first pass voted for. */
export interface ConceptValueMatchColumn {
  column_name: string
  source_table: string | null
  values_requested: number
  /** The OMOP domain every result below is restricted to, or null when the first
   *  pass found no concept at all. */
  domain: string | null
  /** How many values voted for each domain in the first pass. */
  domain_votes: Record<string, number>
  /** How many values were searched again because the first pass put them outside
   *  the chosen domain. */
  rematched: number
  /** Keyed by the source value. */
  results: Record<string, ConceptMatchResult>
}

export const matchColumnValues = (projectId: string, columns: ConceptMatchRequestColumn[]) =>
  api
    .post<{ columns: ConceptValueMatchColumn[] }>(
      `/projects/${projectId}/match-values`,
      { columns },
    )
    .then(r => r.data)

/** A concept suggested for one source value within a known domain. `source` is
 *  "rule" when the backend's normalizer resolved it without the matcher. */
export interface ValueConceptSuggestion {
  value: string
  /** What was actually matched, after normalization ("greece" → "Greek"). */
  term: string
  source: 'rule' | 'matcher'
  status: ConceptMatchResult['status']
  confidence: number
  concept_id: number | null
  concept_name: string | null
  /** Race only: the narrower concept matched before rolling up to its
   *  top-level category (e.g. Black 38003598 → 8516). */
  detailed?: { concept_id: number; concept_name: string | null }
  candidates: ConceptMatchCandidate[]
}

/** Narrows a domain to the vocabularies / concept classes a field wants, e.g.
 *  visit_concept_id → Visit, place of service → CMS Place of Service. */
export interface ConceptPreference {
  vocabularies?: string[]
  concept_classes?: string[]
}

export const suggestValueConcepts = (projectId: string, domain: string, values: string[], prefer?: ConceptPreference) =>
  api
    .post<{ domain: string; results: Record<string, ValueConceptSuggestion> }>(
      `/projects/${projectId}/suggest-value-concepts`,
      { domain, values, ...(prefer ? { prefer } : {}) },
    )
    .then(r => r.data.results)

/** A search result, in display order: trees of broader → narrower concepts
 *  (`depth` 0 at the top, `parent_id` the row it sits under, `is_parent` when
 *  rows sit under it), then the results with no relatives among them. */
export interface DomainSearchResult extends Omit<ConceptMatchCandidate, 'score'> {
  score: number | null
  depth?: number
  is_parent?: boolean
  parent_id?: number
}

export const searchDomainConcepts = (projectId: string, query: string, domain: string, limit = 15) =>
  api
    .get<{ term: string; results: DomainSearchResult[] }>(
      `/projects/${projectId}/search-concepts`,
      { params: { query, domain, limit } },
    )
    .then(r => r.data)

// ---- Column auto-match (table steps) ----

/** Whether the AI fallback of column auto-match can be used. */
export interface LlmStatus {
  status: 'ready' | 'not_configured' | 'error'
  model: string
  detail: string | null
}

export const getColumnMatcherHealth = (refresh = false) =>
  api
    .get<{ llm: LlmStatus }>('/projects/column-matcher/health', { params: refresh ? { refresh: 1 } : {} })
    .then(r => r.data.llm)

/** The source column picked for one step field. `auto` matches are confident
 *  enough to fill in; `suggest` ones are offered for the user to accept. */
export interface ColumnMatch {
  column: string | null
  filename: string | null
  score: number
  status: 'auto' | 'suggest' | 'none'
  source: 'heuristic' | 'llm'
  reason: string
  alternatives: { column: string; filename: string; score: number }[]
  /** Date fields: the format to set the step's date format to when applying
   *  this match, or null to leave it as it is. */
  date_format: string | null
  /** Date fields: the format the column's values are written in. */
  detected_format: string | null
  /** Why a match was only suggested, or something to check about it. */
  warning: string | null
}

export interface ColumnMatchField {
  /** Unique within the request, e.g. "city_col" or "1:date_col". */
  key: string
  /** The backend field spec to match against, when it differs from `key`. */
  spec?: string
  /** What tells this field apart from its siblings, e.g. a visit's label. */
  hint?: string
  /** Files to look in for this field, when they differ from the request's. */
  filenames?: string[]
  /** Date fields: the step's current date format… */
  date_format?: string
  /** …the name shared by the fields that use that one format… */
  format_group?: string
  /** …and whether a column already mapped in the step relies on it. */
  format_locked?: boolean
}

export const suggestColumnMapping = (
  projectId: string,
  body: { table: string; filenames: string[]; fields: ColumnMatchField[]; exclude_columns: string[]; use_llm: boolean },
) =>
  api
    .post<{ matches: Record<string, ColumnMatch>; llm_available: boolean; llm_used: boolean; llm_error: string | null }>(
      `/projects/${projectId}/suggest-column-mapping`,
      body,
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
