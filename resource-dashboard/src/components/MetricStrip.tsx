import type { ResourceMetric } from '../types/dashboard'

export function MetricStrip({ metrics }: { metrics: ResourceMetric[] }) {
  return (
    <section className="metric-strip" aria-label="资源总览">
      {metrics.map((metric) => (
        <article className="metric-card" key={metric.key}>
          <strong>{metric.value.toLocaleString('zh-CN')}</strong>
          <span>{metric.label}({metric.unit})</span>
        </article>
      ))}
    </section>
  )
}
