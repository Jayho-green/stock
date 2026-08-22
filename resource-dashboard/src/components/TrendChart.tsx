import { useMemo } from 'react'
import type { EChartsCoreOption } from 'echarts/core'
import type { TrendDataset } from '../types/dashboard'
import { useEChart } from '../hooks/useEChart'
import { echarts } from '../lib/echarts'

type TrendChartProps = {
  dataset: TrendDataset
  percent?: boolean
  unitLabel?: string
}

export function TrendChart({ dataset, percent = false, unitLabel }: TrendChartProps) {
  const option = useMemo<EChartsCoreOption>(() => ({
    animationDuration: 900,
    color: dataset.series.map((series) => series.color ?? '#20d4d9'),
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(2, 13, 18, .95)',
      borderColor: '#2d838b',
      borderWidth: 1,
      textStyle: { color: '#d8f2f3' },
      axisPointer: { lineStyle: { color: 'rgba(67, 216, 218, .4)' } },
    },
    legend: {
      top: 6,
      right: 26,
      icon: 'rect',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 26,
      textStyle: { color: '#b4c4c6', fontSize: 12 },
    },
    grid: { left: unitLabel ? 80 : 58, right: 36, top: 66, bottom: 36 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dataset.categories,
      axisLine: { lineStyle: { color: '#8ba0a2' } },
      axisTick: { lineStyle: { color: '#8ba0a2' } },
      axisLabel: { color: '#a8b8bb', fontSize: 11, interval: 0 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: percent ? 100 : 5000,
      interval: percent ? 50 : 2500,
      name: unitLabel,
      nameLocation: 'middle',
      nameGap: 50,
      nameTextStyle: { color: '#9fb3b6', fontSize: 12 },
      axisLabel: { color: '#9fb3b6', fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: 'rgba(93, 133, 138, .17)', type: 'dashed' } },
    },
    series: dataset.series.map((series, index) => ({
      name: series.name,
      type: 'line',
      smooth: 0.42,
      showSymbol: !percent,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { width: 2.3, color: series.color },
      itemStyle: { color: series.color, borderColor: '#dff', borderWidth: 1 },
      areaStyle: !percent && index === dataset.series.length - 1 ? {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(18, 193, 198, .28)' },
          { offset: 1, color: 'rgba(18, 193, 198, .02)' },
        ]),
      } : undefined,
      data: series.values,
    })),
  }), [dataset, percent, unitLabel])

  const ref = useEChart(option)
  return <div className="chart chart--trend" ref={ref} aria-label="资源使用趋势图" />
}
