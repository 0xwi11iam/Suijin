export interface KbSourceMap { [name: string]: number }

export interface KbInfo {
  built: boolean
  docs?: number
  sources?: KbSourceMap
  failed?: Record<string, string>
  age_days?: number | null
  built_at?: string
  error?: string
}

export interface LabInfo { name: string; port: number | null; running: boolean }

export interface AuditSummary {
  name: string
  engagement: string
  started: string
  actions: number
  success: number
  failed: number
  findings: number
  cost_usd: number
}

export interface ReportFile { name: string; size: number; mtime: number }

export interface TrafficEntry {
  [k: string]: unknown
  method?: string
  path?: string
  ip?: string
  timestamp?: string
}

export interface KgNode { id: string; type: string; data: Record<string, unknown> }
export interface KgEdge { from: string; to: string; rel?: string; [k: string]: unknown }

export interface BlueKg {
  node_counts: Record<string, number>
  attack_type_counts: Record<string, number>
  nodes: KgNode[]
  edges: KgEdge[]
}

export interface Snapshot {
  ts: number
  version: string
  provider: { name: string; model: string; zai_endpoint: string | null }
  kb: KbInfo
  tools: { module_tool_count: number; missing: Record<string, string[]> }
  labs: LabInfo[]
  tarpit: Record<string, { delay?: number; [k: string]: unknown }>
  traffic_count: number
  traffic_recent: TrafficEntry[]
  signal_counts: Record<string, number>
  blue_kg: BlueKg | null
  red_kg: Record<string, Record<string, unknown>>
  reports: ReportFile[]
  sessions_count: number
  audits: AuditSummary[]
  error?: string
}
