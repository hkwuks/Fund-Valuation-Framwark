import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type FundRanking as FundRankingItem } from '../api'
import { state } from '../state'

export class FundRanking extends PanelBase {
  private chart: echarts.ECharts | null = null
  private rankings: FundRankingItem[] = []
  private sortKey: string = 'total_score'
  private sortAsc: boolean = false

  constructor() {
    super({ id: 'fund_ranking', title: '选基排名', defaultGridPos: { x: 1, y: 0, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-ranking'
    el.innerHTML = `
      <div class="panel-header">
        <h3>选基排名</h3>
        <div class="panel-toolbar">
          <select class="rank-type-select" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
            <option value="stock">股票型</option>
            <option value="balanced">混合型</option>
            <option value="bond">债券型</option>
            <option value="index">指数型</option>
          </select>
        </div>
      </div>
      <div class="rank-body" style="display:grid;grid-template-columns:1fr 300px;gap:8px;">
        <div class="rank-table-wrapper" style="overflow-y:auto;max-height:220px;"></div>
        <div class="rank-radar" style="height:220px;"></div>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.rank-type-select')?.addEventListener('change', () => this.refresh())
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const fundType = (this.el.querySelector('.rank-type-select') as HTMLSelectElement)?.value || 'stock'
    try {
      const res = await fundQuantApi.screenFunds(fundType, 10)
      this.rankings = res.data?.rankings || []
      this.renderTable()
      if (this.rankings.length) this.renderRadar(this.rankings[0])
    } catch { /* silent keep old data */ }
  }

  private sortBy(key: string): void {
    if (this.sortKey === key) {
      this.sortAsc = !this.sortAsc
    } else {
      this.sortKey = key
      this.sortAsc = key === 'total_score' ? false : true
    }
    this.renderTable()
  }

  private renderTable(): void {
    const el = this.el?.querySelector('.rank-table-wrapper')
    if (!el) return

    // 排序
    const sorted = [...this.rankings].sort((a, b) => {
      const av = a[this.sortKey as keyof FundRankingItem] ?? 0
      const bv = b[this.sortKey as keyof FundRankingItem] ?? 0
      if (typeof av === 'string' && typeof bv === 'string') {
        return this.sortAsc ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      return this.sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })

    const sortDir = (k: string) => this.sortKey === k ? (this.sortAsc ? 'sorted-asc' : 'sorted-desc') : ''

    el.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr>
        <th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;">#</th>
        <th class="sortable ${sortDir('fund_name')}" data-sort="fund_name" style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:left;">名称</th>
        <th class="sortable ${sortDir('total_score')}" data-sort="total_score" style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:right;">总分</th>
        <th style="padding:4px 6px;"></th>
      </tr></thead>
      <tbody>${sorted.map((r, i) => `
        <tr data-code="${r.fund_code}" style="cursor:pointer;">
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);text-align:center;color:var(--text-tertiary);">${i + 1}</td>
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);color:var(--text-primary);font-weight:500;">${r.fund_name || r.fund_code}</td>
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);text-align:right;font-weight:600;color:${r.total_score >= 0 ? 'var(--danger-color)' : 'var(--success-color)'}">${r.total_score.toFixed(4)}</td>
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);text-align:right;">
            <button class="btn btn-sm btn-outline rank-add-btn" data-code="${r.fund_code}" style="font-size:11px;padding:2px 6px;">+组合</button>
          </td>
        </tr>
      `).join('')}</tbody></table>
      <div class="rank-actions">
        <button class="btn btn-sm btn-outline rank-backtest-btn" style="font-size:11px;">回测选中</button>
      </div>`

    // 排序
    el.querySelectorAll('th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const key = (th as HTMLElement).dataset.sort || 'total_score'
        this.sortBy(key)
      })
    })

    // 点击行→选基金
    el.querySelector('tbody')?.addEventListener('dblclick', (e: Event) => {
      const target = e.target as HTMLElement
      const tr = target.closest('tr')
      if (tr) {
        const code = tr.getAttribute('data-code')
        if (code) state.set('selectedFund', code)
      }
    })

    // 加入组合
    el.querySelectorAll('.rank-add-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        alert('基金已添加到组合（待后端实现组合管理）')
      })
    })

    // 回测按钮
    el.querySelector('.rank-backtest-btn')?.addEventListener('click', () => {
      alert('回测功能请在回测面板使用（待实现）')
    })
  }

  private renderRadar(top: FundRankingItem): void {
    const el = this.el?.querySelector('.rank-radar')
    if (!el) return
    if (this.chart) this.chart.dispose()
    this.chart = echarts.init(el as HTMLElement)
    const factors = top.factors || {}
    const indicators = Object.entries(factors).map(([k, v]) => ({
      name: k, max: Math.max(Math.abs(v) * 2, 1),
    }))
    this.chart.setOption({
      title: { text: top.fund_name, left: 'center', textStyle: { fontSize: 11 } },
      radar: { indicator: indicators, shape: 'polygon', radius: '60%' },
      series: [{ type: 'radar', data: [{ value: Object.values(factors).map(v => Math.abs(v)), name: '评分' }], areaStyle: { opacity: 0.2 } }],
      tooltip: { trigger: 'item' },
    })
  }

  destroy(): void {
    this.chart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
  }
}
