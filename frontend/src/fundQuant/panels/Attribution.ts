import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type AttributionResult } from '../api'
import { state } from '../state'

export class Attribution extends PanelBase {
  private chart: echarts.ECharts | null = null

  constructor() {
    super({ id: 'attribution', title: '归因分析', defaultGridPos: { x: 0, y: 3, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-attribution'
    el.innerHTML = `
      <div class="panel-header">
        <h3>归因分析</h3>
        <div class="panel-toolbar">
          <select class="attr-method" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
            <option value="brinson">Brinson</option>
            <option value="carino">Carino 链接</option>
          </select>
          <button class="btn btn-sm btn-outline attr-export" style="font-size:11px;">导出</button>
        </div>
      </div>
      <div class="attr-chart" style="height:160px;"></div>
      <div class="attr-summary" style="padding:6px 8px;font-size:12px;color:var(--text-secondary);border-top:1px solid var(--border-light);margin-top:8px;"></div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.attr-method, .attr-period')?.addEventListener('change', () => this.refresh())
    this.el?.querySelector('.attr-export')?.addEventListener('click', () => this.exportChart())
  }

  private exportChart(): void {
    if (!this.chart) return
    const url = this.chart.getDataURL({ type: 'png', pixelRatio: 2 })
    const a = document.createElement('a')
    a.href = url
    a.download = `attribution-${new Date().toISOString().slice(0, 10)}.png`
    a.click()
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const codes = state.get('fundPool').map(f => f.fund_code)
    if (!codes.length) return
    try {
      const res = await fundQuantApi.getAttribution(codes, '2026-01-01', '2026-06-30')
      if (!res.success) return
      this.renderChart(res.data)
      this.renderSummary(res.data.cumulative)
    } catch { /* ignore */ }
  }

  private renderChart(data: AttributionResult): void {
    const chartEl = this.el?.querySelector<HTMLElement>('.attr-chart')
    if (!chartEl || !data.periods.length) return
    if (this.chart) this.chart.dispose()
    this.chart = echarts.init(chartEl)
    this.chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['配置收益', '选基收益', '交互收益'], bottom: 0, textStyle: { fontSize: 10 } },
      grid: { left: '3%', right: '3%', bottom: '20%', top: '3%', containLabel: true },
      xAxis: { type: 'category', data: data.periods.map(p => p.date), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 } },
      series: [
        { name: '配置收益', type: 'bar', stack: 'total', data: data.periods.map(p => p.allocation), itemStyle: { color: '#3b82f6' } },
        { name: '选基收益', type: 'bar', stack: 'total', data: data.periods.map(p => p.selection), itemStyle: { color: '#10b981' } },
        { name: '交互收益', type: 'bar', stack: 'total', data: data.periods.map(p => p.interaction), itemStyle: { color: '#f59e0b' } },
      ],
    })
  }

  private renderSummary(cumulative: any): void {
    const el = this.el?.querySelector('.attr-summary')
    if (!el) return
    el.innerHTML = `配置: ${cumulative.allocation >= 0 ? '+' : ''}${cumulative.allocation}% &nbsp;|&nbsp; 选基: ${cumulative.selection >= 0 ? '+' : ''}${cumulative.selection}% &nbsp;|&nbsp; 交互: ${cumulative.interaction >= 0 ? '+' : ''}${cumulative.interaction}% &nbsp;|&nbsp; 超额: ${cumulative.excess >= 0 ? '+' : ''}${cumulative.excess}%`
  }

  destroy(): void {
    this.chart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
  }
}
