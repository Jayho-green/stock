import type { PropsWithChildren, ReactNode } from 'react'
import { GridIcon } from './Icons'

type PanelProps = PropsWithChildren<{
  title: string
  className?: string
  actions?: ReactNode
  framed?: boolean
}>

export function Panel({ title, className = '', actions, framed = true, children }: PanelProps) {
  return (
    <section className={`panel ${framed ? 'panel--framed' : ''} ${className}`}>
      <header className="panel__header">
        <span className="panel__accent" />
        <span className="panel__icon-wrap"><GridIcon className="panel__icon" /></span>
        <h2>{title}</h2>
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  )
}
