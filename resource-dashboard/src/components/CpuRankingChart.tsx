import { useMemo } from 'react'
import type { EChartsCoreOption } from 'echarts/core'
import type { ApplicationUsage } from '../types/dashboard'
import { useEChart } from '../hooks/useEChart'

export function CpuRankingChart({ items }: { items: ApplicationUsage[] }) {
  const option = useMemo<EChartsCoreOption>(() => ({
    animationDuration: 800,
    grid: { left: 170, right: 28, top: 16, bottom: 13 },
    xAxis: { type: 'value', max: 100, show: false },
    yAxis: {
      type: 'category',
      inverse: true,
      data: items.map((item) => item.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#b9cdd0', fontSize: 12, width: 154, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: items.map((item) => item.value),
      barWidth: 15,
      showBackground: false,
      itemStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
          colorStops: [{ offset: 0, color: '#0d4d56' }, { offset: 1, color: '#20d2d8' }],
        },
      },
      label: {
        show: true,
        position: 'right',
        color: '#b9cdd0',
        fontSize: 12,
        formatter: (params: unknown) => `${(params as { value?: number }).value ?? 0}%`,
      },
    }],
  }), [items])

  const ref = useEChart(option)
  return <div className="chart chart--ranking" ref={ref} aria-label="应用维度CPU使用率排名" />
}
