import { PanelBase } from '../layout'
import { fundQuantApi, type AuroraBacktestResult } from '../api'
import { state } from '../state'

/**
 * 策略回测面板 — 用 AuroraCore 统一引擎跑 etf_rotation / all_weather
 *
 * 选策略 + 模式 → 拉基金池 → 回测 → 展示指标。
 */
export class StrategyBacktest extends PanelBase {
  private running = false

  constructor() {
    super({ id: 'strategy-backtest', title: '策略回测', defaultGridPos: { x: 4, y: 3, w: 3, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-strategy-backtest'
    el.innerHTML = `
      <div class="panel-header">
        <h3>📈 策略回测</h3>
        <div class="panel-toolbar">
          <select class="sb-strategy">
            <option value="etf_rotation_aurora">ETF动量轮动</option>
            <option value="all_weather_aurora">桥水全天候</option>
          </select>
          <select class="sb-mode" style="display:none;">
            <option value="fixed">固定权重</option>
            <option value="risk_parity">风险平价</option>
          </select>
          <input class="sb-start" type="date" value="2021-01-01" style="font-size:11px;padding:2px 4px;">
          <button class="btn btn-sm btn-primary sb-run" style="font-weight:600;">▶ 回测</button>
        </div>
      </div>
      <div class="sb-body">
        <div class="sb-metrics"></div>
        <div class="sb-msg" style="font-size:12px;color:var(--text-tertiary);padding:8px;">选择策略后点击「回测」</div>
      </div>`
    return el
  }

  protected afterMount(): void {
    const strategySel = this.el?.querySelector('.sb-strategy') as HTMLSelectElement
    const modeSel = this.el?.querySelector('.sb-mode') as HTMLSelectElement
    const runBtn = this.el?.querySelector('.sb-run') as HTMLButtonElement

    strategySel?.addEventListener('change', () => {
      modeSel!.style.display = strategySel.value === 'all_weather_aurora' ? '' : 'none'
    })

    runBtn?.addEventListener('click', () => this.run())
  }

  private async run(): Promise<void> {
    if (this.running) return
    this.running = true
    const btn = this.el?.querySelector('.sb-run') as HTMLButtonElement
    if (btn) btn.textContent = '回测中…'
    try {
      const strategySel = this.el?.querySelector('.sb-strategy') as HTMLSelectElement
      const modeSel = this.el?.querySelector('.sb-mode') as HTMLSelectElement
      const startInput = this.el?.querySelector('.sb-start') as HTMLInputElement
      const strategy = strategySel?.value || 'etf_rotation_aurora'
      const end = new Date().toISOString().slice(0, 10)
      const codes = state.get('fundPool').map(f => f.fund_code)
      if (!codes.length) {
        this.showError('基金池为空')
        return
      }
      const params: Record<string, any> = {}
      if (strategy === 'all_weather_aurora') params.mode = modeSel?.value || 'fixed'

      const res = await fundQuantApi.runAuroraBacktest({
        fund_codes: codes, start_date: startInput?.value || '2021-01-01', end_date: end,
        initial_capital: 100000, strategy_name: strategy, params,
      })
      if (!res.success) throw new Error('回测失败')
      this.renderMetrics(res.data)
    } catch (e: any) {
      this.showError(e?.message || '回测失败')
    } finally {
      this.running = false
      if (btn) btn.textContent = '▶ 回测'
    }
  }

  private renderMetrics(d: AuroraBacktestResult): void {
    const metricsEl = this.el?.querySelector('.sb-metrics')
    const msgEl = this.el?.querySelector('.sb-msg')
    if (!metricsEl) return
    const name = d.strategy === 'all_weather_aurora' ? `全天候(${d.mode || 'fixed'})` : 'ETF动量轮动'
    metricsEl.innerHTML = `
      <div class="sb-kpis">
        <div class="sb-kpi"><span class="k-label">策略</span><span class="k-val">${name}</span></div>
        <div class="sb-kpi"><span class="k-label">总收益</span><span class="k-val ${d.total_return >= 0 ? 'k-pos' : 'k-neg'}">${(d.total_return * 100).toFixed(1)}%</span></div>
        <div class="sb-kpi"><span class="k-label">年化</span><span class="k-val">${(d.annual_return * 100).toFixed(1)}%</span></div>
        <div class="sb-kpi"><span class="k-label">夏普</span><span class="k-val">${d.sharpe_ratio.toFixed(2)}</span></div>
        <div class="sb-kpi"><span class="k-label">最大回撤</span><span class="k-val k-neg">${(d.max_drawdown * 100).toFixed(1)}%</span></div>
        <div class="sb-kpi"><span class="k-label">年化波动</span><span class="k-val">${(d.volatility * 100).toFixed(1)}%</span></div>
        <div class="sb-kpi"><span class="k-label">交易</span><span class="k-val">${d.n_trades}笔</span></div>
      </div>
      <div class="sb-funds" style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">基金池: ${d.funds.join(' / ')}</div>`
    if (msgEl) msgEl.textContent = ''
  }

  private showError(msg: string): void {
    const metricsEl = this.el?.querySelector('.sb-metrics')
    const msgEl = this.el?.querySelector('.sb-msg')
    if (metricsEl) metricsEl.innerHTML = ''
    if (msgEl) msgEl.textContent = `❌ ${msg}`
  }

  async refresh(): Promise<void> { /* 手动触发，不自动刷新 */ }

  destroy(): void {
    super.destroy()
  }
}
