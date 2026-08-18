import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'
import { getChartTheme, DARK_SERIES, LIGHT_SERIES } from '../../fundQuantCharts'

interface FundCache {
  navData: any[]
  benchmarkData?: { date: string; value: number }[]
}

/** 沪深300近似代码（用于基准对比） */
const BENCHMARK_CODE = '000300'

export class NavChart extends PanelBase {
  private chart: echarts.ECharts | null = null
  private unsub: (() => void) | null = null
  private currentCode: string = ''
  private fundCache: Map<string, FundCache> = new Map()
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
          <label style="font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:4px;">
            <input type="checkbox" class="nav-toggle-benchmark" checked> 基准
          </label>
          <button class="btn btn-sm btn-outline nav-research-btn" title="择时研究" style="font-size:11px;">🔬 研究</button>
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

    this.el?.querySelector('.nav-toggle-benchmark')?.addEventListener('change', () => {
      if (this.currentCode) this.renderChartWithFilter()
    })

    // 研究按钮 → 展开研究区
    this.el?.querySelector('.nav-research-btn')?.addEventListener('click', () => {
      const code = this.currentCode || state.get('selectedFund')
      console.log('[NavChart] 研究按钮点击, fundCode:', code)
      if (code) {
        const fundName = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || ''
        state.set('researchPanel', {
          visible: true, activeTab: 'exposure', fundCode: code,
          signal: { direction: 'hold' as any, confidence: 0, strategy_name: '', timestamp: '', fund_code: code, fund_name: fundName } as any,
        })
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
    if (!code) {
      const chartEl = this.el?.querySelector<HTMLElement>('.panel-chart')
      if (chartEl) {
        chartEl.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">暂无基金数据，请先在"基金管理"中添加基金</div>'
        chartEl.classList.remove('skeleton')
      }
      return
    }
    if (select) select.value = code

    const cached = this.fundCache.get(code)
    if (cached) {
      this.currentCode = code
      this.renderChartWithFilter()
      return
    }

    this.showLoading()
    this.currentCode = code
    await this.fetchAndRender(code)
  }

  private async fetchAndRender(code: string, retried = false): Promise<void> {
    try {
      const navRes = await fundQuantApi.getNav(code)
      if (code !== this.currentCode) return
      const navData = navRes.data?.nav_history || []
      if (!navData.length) {
        if (!retried && !this.collectedFunds.has(code)) {
          this.collectedFunds.add(code)
          const name = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || code
          this.showCollecting(name)
          try { await fundQuantApi.collectNavData([code], 5) } catch { /* ignore */ }
          await this.fetchAndRender(code, true)
          return
        }
        this.showNoData(code)
        return
      }

      // 获取基准数据（沪深300）
      const benchRes = await fundQuantApi.getNav(BENCHMARK_CODE).catch(() => null)
      const benchmarkData = benchRes?.data?.nav_history?.map((d: any) => ({
        date: (d.date || '').slice(0, 10),
        value: d.nav || d.adjusted_nav || 0,
      })) || undefined

      if (code !== this.currentCode) return
      this.fundCache.set(code, { navData, benchmarkData })
      this.renderChartWithFilter()
    } catch {
      if (!retried && !this.collectedFunds.has(code)) {
        this.collectedFunds.add(code)
        const name = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || code
        this.showCollecting(name)
        try { await fundQuantApi.collectNavData([code], 5) } catch { /* ignore */ }
        await this.fetchAndRender(code, true)
        return
      }
      if (this.currentCode === code) this.fundCache.delete(code)
      this.showNoData(code)
    }
  }

  private showLoading(): void {
    this.setChartContent('<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">加载中...</div>')
  }

  private showCollecting(name: string): void {
    this.setChartContent(`<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">正在收集 ${name} 数据...</div>`)
  }

  private showNoData(fundCode: string): void {
    const name = state.get('fundPool').find(f => f.fund_code === fundCode)?.fund_name || fundCode
    this.setChartContent(`<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">${name} 暂无净值数据</div>`)
  }

  private setChartContent(html: string): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return
    this.chart?.dispose()
    this.chart = null
    chartEl.classList.remove('skeleton')
    chartEl.innerHTML = html
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
    const showBenchmark = (this.el?.querySelector('.nav-toggle-benchmark') as HTMLInputElement)?.checked ?? true
    this.renderChart(
      filtered,
      showBenchmark ? cached.benchmarkData : undefined,
    )
  }

  private renderChart(
    navData: any[],
    benchmarkData?: { date: string; value: number }[],
  ): void {
    if (!this.el) return
    const chartEl = this.el.querySelector<HTMLElement>('.panel-chart')
    if (!chartEl) return

    chartEl.classList.remove('skeleton')
    this.chart?.dispose()
    this.chart = null

    const dates = navData.map((d: any) => (d.date || '').slice(0, 10))
    const navValues = navData.map((d: any) => d.nav || d.adjusted_nav)

    let peak = -Infinity
    const drawdown = navData.map((d: any) => {
      const nav = d.nav || d.adjusted_nav || 0
      peak = Math.max(peak, nav)
      return (nav / peak - 1) * 100
    })

    // 基准归一化（以基金日期范围内的第一个值为基准）
    const isDark = document.body.classList.contains('dark-mode')
    const C = isDark ? DARK_SERIES : LIGHT_SERIES
    let benchmarkSeries: any = undefined
    if (benchmarkData && benchmarkData.length > 2) {
      const benchMap = new Map(benchmarkData.map(d => [d.date, d.value]))
      const aligned = dates.map(date => benchMap.get(date) ?? null).filter(v => v !== null) as number[]
      if (aligned.length > 5) {
        const base = aligned[0]
        const benchNormalized = dates.map(d => {
          const v = benchMap.get(d)
          return v != null ? (v / base) * navValues[0] : null
        })
        benchmarkSeries = {
          name: '沪深300',
          type: 'line',
          data: benchNormalized,
          smooth: true,
          lineStyle: { width: 1.5, color: C.benchmark, type: 'dashed' },
          symbol: 'none',
        }
      }
    }

    this.chart = echarts.init(chartEl)
    const theme = getChartTheme(isDark)

    const axColor = isDark ? '#8494ad' : '#64748b'
    const axLineColor = isDark ? '#1e2d42' : '#cbd5e1'
    const gridColor = isDark ? '#152238' : '#e2e8f0'

    const option: echarts.EChartsOption = {
      ...theme,
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      legend: {
        data: ['净值', '沪深300', '买入信号', '卖出信号', '回撤'],
        bottom: 0,
        textStyle: { fontSize: 11 },
      },
      grid: { left: '3%', right: '4%', bottom: '18%', top: '4%', containLabel: true },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { rotate: 45, fontSize: 11, color: axColor },
        axisLine: { lineStyle: { color: axLineColor } },
        axisTick: { lineStyle: { color: axLineColor } },
        splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
      },
      yAxis: [
        {
          type: 'value', scale: true, name: '净值',
          axisLabel: { color: axColor },
          nameTextStyle: { color: axColor },
          splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
        },
        {
          type: 'value', scale: true, name: '回撤%', min: -30, max: 5,
          axisLabel: { formatter: '{value}%', color: axColor },
          nameTextStyle: { color: axColor },
          splitLine: { show: false },
        },
      ],
      // 框选放大
      dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none' }],
      brush: { toolbox: ['rect', 'keep', 'clear'], xAxisIndex: 0 },
      toolbox: {
        feature: {
          brush: { type: ['rect', 'keep', 'clear'] },
          restore: {},
          dataZoom: { yAxisIndex: 'none' },
        },
        right: 10,
        top: 4,
      },
      series: [
        {
          name: '净值', type: 'line',
          data: navValues,
          smooth: true,
          lineStyle: { width: 2, color: C.nav },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: C.navArea },
              { offset: 1, color: isDark ? 'rgba(74,144,217,0)' : 'rgba(59,130,246,0)' },
            ]),
          },
        },
        ...(benchmarkSeries ? [benchmarkSeries] : []),
        {
          name: '回撤', type: 'line',
          data: drawdown,
          smooth: true, yAxisIndex: 1,
          lineStyle: { width: 1.5, color: C.drawdown, type: 'dashed' as const },
          areaStyle: { color: C.drawdownArea },
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

  onActivated(): void {
    this.chart?.resize()
  }
}
