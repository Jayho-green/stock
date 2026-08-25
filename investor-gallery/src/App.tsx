import { useCallback, useEffect, useMemo, useState } from 'react'
import { Footer } from './components/Footer'
import { Header } from './components/Header'
import { Hero } from './components/Hero'
import { InvestorDrawer } from './components/InvestorDrawer'
import { PortraitWall } from './components/PortraitWall'
import { investors } from './data/investors'
import type { School } from './types'

export default function App() {
  const [activeSchool, setActiveSchool] = useState<School>('全部')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const visibleInvestors = useMemo(
    () => activeSchool === '全部' ? investors : investors.filter((investor) => investor.school === activeSchool),
    [activeSchool],
  )

  const selectedInvestor = investors.find((investor) => investor.id === selectedId) ?? null
  const closeDrawer = useCallback(() => setSelectedId(null), [])

  useEffect(() => {
    const revealItems = Array.from(document.querySelectorAll<HTMLElement>('.reveal'))
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => entry.isIntersecting && entry.target.classList.add('is-visible')),
      { threshold: 0.12 },
    )
    revealItems.forEach((item) => observer.observe(item))
    return () => observer.disconnect()
  }, [activeSchool])

  useEffect(() => {
    let frame = 0
    const update = () => {
      frame = 0
      const section = document.querySelector<HTMLElement>('.portrait-section')
      if (!section) return
      const rect = section.getBoundingClientRect()
      const progress = Math.min(1, Math.max(0, -rect.top / Math.max(rect.height, 1)))
      section.style.setProperty('--trajectory-shift', `${progress * 72}px`)
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <div className="app-shell">
      <Header />
      <main>
        <Hero onOpen={setSelectedId} />
        <PortraitWall
          investors={visibleInvestors}
          activeSchool={activeSchool}
          onSchoolChange={setActiveSchool}
          onOpen={setSelectedId}
        />
      </main>
      <Footer />
      <InvestorDrawer investor={selectedInvestor} onClose={closeDrawer} />
    </div>
  )
}
