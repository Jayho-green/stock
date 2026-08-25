import { useEffect, useMemo, useState } from 'react'
import type { OperationSummary } from '../types/dashboard'

type HeaderProps = {
  summary: OperationSummary[]
  serverTime?: string
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function formatTime(date: Date) {
  return {
    date: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    time: `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  }
}

export function Header({ summary, serverTime }: HeaderProps) {
  const initial = useMemo(() => (serverTime ? new Date(serverTime) : new Date()), [serverTime])
  const [now, setNow] = useState(initial)

  useEffect(() => {
    setNow(serverTime ? new Date(serverTime) : new Date())
    const timer = window.setInterval(() => setNow((date) => new Date(date.getTime() + 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [serverTime])

  const formatted = formatTime(now)

  return (
    <header className="dashboard-header">
      <div className="dashboard-header__title-plate">
        <h1>资源管理大屏</h1>
      </div>
      <div className="dashboard-header__summary">
        {summary.map((item) => (
          <div className="summary-pill" key={item.key}>
            <span className="summary-pill__dot" />
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      <time className="dashboard-header__clock" dateTime={now.toISOString()}>
        <span>{formatted.date}</span>
        <span>{formatted.time}</span>
      </time>
    </header>
  )
}
