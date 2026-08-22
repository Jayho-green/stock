export type ResourceMetric = {
  key: string
  label: string
  value: number
  unit: string
}

export type OperationSummary = {
  key: string
  label: string
  value: number
}

export type ApplicationUsage = {
  id: string
  name: string
  value: number
}

export type TrendSeries = {
  name: string
  values: number[]
  color?: string
}

export type TrendDataset = {
  categories: string[]
  series: TrendSeries[]
}

export type CenterResource = {
  label: string
  used: string
  capacity: string
  rate: number
}

export type ResourceCenter = {
  id: string
  name: string
  cityName: string
  value: number
  coordinate: [number, number]
  tone: 'cyan' | 'amber'
  resources: CenterResource[]
}

export type DashboardSnapshot = {
  serverTime: string
  resourceMetrics: ResourceMetric[]
  operationSummary: OperationSummary[]
  quotaRanking: ApplicationUsage[]
  cpuRanking: ApplicationUsage[]
  memoryRanking: ApplicationUsage[]
  applicationTrend: TrendDataset
  resourceTrends: Record<'cpu' | 'memory' | 'disk', TrendDataset>
  centers: ResourceCenter[]
  cityValues: Record<string, number>
}

export type DashboardApiStatus = 'loading' | 'live' | 'mock' | 'error'
