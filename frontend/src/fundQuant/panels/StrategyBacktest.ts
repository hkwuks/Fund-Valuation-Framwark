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
    super({ id: 'strategy-backtest', title: '策略回测', defaultGridPos: { x: 0, y: 6, w: 3, h: 1 } })
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
            <option value="bl_quadrant_aurora">BL四象限观点</option>
            <option value="black_litterman_aurora">Black-Litterman</option>
            <option value="risk_parity_aurora">风险平价</option>
            <option value="hrp_aurora">层次风险平价(HRP)</option>
            <option value="max_diversification_aurora">最大多元化(MDP)</option>
            <option value="dynamic_risk_parity_aurora">动态风险平价</option>
            <option value="vol_targeting_aurora">波动率目标</option>
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
      if (strategy === 'black_litterman_aurora') {
        const views = (state.get('blViews') as any[]) || []
        if (views.length) params.views = views
      }

      const res = await fundQuantApi.runAuroraBacktest({
        fund_codes: codes, start_date: startInput?.value || '2021-01-01', end_date: end,
        initial_capital: 100000, strategy_name: strategy, params,
      })
      if (!res.success) throw new Error('回测失败')
      // 等权买入持有基准（并行拉取；失败不阻塞策略结果展示）
      const base = await fundQuantApi.runEqualWeightBacktest({
        fund_codes: codes, start_date: startInput?.value || '2021-01-01', end_date: end,
        initial_capital: 100000,
      }).catch(() => null)
      this.renderMetrics(res.data, base?.data ?? null)
    } catch (e: any) {
      this.showError(e?.message || '回测失败')
    } finally {
      this.running = false
      if (btn) btn.textContent = '▶ 回测'
    }
  }

  private renderMetrics(d: AuroraBacktestResult, base: AuroraBacktestResult | null): void {
    const metricsEl = this.el?.querySelector('.sb-metrics')
    const msgEl = this.el?.querySelector('.sb-msg')
    if (!metricsEl) return
    const names: Record<string, string> = {
      all_weather_aurora: '全天候',
      etf_rotation_aurora: 'ETF动量轮动',
      bl_quadrant_aurora: 'BL四象限观点',
      black_litterman_aurora: 'Black-Litterman',
      risk_parity_aurora: '风险平价',
      hrp_aurora: '层次风险平价(HRP)',
      max_diversification_aurora: '最大多元化(MDP)',
      dynamic_risk_parity_aurora: '动态风险平价',
      vol_targeting_aurora: '波动率目标',
    }
    const stratName = names[d.strategy] || d.strategy
    const name = d.strategy === 'all_weather_aurora' ? `${stratName}(${d.mode || 'fixed'})` : stratName
    // 基准对比行：策略 vs 等权买入持有（含费用）
    const cmp = base && [
      { label: '总收益', s: d.total_return, b: base.total_return, pct: true },
      { label: '夏普', s: d.sharpe_ratio, b: base.sharpe_ratio },
      { label: '最大回撤', s: d.max_drawdown, b: base.max_drawdown, pct: true },
    ]
    const fmt = (v: number, pct?: boolean) => pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(2)
    const cls = (s: number, b: number) => (s > b ? 'k-pos' : 'k-neg')
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
      ${cmp ? `<div class="sb-baseline">
        <span style="font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.3px;">vs 等权买入持有</span>
        <div class="sb-kpis" style="margin-top:4px;">
          ${cmp.map(c => `
            <div class="sb-kpi">
              <span class="k-label">${c.label}</span>
              <span class="k-val ${cls(c.s, c.b)}">${fmt(c.s, c.pct)}</span>
              <span style="font-size:10px;color:var(--text-tertiary);">基准 ${fmt(c.b, c.pct)}</span>
            </div>`).join('')}
        </div>
      </div>` : ''}
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
