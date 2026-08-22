import type { ApplicationUsage } from '../types/dashboard'

export function QuotaUsage({ items }: { items: ApplicationUsage[] }) {
  return (
    <div className="quota-grid">
      {items.map((item) => (
        <div className="quota-item" key={item.id}>
          <div className="quota-item__meta">
            <span>{item.name}</span>
            <strong className={item.value >= 90 ? 'is-hot' : ''}>{item.value}%</strong>
          </div>
          <div className="quota-item__track">
            <span style={{ width: `${item.value}%` }} className={item.value >= 90 ? 'is-hot' : ''} />
          </div>
        </div>
      ))}
    </div>
  )
}
