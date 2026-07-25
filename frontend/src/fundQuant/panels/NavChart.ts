import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'
import { getChartTheme } from '../../fundQuantCharts'

interface FundCache {
  navData: any[]
  buySignals: { date: string; nav: number }[]
  sellSignals: { date: string; nav: number }[]
}

export class NavChart extends PanelBase {
  private chart: echarts.ECharts | null = null
  private unsub: (() => void) | null = null
  /** 当前显示的基金代码 */
  private currentCode: string = ''
  /** 按基金代码缓存净值+信号 */
  private fundCache: Map<string, FundCache> = new Map()
  /** 已经尝试过收集数据的基金（避免无限循环） */
  private collectedFunds: Set<string> = new Set()
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
      if (days !== this.activeDays && this.fundCache.size) {
        this.activeDays = days
        this.renderChartWithFilter()
      }
    })
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const select = this.el.querySelector<HTMLSelectElement>('.nav-fund-select')
    const pool = state.get('fundPool')

    // 更新下拉列表
    if (select) {
      const currentVal = select.value
      select.innerHTML = pool.map(f =>
        `<option value="${f.fund_code}"${f.fund_code === currentVal ? ' selected' : ''}>${f.fund_name || f.fund_code}</option>`
      ).join('')
    }

    const code = state.get('selectedFund') || pool[0]?.fund_code
    if (!code) {
      const chartEl = this.el?.querySelector<HTMLElement>('.panel-chart')
      if (chartEl) {
        chartEl.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">暂无基金数据，请先在"基金管理"中添加基金</div>'
        chartEl.classList.remove('skeleton')
      }
      return
    }
    if (select) select.value = code

    // 有缓存 → 直接渲染，不重新请求
    const cached = this.fundCache.get(code)
    if (cached) {
      this.currentCode = code
      this.clearECharts()
      this.renderChartWithFilter()
      return
    }

    // 无缓存 → 展示加载中，开始获取
    this.showLoading()
    this.currentCode = code

    await this.fetchAndRender(code)
  }

  /** 获取净值数据并渲染（带数据收集兜底） */
  private async fetchAndRender(code: string, retried = false): Promise<void> {
    try {
      const navRes = await fundQuantApi.getNav(code)
      if (code !== this.currentCode) return
      const navData = navRes.data?.nav_history || []
      if (!navData.length) {
        // 没有净值数据：尝试收集一次
        if (!retried && !this.collectedFunds.has(code)) {
          this.collectedFunds.add(code)
          const name = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || code
          this.showCollecting(name)
          try {
            await fundQuantApi.collectNavData([code], 5)
          } catch { /* 收集失败仍尝试获取 */ }
          // 收集完再试一次
          await this.fetchAndRender(code, true)
          return
        }
        // 收集过后依然没有数据
        this.showNoData(code)
        return
      }

      // 缓存数据
      const sigRes = await fundQuantApi.getSignals(code, 50)
      if (code !== this.currentCode) return
      const signals = (sigRes.data || []).filter(s => s.direction === 'buy' || s.direction === 'sell')
      const buySignals = signals.filter(s => s.direction === 'buy').map(s => ({
        date: (s.created_at || '').slice(0, 10), nav: 0,
      }))
      const sellSignals = signals.filter(s => s.direction === 'sell').map(s => ({
        date: (s.created_at || '').slice(0, 10), nav: 0,
      }))

      for (const pt of [...buySignals, ...sellSignals]) {
        const match = navData.find((d: any) => (d.date || '').slice(0, 10) === pt.date)
        pt.nav = match ? (match.nav || (match.adjusted_nav ?? 0)) : 0
      }

      if (code !== this.currentCode) return
      this.fundCache.set(code, { navData, buySignals, sellSignals })
      this.renderChartWithFilter()
    } catch {
      if (!retried && !this.collectedFunds.has(code)) {
        // 请求异常（404 等）：尝试收集一次
        this.collectedFunds.add(code)
        const name = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || code
        this.showCollecting(name)
        try { await fundQuantApi.collectNavData([code], 5) } catch { /* ignore */ }
        await this.fetchAndRender(code, true)
        return
      }
      if (this.currentCode === code) {
        this.fundCache.delete(code)
      }
      this.showNoData(code)
    }
  }

  private clearECharts(): void {
    this.chart?.dispose()
    this.chart = null
  }

  /** 显示加载中状态 */
  private showLoading(): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return
    this.chart?.dispose()
    this.chart = null
    chartEl.classList.remove('skeleton')
    chartEl.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">加载中...</div>'
  }

  /** 显示数据收集中 */
  private showCollecting(name: string): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return
    chartEl.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">正在收集 ${name} 数据...</div>`
  }

  /** 显示无数据提示 */
  private showNoData(fundCode: string): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return
    const name = state.get('fundPool').find(f => f.fund_code === fundCode)?.fund_name || fundCode
    chartEl.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">${name} 暂无净值数据</div>`
  }

  private filterData<T extends { date: string }>(data: T[], days: number): T[] {
    if (days >= 9999) return data
    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - days)
    return data.filter(d => new Date(d.date) >= cutoff)
  }

  private renderChartWithFilter(): void {
    const cached = this.fundCache.get(this.currentCode)
    if (!cached || !cached.navData.length) return
    const filtered = this.filterData(cached.navData, this.activeDays)
    const validDates = new Set(filtered.map((d: any) => (d.date || '').slice(0, 10)))
    this.renderChart(
      filtered,
      cached.buySignals.filter(s => validDates.has(s.date)),
      cached.sellSignals.filter(s => validDates.has(s.date)),
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
    this.clearECharts()

    let peak = -Infinity
    const drawdown = navData.map((d: any) => {
      const nav = d.nav || d.adjusted_nav || 0
      peak = Math.max(peak, nav)
      return { date: (d.date || '').slice(0, 10), value: (nav / peak - 1) * 100 }
    })

    const isDark = document.body.classList.contains('dark-mode')
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

  /** Tab 激活时修复 ECharts 尺寸 */
  onActivated(): void {
    this.chart?.resize()
  }
}
