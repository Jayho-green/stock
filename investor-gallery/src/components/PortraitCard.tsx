import type { Investor } from '../types'
import { ArrowIcon } from './Icons'

interface PortraitCardProps {
  investor: Investor
  index: number
  onOpen: (id: string) => void
}

export function PortraitCard({ investor, index, onOpen }: PortraitCardProps) {
  return (
    <article className={`portrait-card ${investor.tile} reveal`}>
      <button type="button" onClick={() => onOpen(investor.id)} aria-label={`打开${investor.nameZh}档案`}>
        <img src={investor.image} alt={investor.nameZh} loading={index > 4 ? 'lazy' : 'eager'} style={{ objectPosition: investor.objectPosition }} />
        <span className="portrait-shade" aria-hidden="true" />
        <span className="portrait-index">{String(index + 1).padStart(2, '0')}</span>
        <span className="portrait-meta">
          <span>
            <strong>{investor.nameZh}</strong>
            <small>{investor.nameEn}</small>
          </span>
          <em>{investor.principle}</em>
        </span>
        <span className="portrait-reveal">
          <span>{investor.summary}</span>
          <i><ArrowIcon size={18} /></i>
        </span>
      </button>
    </article>
  )
}
