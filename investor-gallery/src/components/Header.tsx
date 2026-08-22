import { BrandMark } from './BrandMark'

export function Header() {
  return (
    <header className="site-header">
      <a className="brand" href="#top" aria-label="LONG VIEW 投资者档案首页">
        <BrandMark />
        <span className="brand-copy">
          <strong>LONG VIEW</strong>
          <small>投资者档案</small>
        </span>
      </a>
      <nav className="site-nav" aria-label="主导航">
        <a href="#portraits">人物</a>
        <a href="#portraits">流派</a>
        <a href="#credits">图片来源</a>
      </nav>
      <a className="header-index" href="#portraits">12 / ARCHIVE</a>
    </header>
  )
}
