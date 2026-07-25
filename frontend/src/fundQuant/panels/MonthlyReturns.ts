// frontend/src/fundQuant/panels/MonthlyReturns.ts
import { PanelBase } from '../layout'
import { fundQuantApi, type MonthlyReturn } from '../api'
import { state } from '../state'

export class MonthlyReturns extends PanelBase {
  private allMatrix: MonthlyReturn[] = []
  private selectedYear: string = 'all'

  constructor() {
    super({ id: 'monthly_returns', title: '月度收益', defaultGridPos: { x: 1, y: 3, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-monthly'
    el.innerHTML = `
      <div class="panel-header">
        <h3>月度收益</h3>
        <select class="monthly-year-select" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
          <option value="all">全部年份</option>
        </select>
      </div>
      <div class="monthly-table-wrapper" style="overflow-x:auto;"></div>
      <div class="monthly-stats" style="padding:6px 8px;font-size:12px;color:var(--text-secondary);border-top:1px solid var(--border-light);margin-top:8px;"></div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.monthly-year-select')?.addEventListener('change', (e) => {
      this.selectedYear = (e.target as HTMLSelectElement).value
      if (this.allMatrix.length) this.renderMatrix()
    })
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const code = state.get('selectedFund') || state.get('fundPool')[0]?.fund_code
    if (!code) return
    try {
      const res = await fundQuantApi.getMonthlyReturns(code)
      if (!res.success) return
      this.allMatrix = res.data.matrix || []
      this.updateYearSelect()
      this.renderMatrix()
      this.renderStats(res.data.stats)
    } catch { /* ignore */ }
  }

  private updateYearSelect(): void {
    const select = this.el?.querySelector<HTMLSelectElement>('.monthly-year-select')
    if (!select) return
    const years = [...new Set(this.allMatrix.map(m => m.year))].sort((a, b) => b - a)
    select.innerHTML = '<option value="all">全部年份</option>' +
      years.map(y => `<option value="${y}"${this.selectedYear === String(y) ? ' selected' : ''}>${y}年</option>`).join('')
  }

  private renderMatrix(): void {
    const el = this.el?.querySelector('.monthly-table-wrapper')
    if (!el || !this.allMatrix.length) return

    let matrix = this.allMatrix
    if (this.selectedYear !== 'all') {
      matrix = matrix.filter(m => m.year === parseInt(this.selectedYear))
    }

    const byYear: Record<number, Record<number, number>> = {}
    const months = new Set<number>()
    matrix.forEach(m => {
      if (!byYear[m.year]) byYear[m.year] = {}
      byYear[m.year][m.month] = m.return
      months.add(m.month)
    })
    const sortedMonths = Array.from(months).sort((a, b) => a - b)

    let html = `<table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr><th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;"></th>`
    const monthNames = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']
    sortedMonths.forEach(m => { html += `<th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:right;">${monthNames[m - 1]}</th>` })
    html += `<th style="padding:4px 6px;color:var(--text-secondary);font-weight:600;text-align:right;">年化</th></tr></thead><tbody>`

    Object.entries(byYear).sort().forEach(([year, data]) => {
      const returns = sortedMonths.map(m => data[m]).filter(r => r != null) as number[]
      const annualReturn = returns.length ? returns.reduce((a, b) => (1 + a/100) * (1 + b/100) - 1, 0) : 0
      html += `<tr><td style="padding:4px 6px;font-weight:600;color:var(--text-primary);">${year}</td>`
      sortedMonths.forEach(m => {
        const ret = data[m]
        if (ret == null) {
          html += `<td style="padding:4px 6px;text-align:right;color:var(--text-tertiary);">--</td>`
        } else {
          const intensity = Math.min(Math.abs(ret) / 5, 1)
          const bgColor = ret >= 0
            ? `rgba(239,68,68,${intensity * 0.3})`
            : `rgba(16,185,129,${intensity * 0.3})`
          html += `<td style="padding:4px 6px;text-align:right;font-weight:600;background:${bgColor};color:${ret >= 0 ? 'var(--danger-color)' : 'var(--success-color)'};">${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%</td>`
        }
      })
      html += `<td style="padding:4px 6px;text-align:right;font-weight:600;color:${annualReturn >= 0 ? 'var(--danger-color)' : 'var(--success-color)'};">${annualReturn >= 0 ? '+' : ''}${(annualReturn * 100).toFixed(1)}%</td></tr>`
    })
    html += `</tbody></table>`
    el.innerHTML = html
  }

  private renderStats(stats: any): void {
    const el = this.el?.querySelector('.monthly-stats')
    if (!el || !stats.total_months) return
    el.innerHTML = `正收益: ${stats.positive_months}/${stats.total_months} (${(stats.positive_months / stats.total_months * 100).toFixed(0)}%) &nbsp;|&nbsp; 平均正: +${stats.avg_positive}% &nbsp;|&nbsp; 平均负: ${stats.avg_negative}%`
  }
}
