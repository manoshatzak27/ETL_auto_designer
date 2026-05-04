export interface ProjectSummary {
  id: string
  name: string
  description: string
  created_at: string
  updated_at: string
  source_filename: string
  last_execution_status: string
}

export interface Project {
  id: string
  name: string
  description: string
  created_at: string
  updated_at: string
  source_filename: string
  source_delimiter: string
  source_encoding: string
  source_columns: string[]
  source_row_count: number
  etl_config: Record<string, unknown>
  generated_code: string
  generated_scripts: Record<string, string>
  last_execution_status: string
  output_files: string[]
  mapping_files: Record<string, string>
}

// ---- Per-table config shapes ----

export interface PersonConfig {
  enabled: boolean
  mappings: {
    person_id: FieldMapping
    gender_concept_id: FieldMappingWithValueMap
    year_of_birth: DateFieldMapping
    month_of_birth?: DateFieldMapping
    day_of_birth?: DateFieldMapping
    race_concept_id?: RaceEthnicityMapping | ConstantMapping
    ethnicity_concept_id?: RaceEthnicityMapping | ConstantMapping
    location_id?: RaceEthnicityMapping
    provider_id?: RaceEthnicityMapping
    care_site_id?: RaceEthnicityMapping
  }
  required_source_cols: string[]
}

export interface FieldMapping {
  source_col: string
  transform?: string
  auto_increment?: boolean
}

export interface FieldMappingWithValueMap extends FieldMapping {
  value_map: Record<string, number>
  default?: number
}

export interface DateFieldMapping extends FieldMapping {
  date_format: string
  transform: 'date_year' | 'date_month' | 'date_day' | 'date_full'
}

export interface ConstantMapping {
  constant: number
}

export interface RaceEthnicityMapping {
  source_col: string
  value_map: Record<string, number>
  default: number
}

export interface VisitDefinition {
  label: string
  date_col: string
  visit_concept_id: number
  type_concept_id: number
  source_value: string
  optional: boolean
}

export interface VisitOccurrenceConfig {
  enabled: boolean
  visit_definitions: VisitDefinition[]
}

export interface ObservationPeriodConfig {
  enabled: boolean
  start_date_col: string
  end_date_col: string
  end_date_fallback: 'start_date' | 'today'
  period_type_concept_id: number
}

export interface StemTableOverride {
  variable: string
  field?: string
  value?: number
  value_as_string?: string
  value_map?: Record<string, Record<string, unknown>>
}

export interface StemTableConfig {
  enabled: boolean
  variable_groups: Record<string, string[]>
  concept_mapping_csvs: {
    variable_mapping?: string
    value_mapping?: string
    variable_value_mapping?: string
  }
  special_overrides: StemTableOverride[]
}

export interface DeathConfig {
  enabled: boolean
  filter_col: string
  filter_value: string
  date_method: 'onset_plus_years' | 'direct_col'
  onset_col?: string
  years_offset?: number
  date_col?: string
  death_type_concept_id: number
}

export interface ConceptLink {
  concept_name: string
  concept_code: string
  concept_id: number
  domain: string
  vocabulary_id: string
  score: number
  justification?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  table?: string
  code_updated?: boolean
}
