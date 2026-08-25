import { useCallback, useEffect, useMemo, useState } from 'react'
import { dashboardApi } from './api/dashboard'
import { BubbleUsageChart } from './components/BubbleUsageChart'
import { CpuRankingChart } from './components/CpuRankingChart'
import { Header } from './components/Header'
import { MetricStrip } from './components/MetricStrip'
import { Panel } from './components/Panel'
import { QuotaUsage } from './components/QuotaUsage'
import { ResourceTable } from './components/ResourceTable'
import { ShandongMap } from './components/ShandongMap'
import { TrendChart } from './components/TrendChart'
import { mockDashboard } from './data/mockDashboard'
import type { DashboardApiStatus, DashboardSnapshot } from './types/dashboard'

type TrendMetric = 'cpu' | 'memory' | 'disk'

const trendTabs: Array<{ key: TrendMetric; label: string }> = [
  { key: 'cpu', label: 'CPU' },
  { key: 'memory', label: '内存' },
  { key: 'disk', label: '磁盘' },
]

function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot>(mockDashboard)
  const [status, setStatus] = useState<DashboardApiStatus>('loading')
  const [trendMetric, setTrendMetric] = useState<TrendMetric>('cpu')

  const overallTrend = useMemo(() => {
    const factor = trendMetric === 'memory' ? 0.84 : trendMetric === 'disk' ? 1.12 : 1
    return {
      ...snapshot.applicationTrend,
      series: snapshot.applicationTrend.series.map((series) => ({
        ...series,
        values: series.values.map((value) => Math.round(value * factor)),
      })),
    }
  }, [snapshot.applicationTrend, trendMetric])

  const loadSnapshot = useCallback(async () => {
    try {
      const next = await dashboardApi.getSnapshot()
      setSnapshot(next)
      setStatus(dashboardApi.isMockMode ? 'mock' : 'live')
    } catch (error) {
      console.error(error)
      setStatus('error')
    }
  }, [])

  useEffect(() => {
    void loadSnapshot()
    const timer = window.setInterval(() => void loadSnapshot(), 60_000)
    return () => window.clearInterval(timer)
  }, [loadSnapshot])

  return (
    <main className="dashboard" data-api-status={status}>
      <p className="sr-only" aria-live="polite">
        {status === 'loading' ? '数据加载中' : status === 'error' ? '数据接口连接失败，正在显示最近一次数据' : '数据已更新'}
      </p>

      <Header summary={snapshot.operationSummary} serverTime={snapshot.serverTime} />
      <MetricStrip metrics={snapshot.resourceMetrics} />

      <div className="dashboard-grid">
        <Panel title="山东省资源分布" className="dashboard-grid__map" framed={false}>
          <ShandongMap centers={snapshot.centers} cityValues={snapshot.cityValues} />
        </Panel>

        <div className="dashboard-grid__quadrants">
          <Panel title="应用维度配额使用率TOP5">
            <QuotaUsage items={snapshot.quotaRanking} />
          </Panel>
          <Panel title="应用维度CPU使用率排名">
            <CpuRankingChart items={snapshot.cpuRanking} />
          </Panel>
          <Panel title="应用维度资源内存使用率TOP5">
            <ResourceTable items={snapshot.memoryRanking} />
          </Panel>
          <Panel title="应用维度资源内存使用率TOP5">
            <BubbleUsageChart />
          </Panel>
        </div>

        <Panel title="资源申请订单趋势" className="dashboard-grid__left-trend">
          <TrendChart dataset={snapshot.resourceTrends.cpu} percent />
        </Panel>

        <Panel
          title="过去一段时间的资源使用整体趋势"
          className="dashboard-grid__right-trend"
          actions={(
            <div className="trend-tabs" role="tablist" aria-label="资源指标">
              {trendTabs.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={trendMetric === tab.key}
                  className={trendMetric === tab.key ? 'is-active' : ''}
                  onClick={() => setTrendMetric(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          )}
        >
          <TrendChart dataset={overallTrend} unitLabel="单位：条" />
        </Panel>
      </div>
    </main>
  )
}

export default App
