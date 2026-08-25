import type { EChartsCoreOption } from 'echarts/core'
import { useEffect, useRef } from 'react'
import { echarts } from '../lib/echarts'

type ChartEvents = Record<string, (params: unknown) => void>

export function useEChart(option: EChartsCoreOption, events: ChartEvents = {}) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)
  const eventsRef = useRef(events)
  eventsRef.current = events

  useEffect(() => {
    const element = elementRef.current
    if (!element) return

    const chart = echarts.init(element, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element)

    Object.entries(eventsRef.current).forEach(([eventName, handler]) => {
      chart.on(eventName, handler)
    })

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true, lazyUpdate: true })
  }, [option])

  return elementRef
}
