import { useEffect, useRef } from 'react'
import type { Investor } from '../types'
import { ArrowIcon, CloseIcon } from './Icons'

interface InvestorDrawerProps {
  investor: Investor | null
  onClose: () => void
}

export function InvestorDrawer({ investor, onClose }: InvestorDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!investor) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKey)
    }
  }, [investor, onClose])

  if (!investor) return null

  return (
    <div className="drawer-layer" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="investor-drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <button className="drawer-close" type="button" onClick={onClose} ref={closeRef} aria-label="关闭人物档案">
          <CloseIcon />
        </button>

        <figure className="drawer-image">
          <img src={investor.image} alt={investor.nameZh} style={{ objectPosition: investor.objectPosition }} />
          <figcaption>{investor.school} · {investor.principle}</figcaption>
        </figure>

        <div className="drawer-content">
          <span className="drawer-years">{investor.years}</span>
          <h2 id="drawer-title">{investor.nameZh}</h2>
          <p className="drawer-en">{investor.nameEn}</p>
          <p className="drawer-summary">{investor.summary}</p>

          <dl className="drawer-facts">
            <div>
              <dt>核心观察</dt>
              <dd>{investor.focus.join(' · ')}</dd>
            </div>
            <div>
              <dt>延伸阅读</dt>
              <dd>{investor.reading}</dd>
            </div>
          </dl>

          <a className="source-link" href={investor.sourceUrl} target="_blank" rel="noreferrer">
            查看照片来源与许可 <ArrowIcon size={18} />
          </a>
          <p className="image-credit">摄影 / 来源：{investor.credit} · {investor.license}</p>
        </div>
      </aside>
    </div>
  )
}
