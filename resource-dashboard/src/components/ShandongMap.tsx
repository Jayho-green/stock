import type { EChartsCoreOption } from 'echarts/core'
import { useEffect, useMemo, useState } from 'react'
import type { ResourceCenter } from '../types/dashboard'
import { useEChart } from '../hooks/useEChart'
import { echarts } from '../lib/echarts'
import { BackIcon, DiamondIcon, ServerIcon } from './Icons'

type ShandongMapProps = {
  centers: ResourceCenter[]
  cityValues: Record<string, number>
}

function CenterDetail({ center }: { center: ResourceCenter }) {
  return (
    <aside className="center-detail" aria-live="polite">
      <header>
        <DiamondIcon className="center-detail__diamond" />
        <h3>{center.name}</h3>
      </header>
      <div className="center-detail__rows">
        {center.resources.map((resource) => (
          <div className="center-detail__row" key={resource.label}>
            <span className="center-detail__dash">—</span>
            <span>{resource.label}</span>
            <strong>{resource.used}</strong>
            <span>{resource.capacity}</span>
            <b>{resource.rate}%</b>
          </div>
        ))}
      </div>
    </aside>
  )
}

export function ShandongMap({ centers, cityValues }: ShandongMapProps) {
  const [ready, setReady] = useState(false)
  const [selectedId, setSelectedId] = useState(centers[1]?.id ?? centers[0]?.id ?? '')

  useEffect(() => {
    let active = true
    fetch('/data/shandong.json')
      .then((response) => {
        if (!response.ok) throw new Error(`地图数据加载失败: ${response.status}`)
        return response.json()
      })
      .then((geoJson: unknown) => {
        if (!active) return
        echarts.registerMap('shandong-resource-map', geoJson as Parameters<typeof echarts.registerMap>[1])
        setReady(true)
      })
      .catch(() => setReady(false))
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!centers.some((center) => center.id === selectedId) && centers[0]) {
      setSelectedId(centers[0].id)
    }
  }, [centers, selectedId])

  const selected = centers.find((center) => center.id === selectedId) ?? centers[0]

  const option = useMemo<EChartsCoreOption>(() => ({
    animationDuration: 900,
    animationEasing: 'cubicOut',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(2, 13, 18, .94)',
      borderColor: '#2d8f98',
      borderWidth: 1,
      textStyle: { color: '#d9f7f8', fontSize: 15 },
      formatter: (params: unknown) => {
        const item = params as { name?: string; value?: number | number[] }
        const rawValue = Array.isArray(item.value) ? item.value[2] : item.value
        return `${item.name ?? ''}<br/><b style="color:#36e0e3">${rawValue ?? 0}</b> 个资源`
      },
    },
    geo: {
      map: 'shandong-resource-map',
      roam: false,
      left: '2%',
      right: '7%',
      top: '5%',
      bottom: '9%',
      scaleLimit: { min: 0.8, max: 2 },
      label: {
        show: true,
        color: '#d7edef',
        fontSize: 12,
        lineHeight: 16,
        formatter: (params: unknown) => {
          const item = params as { name?: string }
          const cityName = item.name ?? ''
          return `${cityName.replace('市', '')}\n${cityValues[cityName] ?? ''}`
        },
      },
      itemStyle: {
        areaColor: '#123c44',
        borderColor: '#5bc7cc',
        borderWidth: 1.2,
        shadowColor: 'rgba(38, 231, 235, .72)',
        shadowBlur: 13,
        shadowOffsetY: 5,
      },
      emphasis: {
        label: { color: '#ffffff' },
        itemStyle: { areaColor: '#1b6872', borderColor: '#b8ffff', borderWidth: 1.6 },
      },
    },
    series: [
      {
        type: 'map',
        map: 'shandong-resource-map',
        geoIndex: 0,
        data: Object.entries(cityValues).map(([name, value]) => ({ name, value })),
        label: {
          show: true,
          color: '#d7edef',
          fontSize: 12,
          lineHeight: 16,
          formatter: (params: unknown) => {
            const item = params as { name?: string; value?: number }
            return `${(item.name ?? '').replace('市', '')}\n${item.value ?? ''}`
          },
        },
        itemStyle: {
          areaColor: '#174d55',
          borderColor: '#397981',
          borderWidth: 0.9,
        },
        emphasis: {
          label: { color: '#fff', fontWeight: 700 },
          itemStyle: { areaColor: '#1b6f79' },
        },
        select: {
          label: { color: '#fff' },
          itemStyle: { areaColor: '#226f78' },
        },
      },
      {
        type: 'effectScatter',
        coordinateSystem: 'geo',
        zlevel: 3,
        rippleEffect: { scale: 3, brushType: 'stroke', number: 3 },
        symbol: 'pin',
        symbolSize: (value: number[]) => Math.max(28, Math.min(46, Number(value[2]) / 30)),
        label: {
          show: true,
          position: 'top',
          distance: 7,
          color: '#eaffff',
          fontSize: 13,
          fontWeight: 700,
          padding: [4, 9],
          borderRadius: 10,
          backgroundColor: 'rgba(5, 28, 34, .9)',
          borderColor: '#5b9da2',
          borderWidth: 1,
          formatter: (params: unknown) => {
            const item = params as { data?: { label?: string } }
            return item.data?.label ?? ''
          },
        },
        data: centers.map((center) => ({
          name: center.cityName,
          label: center.name,
          value: [...center.coordinate, center.value],
          itemStyle: {
            color: center.tone === 'amber' ? '#f3b564' : '#67eced',
            shadowBlur: center.id === selectedId ? 22 : 12,
            shadowColor: center.tone === 'amber' ? '#d98937' : '#3af5f5',
          },
          selected: center.id === selectedId,
        })),
      },
    ],
  }), [centers, cityValues, selectedId])

  const chartRef = useEChart(ready ? option : {}, {
    click: (params) => {
      const item = params as { name?: string }
      const center = centers.find((candidate) => candidate.cityName === item.name)
      if (center) setSelectedId(center.id)
    },
  })

  function selectPrevious() {
    const index = Math.max(0, centers.findIndex((center) => center.id === selectedId))
    const nextIndex = (index - 1 + centers.length) % centers.length
    setSelectedId(centers[nextIndex]?.id ?? selectedId)
  }

  return (
    <div className="map-stage">
      <div className="map-stage__province map-stage__province--hebei">河北</div>
      <div className="map-stage__province map-stage__province--jiangsu">江苏</div>
      <button className="map-stage__back" type="button" onClick={selectPrevious} aria-label="切换上一个资源中心">
        <BackIcon />
      </button>
      <div className="map-stage__cloud-count">
        <div>
          <span>上云应用数</span>
          <strong>24<small>个</small></strong>
        </div>
        <span className="map-stage__orbit"><ServerIcon /></span>
      </div>
      <div className="map-stage__chart" ref={chartRef} aria-label="山东省资源分布地图" />
      {!ready ? <div className="map-stage__loading">地图加载中</div> : null}
      {selected ? <CenterDetail center={selected} /> : null}
    </div>
  )
}
