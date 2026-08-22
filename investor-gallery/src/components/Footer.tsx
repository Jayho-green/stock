import { investors } from '../data/investors'

export function Footer() {
  return (
    <footer className="site-footer" id="credits">
      <div className="footer-lead">
        <strong>LONG VIEW</strong>
        <p>照片来自 Wikimedia Commons。点击人物可查看每张照片的作者、许可和原始页面。</p>
      </div>
      <div className="credit-list" aria-label="照片来源">
        {investors.map((investor) => (
          <a key={investor.id} href={investor.sourceUrl} target="_blank" rel="noreferrer">
            {investor.nameZh} <span>{investor.license}</span>
          </a>
        ))}
      </div>
      <div className="footer-bottom">
        <span>INVESTOR ARCHIVE · 2026</span>
        <a href="#top">返回顶部 ↑</a>
      </div>
    </footer>
  )
}
