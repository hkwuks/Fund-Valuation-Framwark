import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type BacktestResult } from '../api'
import { state } from '../state'

export class BacktestPanel extends PanelBase {
  private chart: echarts.ECharts | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null

  constructor() {
    super({ id: 'backtest', title: '回测', defaultGridPos: { x: 0, y: 5, w: 2, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-backtest'
    el.innerHTML = `
      <div class="panel-header">
        <h3>回测</h3>
      </div>
      <div class="bt-form" style="display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;padding:8px 12px;border-bottom:1px solid var(--border-light);">
        <select class="bt-strategy" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
          <option value="">— 选择策略 —</option>
        </select>
        <select class="bt-fund" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">
          <option value="">— 选择基金 —</option>
        </select>
        <div style="display:flex;gap:4px;">
          <input type="date" class="bt-start" value="2024-01-01" style="flex:1;padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;font-size:11px;background:var(--bg-primary);color:var(--text-primary);">
          <input type="date" class="bt-end" value="2025-12-31" style="flex:1;padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;font-size:11px;background:var(--bg-primary);color:var(--text-primary);">
        </div>
        <div style="display:flex;gap:4px;">
          <input type="number" class="bt-capital" value="100000" step="10000" min="10000" style="flex:1;width:80px;padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;font-size:11px;background:var(--bg-primary);color:var(--text-primary);">
          <select class="bt-freq" style="padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;font-size:11px;background:var(--bg-primary);color:var(--text-primary);">
            <option value="monthly">月频</option><option value="weekly">周频</option><option value="quarterly">季频</option><option value="yearly">年频</option>
          </select>
          <button class="btn btn-sm btn-primary bt-run" style="white-space:nowrap;">运行</button>
        </div>
      </div>
      <div class="bt-body" style="overflow-y:auto;flex:1;">
        <div class="bt-result-area" style="padding:8px 12px;">
          <div class="bt-placeholder" style="text-align:center;color:var(--text-tertiary);font-size:13px;padding:20px;">选择策略和基金，点击「运行」开始回测</div>
        </div>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.loadStrategyOptions()
    this.loadFundOptions()

    state.on('fundPool', () => this.loadFundOptions())

    this.el?.querySelector('.bt-run')?.addEventListener('click', () => this.runBacktest())
  }

  private async loadStrategyOptions(): Promise<void> {
    const sel = this.el?.querySelector<HTMLSelectElement>('.bt-strategy')
    if (!sel) return
    try {
      const res = await fundQuantApi.getStrategyList()
      const strategies = (res.data || []).filter(s => s.type === 'timing')
      sel.innerHTML = `<option value="">— 选择策略 —</option>
        ${strategies.map(s => `<option value="${s.name}">${s.display_name || s.name}</option>`).join('')}`
    } catch { /* keep default */ }
  }

  private loadFundOptions(): void {
    const sel = this.el?.querySelector<HTMLSelectElement>('.bt-fund')
    if (!sel) return
    const pool = state.get('fundPool')
    sel.innerHTML = `<option value="">— 选择基金 —</option>
      ${pool.map(f => `<option value="${f.fund_code}">${f.fund_name || f.fund_code}</option>`).join('')}`
  }

  private async runBacktest(): Promise<void> {
    const strategy = (this.el?.querySelector('.bt-strategy') as HTMLSelectElement)?.value
    const fundCode = (this.el?.querySelector('.bt-fund') as HTMLSelectElement)?.value
    const start = (this.el?.querySelector('.bt-start') as HTMLInputElement)?.value
    const end = (this.el?.querySelector('.bt-end') as HTMLInputElement)?.value
    const capital = parseFloat((this.el?.querySelector('.bt-capital') as HTMLInputElement)?.value || '100000')
    const freq = (this.el?.querySelector('.bt-freq') as HTMLSelectElement)?.value || 'monthly'

    if (!strategy || !fundCode) return

    const area = this.el?.querySelector('.bt-result-area')
    if (!area) return
    area.innerHTML = '<div class="bt-loading" style="text-align:center;color:var(--text-tertiary);padding:12px;">⏳ 回测任务已提交（异步执行）...</div>'

    try {
      const res = await fundQuantApi.runBacktest({
        strategy_name: strategy,
        fund_codes: [fundCode],
        start_date: start,
        end_date: end,
        initial_capital: capital,
        rebalance_freq: freq,
      })
      const id = res.data?.backtest_id
      if (!id) throw new Error('no backtest id')

      this.pending.unshift({
        backtestId: id,
        strategy,
        label: `${fundCode} ${start}~${end}`,
        createdAt: Date.now(),
      })

      // 开始轮询结果
      this.pollResult(id)
    } catch {
      area.innerHTML = '<div class="bt-error" style="text-align:center;color:var(--danger-color);padding:12px;">回测启动失败</div>'
    }
  }

  private pollResult(backtestId: string): void {
    if (this.pollTimer) clearInterval(this.pollTimer)
    this.pollTimer = setInterval(async () => {
      try {
        const res = await fundQuantApi.getBacktest(backtestId)
        const data = res.data
        if (!data || data.status === 'pending') return

        if (this.pollTimer) {
          clearInterval(this.pollTimer)
          this.pollTimer = null
        }

        if (data.status === 'failed') {
          const area = this.el?.querySelector('.bt-result-area')
          if (area) area.innerHTML = '<div class="bt-error" style="text-align:center;color:var(--danger-color);padding:12px;">回测执行失败</div>'
          return
        }

        // 成功 → 渲染结果
        this.result = data
        this.renderResult(data)
      } catch {
        // result not ready yet, keep polling
      }
    }, 2000)
  }

  private renderResult(data: BacktestResult): void {
    const area = this.el?.querySelector('.bt-result-area')
    if (!area) return
    const r = data.result || data as any

    area.innerHTML = `
      <div class="bt-metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px;">
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">累计收益</div>
          <div style="font-size:16px;font-weight:700;${(r.total_return||0) >= 0 ? 'color:var(--danger-color)' : 'color:var(--success-color)'}">${((r.total_return||0)*100).toFixed(1)}%</div>
        </div>
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">年化收益</div>
          <div style="font-size:16px;font-weight:700;${(r.annual_return||0) >= 0 ? 'color:var(--danger-color)' : 'color:var(--success-color)'}">${((r.annual_return||0)*100).toFixed(1)}%</div>
        </div>
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">最大回撤</div>
          <div style="font-size:16px;font-weight:700;color:var(--success-color)">${((r.max_drawdown||0)*100).toFixed(1)}%</div>
        </div>
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">夏普比率</div>
          <div style="font-size:16px;font-weight:700;color:var(--text-primary)">${(r.sharpe_ratio||0).toFixed(2)}</div>
        </div>
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">胜率</div>
          <div style="font-size:16px;font-weight:700;color:var(--text-primary)">${((r.win_rate||0)*100).toFixed(0)}%</div>
        </div>
        <div class="bt-card" style="background:var(--bg-tertiary);border-radius:6px;padding:6px 10px;">
          <div style="font-size:10px;color:var(--text-secondary);">交易次数</div>
          <div style="font-size:16px;font-weight:700;color:var(--text-primary)">${r.total_trades||0}</div>
        </div>
      </div>
      <div class="bt-chart" style="height:150px;margin-bottom:4px;"></div>
      <div class="bt-info" style="font-size:11px;color:var(--text-tertiary);text-align:right;">回测ID: ${data.backtest_id?.slice(0, 16)}…</div>`

    // 渲染权益曲线
    const equity = r.equity_curve || []
    const chartEl = area.querySelector<HTMLElement>('.bt-chart')
    if (chartEl && equity.length) {
      this.renderEquityChart(chartEl, equity)
    }
  }

  private renderEquityChart(chartEl: HTMLElement, equity: { date?: string; total_value?: number; equity?: number }[]): void {
    this.chart?.dispose()
    this.chart = echarts.init(chartEl)
    const dates = equity.map(e => (e.date || '').slice(5, 10) || String(e.bar || ''))
    const values = equity.map(e => e.total_value ?? e.equity ?? 0)
    const base = values[0] || 1
    const pct = values.map(v => ((v - base) / base * 100))

    this.chart.setOption({
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v.toFixed(1)}%` },
      grid: { left: '2%', right: '2%', bottom: '6%', top: '4%', containLabel: true },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 9, interval: 'auto' } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9, formatter: '{value}%' } },
      series: [{
        type: 'line', data: pct, smooth: true,
        lineStyle: { color: '#3b82f6', width: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(59,130,246,0.2)' },
          { offset: 1, color: 'rgba(59,130,246,0)' },
        ])},
        symbol: 'none',
      }],
    })
  }

  async refresh(): Promise<void> {
    this.loadStrategyOptions()
    this.loadFundOptions()
  }

  destroy(): void {
    if (this.pollTimer) clearInterval(this.pollTimer)
    this.chart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
  }
}
