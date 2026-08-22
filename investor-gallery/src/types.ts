export type School = '全部' | '价值' | '品质' | '宏观' | '量化' | '激进' | '配置' | '创新'

export interface Investor {
  id: string
  nameZh: string
  nameEn: string
  years: string
  school: Exclude<School, '全部'>
  principle: string
  summary: string
  focus: string[]
  reading: string
  image: string
  sourceUrl: string
  credit: string
  license: string
  tile: string
  objectPosition?: string
}
