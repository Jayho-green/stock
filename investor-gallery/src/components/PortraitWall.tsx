import type { Investor, School } from '../types'
import { schools } from '../data/investors'
import { PortraitCard } from './PortraitCard'

interface PortraitWallProps {
  investors: Investor[]
  activeSchool: School
  onSchoolChange: (school: School) => void
  onOpen: (id: string) => void
}

export function PortraitWall({ investors, activeSchool, onSchoolChange, onOpen }: PortraitWallProps) {
  return (
    <section className="portrait-section" id="portraits">
      <header className="section-header reveal">
        <div>
          <p>THE INVESTOR ARCHIVE</p>
          <h2>投资思想肖像墙</h2>
        </div>
        <span className="result-count">{String(investors.length).padStart(2, '0')} 人</span>
      </header>

      <div className="filter-bar" role="toolbar" aria-label="按投资流派筛选">
        {schools.map((school) => (
          <button
            className={school === activeSchool ? 'active' : ''}
            type="button"
            key={school}
            onClick={() => onSchoolChange(school)}
            aria-pressed={school === activeSchool}
          >
            {school}
          </button>
        ))}
      </div>

      <div className={`mosaic ${activeSchool === '全部' ? '' : 'is-filtered'}`} key={activeSchool}>
        <div className="trajectory" aria-hidden="true">
          <svg viewBox="0 0 1320 1160" preserveAspectRatio="none">
            <path pathLength="1" d="M-40 920C180 1030 260 690 470 750S680 300 900 440S1120 140 1380 230" />
            <circle cx="470" cy="750" r="5" />
            <circle cx="900" cy="440" r="5" />
          </svg>
        </div>
        {investors.map((investor, index) => (
          <PortraitCard key={investor.id} investor={investor} index={index} onOpen={onOpen} />
        ))}
      </div>
    </section>
  )
}
