import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type FundRanking as FundRankingItem } from '../api'
import { state } from '../state'

export class FundRanking extends PanelBase {
  private chart: echarts.ECharts | null = null

  constructor() {
    super({ id: 'fund_ranking', title: '选基排名', defaultGridPos: { x: 2, y: 1, w: 1, h: 1 } })
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
      const rankings = res.data?.rankings || []
      this.renderTable(rankings)
      if (rankings.length) this.renderRadar(rankings[0])
    } catch { /* silent keep old data */ }
  }

  private renderTable(rankings: FundRankingItem[]): void {
    const el = this.el?.querySelector('.rank-table-wrapper')
    if (!el) return
    el.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr>
        <th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;">#</th>
        <th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:left;">名称</th>
        <th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:right;">总分</th>
      </tr></thead>
      <tbody>${rankings.map((r, i) => `
        <tr data-code="${r.fund_code}" style="cursor:pointer;">
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);text-align:center;color:var(--text-tertiary);">${i + 1}</td>
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);color:var(--text-primary);font-weight:500;">${r.fund_name || r.fund_code}</td>
          <td style="padding:4px 6px;border-bottom:1px solid var(--border-light);text-align:right;font-weight:600;color:${r.total_score >= 0 ? 'var(--danger-color)' : 'var(--success-color)'}">${r.total_score.toFixed(4)}</td>
        </tr>
      `).join('')}</tbody></table>`
    el.querySelector('tbody')?.addEventListener('dblclick', (e: Event) => {
      const target = e.target as HTMLElement
      const tr = target.closest('tr')
      if (tr) {
        const code = tr.getAttribute('data-code')
        if (code) state.set('selectedFund', code)
      }
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
}
