export type Preset = 'unfiltered' | 'italian_preferred' | 'italian_only' | 'custom'

export interface ApplicationUpdate {
  update_available: boolean
  latest_version: string | null
  release_url: string | null
}
export type RuleField = 'title' | 'provider' | 'size' | 'seeders'
export type RuleOperator = 'contains' | 'not_contains' | 'equals' | 'gte' | 'lte'
export type RuleAction = 'score' | 'exclude'

export interface CustomRule {
  enabled: boolean
  field: RuleField
  operator: RuleOperator
  value: string | number
  action: RuleAction
  score?: number
}

export interface ResultProcessing {
  preset: Preset
  custom_rules: CustomRule[]
  subtitle_language_correction: boolean
}

export interface PublicSettings {
  schema_version: 1
  database_update: { enabled: boolean; interval_seconds: number }
  result_processing: ResultProcessing
  prowlarr: {
    url: string
    indexer_url: string
    api_key_configured: boolean
    api_key?: string
  }
}

export interface ProwlarrStatus {
  configured: boolean
  connected: boolean | null
  indexer_installed: boolean | null
  error: string | null
}

export interface AppStatus {
  api_version: number
  application_version: string
  database: { connected: boolean }
  updater: {
    installed_version: string | null
    latest_version: string | null
    enabled: boolean
    updating: boolean
    phase?:
      | 'idle'
      | 'checking'
      | 'downloading'
      | 'validating'
      | 'restoring'
      | 'switching'
      | 'up_to_date'
      | 'updated'
      | 'failed'
    maintenance: boolean
    last_check: string | null
    next_check: string | null
    last_error: string | null
  }
  result_processing: { preset: Preset; custom_rule_count: number }
  prowlarr: ProwlarrStatus
}

export interface IndexerResult {
  created: boolean
  already_installed: boolean
  indexer_id: number | null
}
