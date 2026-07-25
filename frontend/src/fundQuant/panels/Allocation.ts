import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'

export class Allocation extends PanelBase {
  private chart: echarts.ECharts | null = null

  constructor() {
    super({ id: 'allocation', title: '组合配置', defaultGridPos: { x: 1, y: 2, w: 1, h: 2 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-allocation'
    el.innerHTML = `
      <div class="panel-header">
        <h3>组合配置</h3>
        <div class="panel-toolbar">
          <button class="btn btn-sm btn-outline alloc-optimize">优化</button>
        </div>
      </div>
      <div class="alloc-body">
        <div class="alloc-chart"></div>
        <div class="alloc-table-wrapper"></div>
      </div>
      <div class="alloc-stats"></div>`
    return el
  }

  protected afterMount(): void {
    const btn = this.el?.querySelector<HTMLButtonElement>('.alloc-optimize')
    if (btn) {
      btn.disabled = state.get('fundPool').length === 0
      btn.addEventListener('click', () => {
        const codes = state.get('fundPool').map(f => f.fund_code)
        if (codes.length) this.runOptimize(codes)
      })
    }
    state.on('fundPool', () => {
      const pool = state.get('fundPool')
      const codes = pool.map(f => f.fund_code)
      if (btn) btn.disabled = pool.length === 0
      if (codes.length) this.runOptimize(codes)
    })
  }

  private async runOptimize(codes: string[]): Promise<void> {
    try {
      const res = await fundQuantApi.optimizeAllocation(codes)
      if (!res.success) return
      const data = res.data
      this.renderPie(data.weights)
      this.renderTable(data.weights)
      const statsEl = this.el?.querySelector('.alloc-stats')
      if (statsEl) {
        statsEl.innerHTML = `预期年化: ${data.expected_return.toFixed(1)}% &nbsp;|&nbsp; 波动: ${data.portfolio_volatility.toFixed(1)}% &nbsp;|&nbsp; 夏普: ${data.sharpe_ratio.toFixed(2)} &nbsp;|&nbsp; ${data.method}`
      }
    } catch { /* ignore */ }
  }

  private renderPie(weights: Record<string, number>): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.alloc-chart')
    if (!chartEl) return
    if (this.chart) this.chart.dispose()
    this.chart = echarts.init(chartEl)
    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316']
    const pool = state.get('fundPool')
    const data = Object.entries(weights).map(([code, weight], i) => ({
      name: pool.find(f => f.fund_code === code)?.fund_name || code,
      value: parseFloat((weight * 100).toFixed(1)),
      itemStyle: { color: colors[i % colors.length] },
    }))
    this.chart.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c}%' },
      series: [{ type: 'pie', radius: ['30%', '70%'], data, label: { show: false } }],
    })
  }

  private renderTable(weights: Record<string, number>): void {
    const el = this.el?.querySelector('.alloc-table-wrapper')
    if (!el) return
    const pool = state.get('fundPool')
    const sorted = Object.entries(weights).sort((a, b) => b[1] - a[1])
    el.innerHTML = `<table class="alloc-table">
      <thead><tr><th>基金</th><th class="text-right">权重</th></tr></thead>
      <tbody>${sorted.map(([code, w]) => `
        <tr><td class="alloc-name">${pool.find(f => f.fund_code === code)?.fund_name || code}</td>
        <td class="text-right alloc-weight">${(w * 100).toFixed(1)}%</td></tr>
      `).join('')}</tbody></table>`
  }

  async refresh(): Promise<void> {
    const codes = state.get('fundPool').map(f => f.fund_code)
    if (codes.length) await this.runOptimize(codes)
  }

  destroy(): void {
    this.chart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
  }
}
