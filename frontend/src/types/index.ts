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
  end_date_col?: string
  // visit_concept_id: fixed concept or default when column mapping is used
  visit_concept_id: number
  visit_concept_source_col?: string
  visit_concept_value_map?: Record<string, number>
  // type_concept_id: fixed concept or default when column mapping is used
  type_concept_id: number
  visit_type_source_col?: string
  visit_type_value_map?: Record<string, number>
  // visit_source_value: static text or derived from source column
  source_value: string
  visit_source_col?: string
  optional: boolean
  // inpatient fields
  admitted_from_concept_id?: number
  admitted_from_source_col?: string
  admitted_from_value_map?: Record<string, number>
  admitted_from_source_value?: string
  discharged_to_concept_id?: number
  discharged_to_source_col?: string
  discharged_to_value_map?: Record<string, number>
  discharged_to_source_value?: string
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

export interface LocationConfig {
  enabled: boolean
  // Person address columns
  address_1_col: string
  address_2_col: string
  city_col: string
  state_col: string
  zip_col: string
  county_col: string
  country_source_value: string
  // Care site address columns
  cs_address_1_col: string
  cs_address_2_col: string
  cs_city_col: string
  cs_state_col: string
  cs_zip_col: string
  cs_county_col: string
  cs_country_source_value: string
  cs_latitude_col: string
  cs_longitude_col: string
  // Person country config
  country_concept_id_map: Record<string, number>
  country_concept_id_default: number
  // Care site country config
  cs_country_concept_id_default: number
}

export interface CareSiteConfig {
  enabled: boolean
  care_site_name_col: string
  place_of_service_concept_id: number | null
  care_site_source_value_col: string
  place_of_service_source_value_col: string
}

export interface ProviderConfig {
  enabled: boolean
  provider_name_col: string
  npi_col: string
  dea_col: string
  specialty_concept_id: number | null
  specialty_concept_value_map?: Record<string, number>
  care_site_source_value_col: string
  year_of_birth_col: string
  gender_concept_value_map?: Record<string, number>
  gender_concept_id_default?: number
  provider_source_value_col: string
  specialty_source_value_col: string
  gender_source_value_col: string
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
  death_date_col: string
  death_datetime_col: string
  death_type_concept_id: number
  cause_concept_id: number | null
  cause_source_value_col: string
  cause_source_concept_id: number | null
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
