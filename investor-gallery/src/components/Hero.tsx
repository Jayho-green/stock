import { investors } from '../data/investors'
import { ArrowIcon } from './Icons'

const buffett = investors.find((investor) => investor.id === 'buffett')!

interface HeroProps {
  onOpen: (id: string) => void
}

export function Hero({ onOpen }: HeroProps) {
  return (
    <section className="hero" id="top">
      <div className="hero-copy reveal">
        <h1>
          那些把时间
          <span>变成资本的人</span>
        </h1>
        <p>十二位改变现代投资思想的人。不是名言集，而是一面关于耐心、判断与风险的肖像墙。</p>
        <a className="text-link" href="#portraits">
          浏览全部人物 <ArrowIcon size={17} />
        </a>
      </div>

      <button className="hero-portrait reveal" type="button" onClick={() => onOpen(buffett.id)} aria-label="打开沃伦·巴菲特档案">
        <span className="hero-orbit" aria-hidden="true" />
        <img src={buffett.image} alt="沃伦·巴菲特" style={{ objectPosition: buffett.objectPosition }} />
        <span className="hero-caption">
          <strong>{buffett.nameEn}</strong>
          <small>{buffett.principle}</small>
        </span>
        <span className="hero-number">01</span>
      </button>
    </section>
  )
}
