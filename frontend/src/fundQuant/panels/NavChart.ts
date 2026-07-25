import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'
import { getChartTheme } from '../../fundQuantCharts'

export class NavChart extends PanelBase {
  private chart: echarts.ECharts | null = null
  private unsub: (() => void) | null = null
  private currentCode: string = ''
  private cachedNavData: any[] = []
  private cachedBuySignals: { date: string; nav: number }[] = []
  private cachedSellSignals: { date: string; nav: number }[] = []
  private activeDays: number = 90

  constructor() {
    super({ id: 'nav_chart', title: '净值走势', defaultGridPos: { x: 0, y: 1, w: 2, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-navchart'
    el.innerHTML = `
      <div class="panel-header">
        <h3>净值走势</h3>
        <div class="panel-toolbar">
          <select class="nav-fund-select">
            <option value="">-- 选择基金 --</option>
          </select>
          <div class="nav-periods">
            <button class="period-btn" data-days="30">1月</button>
            <button class="period-btn active" data-days="90">3月</button>
            <button class="period-btn" data-days="365">1年</button>
            <button class="period-btn" data-days="1095">3年</button>
          </div>
        </div>
      </div>
      <div class="panel-chart skeleton"></div>`
    return el
  }

  protected afterMount(): void {
    this.unsub = state.on('selectedFund', () => {
      const code = state.get('selectedFund')
      if (code) this.refresh()
    })

    this.el?.querySelector('.nav-fund-select')?.addEventListener('change', (e) => {
      const code = (e.target as HTMLSelectElement).value
      if (code) state.set('selectedFund', code)
    })

    this.el?.querySelector('.nav-periods')?.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('.period-btn') as HTMLElement
      if (!btn) return
      this.el?.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'))
      btn.classList.add('active')
      const days = parseInt(btn.dataset.days || '90', 10)
      if (days !== this.activeDays && this.cachedNavData.length) {
        this.activeDays = days
        this.renderChartWithFilter()
      }
    })
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const select = this.el.querySelector<HTMLSelectElement>('.nav-fund-select')
    const pool = state.get('fundPool')

    if (select) {
      const currentVal = select.value
      select.innerHTML = pool.map(f =>
        `<option value="${f.fund_code}"${f.fund_code === currentVal ? ' selected' : ''}>${f.fund_name || f.fund_code}</option>`
      ).join('')
    }

    const code = state.get('selectedFund') || pool[0]?.fund_code
    if (!code) return
    if (code === this.currentCode) return
    this.currentCode = code
    if (select) select.value = code

    try {
      const navRes = await fundQuantApi.getNav(code)
      const navData = navRes.data?.nav_history || []
      if (!navData.length) return

      this.cachedNavData = navData

      const sigRes = await fundQuantApi.getSignals(code, 50)
      const signals = (sigRes.data || []).filter(s => s.direction === 'buy' || s.direction === 'sell')
      this.cachedBuySignals = signals.filter(s => s.direction === 'buy').map(s => ({
        date: (s.created_at || '').slice(0, 10), nav: 0,
      }))
      this.cachedSellSignals = signals.filter(s => s.direction === 'sell').map(s => ({
        date: (s.created_at || '').slice(0, 10), nav: 0,
      }))

      for (const pt of [...this.cachedBuySignals, ...this.cachedSellSignals]) {
        const match = navData.find((d: any) => (d.date || '').slice(0, 10) === pt.date)
        pt.nav = match ? (match.nav || (match.adjusted_nav ?? 0)) : 0
      }

      this.renderChartWithFilter()
    } catch { /* ignore */ }
  }

  private filterData<T extends { date: string }>(data: T[], days: number): T[] {
    if (days >= 9999) return data
    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - days)
    return data.filter(d => new Date(d.date) >= cutoff)
  }

  private renderChartWithFilter(): void {
    const filtered = this.filterData(this.cachedNavData, this.activeDays)
    const validDates = new Set(filtered.map((d: any) => (d.date || '').slice(0, 10)))
    this.renderChart(
      filtered,
      this.cachedBuySignals.filter(s => validDates.has(s.date)),
      this.cachedSellSignals.filter(s => validDates.has(s.date)),
    )
  }

  private renderChart(
    navData: any[],
    buySignals: { date: string; nav: number }[],
    sellSignals: { date: string; nav: number }[],
  ): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return

    chartEl.classList.remove('skeleton')

    let peak = -Infinity
    const drawdown = navData.map((d: any) => {
      const nav = d.nav || d.adjusted_nav || 0
      peak = Math.max(peak, nav)
      return { date: (d.date || '').slice(0, 10), value: (nav / peak - 1) * 100 }
    })

    const isDark = document.body.classList.contains('dark-mode')
    if (this.chart) { this.chart.dispose(); this.chart = null }
    this.chart = echarts.init(chartEl)
    const theme = getChartTheme(isDark)

    const option: echarts.EChartsOption = {
      ...theme,
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: { data: ['净值', '买入信号', '卖出信号', '回撤'], bottom: 0 },
      grid: { left: '3%', right: '4%', bottom: '15%', containLabel: true },
      xAxis: {
        type: 'category',
        data: navData.map((d: any) => (d.date || '').slice(0, 10)),
        axisLabel: { rotate: 45, fontSize: 11 },
      },
      yAxis: [
        { type: 'value', scale: true, name: '净值' },
        { type: 'value', scale: true, name: '回撤%', min: -30, max: 5, axisLabel: { formatter: '{value}%' } },
      ],
      series: [
        {
          name: '净值', type: 'line',
          data: navData.map((d: any) => d.nav || d.adjusted_nav),
          smooth: true,
          lineStyle: { width: 2, color: '#3b82f6' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(59,130,246,0.2)' },
              { offset: 1, color: 'rgba(59,130,246,0)' },
            ]),
          },
        },
        {
          name: '买入信号', type: 'scatter',
          data: buySignals.map(d => [d.date, d.nav]),
          symbolSize: 12, itemStyle: { color: '#ef4444' },
        },
        {
          name: '卖出信号', type: 'scatter',
          data: sellSignals.map(d => [d.date, d.nav]),
          symbolSize: 12, itemStyle: { color: '#10b981' },
        },
        {
          name: '回撤', type: 'line',
          data: drawdown.map(d => d.value),
          smooth: true, yAxisIndex: 1,
          lineStyle: { width: 1, color: '#f59e0b', type: 'dashed' },
          areaStyle: { color: 'rgba(245,158,11,0.1)' },
        },
      ],
    }
    this.chart.setOption(option)
  }

  destroy(): void {
    this.unsub?.()
    this.chart?.dispose()
    super.destroy()
  }
}
