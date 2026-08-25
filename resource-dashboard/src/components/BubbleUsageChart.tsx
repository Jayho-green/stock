import { useMemo } from 'react'
import type { EChartsCoreOption } from 'echarts/core'
import { useEChart } from '../hooks/useEChart'

const bubbleData = [
  [24, 12, 10, 'API后管平台'],
  [56, 58, 39, 'RASP-代理'],
  [64, 66, 45, '青藤容器--代理'],
  [96, 98, 68, '青藤容器--server-node'],
]

export function BubbleUsageChart() {
  const option = useMemo<EChartsCoreOption>(() => ({
    animationDuration: 950,
    legend: {
      top: 4,
      left: 8,
      icon: 'circle',
      itemWidth: 9,
      itemHeight: 9,
      itemGap: 25,
      textStyle: { color: '#9cb3b7', fontSize: 11 },
      data: ['青藤容器--server node', '青藤容器--代理'],
    },
    grid: { left: 52, right: 26, top: 70, bottom: 34 },
    xAxis: {
      type: 'value', min: 20, max: 100, interval: 20,
      axisLine: { lineStyle: { color: '#70888b' } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { color: '#9fb3b6', fontSize: 11 },
    },
    yAxis: {
      type: 'value', min: 0, max: 150, interval: 75,
      axisLine: { show: true, lineStyle: { color: '#70888b' } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { color: '#9fb3b6', fontSize: 11 },
    },
    tooltip: {
      backgroundColor: 'rgba(2, 13, 18, .95)',
      borderColor: '#2b858e',
      textStyle: { color: '#dff' },
      formatter: (params: unknown) => {
        const value = (params as { value?: Array<number | string> }).value ?? []
        return `${value[3] ?? ''}<br/>CPU ${value[0] ?? 0}% · 内存 ${value[1] ?? 0}%`
      },
    },
    series: [
      {
        name: '青藤容器--server node',
        type: 'scatter',
        data: bubbleData,
        symbolSize: (value: number[]) => value[2],
        itemStyle: {
          color: (params: unknown) => {
            const index = (params as { dataIndex?: number }).dataIndex ?? 0
            return ['rgba(59, 79, 80, .82)', 'rgba(143, 112, 74, .76)', 'rgba(17, 116, 125, .82)', 'rgba(69, 158, 169, .9)'][index]
          },
          borderColor: '#ccecee',
          borderWidth: 1.2,
        },
      },
      { name: '青藤容器--代理', type: 'scatter', data: [], itemStyle: { color: '#647e82' } },
    ],
  }), [])

  const ref = useEChart(option)
  return <div className="chart chart--bubble" ref={ref} aria-label="资源使用率气泡图" />
}
