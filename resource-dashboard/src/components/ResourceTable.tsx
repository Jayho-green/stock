import type { ApplicationUsage } from '../types/dashboard'
import { ChevronIcon } from './Icons'

export function ResourceTable({ items }: { items: ApplicationUsage[] }) {
  return (
    <div className="resource-table" role="table" aria-label="资源内存使用率排行榜">
      <div className="resource-table__header" role="row">
        <span role="columnheader">排名</span>
        <span role="columnheader">名称</span>
        <span role="columnheader">数量</span>
      </div>
      {items.slice(0, 5).map((item, index) => (
        <div className="resource-table__row" role="row" key={item.id}>
          <span role="cell" className="resource-table__rank"><ChevronIcon /> {index + 1}</span>
          <span role="cell">{item.name}</span>
          <strong role="cell" className={item.value >= 90 ? 'is-hot' : ''}>{item.value}</strong>
        </div>
      ))}
    </div>
  )
}
