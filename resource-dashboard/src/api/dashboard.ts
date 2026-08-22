import { mockDashboard } from '../data/mockDashboard'
import type { DashboardSnapshot, ResourceCenter, TrendDataset } from '../types/dashboard'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== 'false'

type RequestOptions = RequestInit & { timeout?: number }

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), options.timeout ?? 8000)

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { Accept: 'application/json', ...options.headers },
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Dashboard API ${response.status}: ${response.statusText}`)
    }

    return (await response.json()) as T
  } finally {
    window.clearTimeout(timeout)
  }
}

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

export const dashboardApi = {
  isMockMode: USE_MOCKS,

  async getSnapshot(): Promise<DashboardSnapshot> {
    if (USE_MOCKS) {
      await wait(180)
      return { ...mockDashboard, serverTime: new Date().toISOString() }
    }
    return request<DashboardSnapshot>('/dashboard/snapshot')
  },

  async getCenters(): Promise<ResourceCenter[]> {
    if (USE_MOCKS) return mockDashboard.centers
    return request<ResourceCenter[]>('/dashboard/centers')
  },

  async getTrend(metric: 'cpu' | 'memory' | 'disk', range = '12m'): Promise<TrendDataset> {
    if (USE_MOCKS) return mockDashboard.resourceTrends[metric]
    return request<TrendDataset>(`/dashboard/trends?metric=${metric}&range=${range}`)
  },
}
