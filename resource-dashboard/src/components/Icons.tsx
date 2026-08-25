type IconProps = { className?: string }

export function GridIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="3" width="6" height="6" rx="0.6" fill="currentColor" />
      <rect x="15" y="3" width="6" height="6" rx="0.6" fill="currentColor" />
      <rect x="3" y="15" width="6" height="6" rx="0.6" fill="currentColor" />
      <rect x="15" y="15" width="6" height="6" rx="0.6" fill="currentColor" />
    </svg>
  )
}

export function ChevronIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 20 20" aria-hidden="true">
      <path d="M7 4.5 12.5 10 7 15.5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function ServerIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 44 44" aria-hidden="true">
      <ellipse cx="22" cy="11" rx="12" ry="5.5" fill="currentColor" opacity="0.95" />
      <path d="M10 11v8c0 3 5.4 5.5 12 5.5S34 22 34 19v-8c0 3-5.4 5.5-12 5.5S10 14 10 11Z" fill="currentColor" opacity="0.76" />
      <path d="M10 20v8c0 3 5.4 5.5 12 5.5S34 31 34 28v-8c0 3-5.4 5.5-12 5.5S10 23 10 20Z" fill="currentColor" opacity="0.58" />
      <ellipse cx="22" cy="28" rx="12" ry="5.5" fill="currentColor" opacity="0.75" />
      <circle cx="29" cy="19" r="1.4" fill="#06151b" />
      <circle cx="29" cy="28" r="1.4" fill="#06151b" />
    </svg>
  )
}

export function DiamondIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 28 28" aria-hidden="true">
      <path d="M14 1.5 26.5 14 14 26.5 1.5 14 14 1.5Z" fill="rgba(5,23,29,.84)" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="14" cy="14" r="3.2" fill="currentColor" />
      <path d="M14 5v4M14 19v4M5 14h4M19 14h4" stroke="currentColor" strokeWidth="1" />
    </svg>
  )
}

export function BackIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 28 28" aria-hidden="true">
      <path d="m17.5 7-7 7 7 7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
