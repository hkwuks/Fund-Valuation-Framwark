import { PanelBase } from '../layout'
import { fundQuantApi, type AuroraBacktestResult, type StrategyParams } from '../api'
import { state } from '../state'

const PARAMS_KEY_PREFIX = 'fundQuant.strategyParams.'

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
            <option value="trend_following_aurora">趋势跟踪</option>
            <option value="gmv_aurora">最小方差(GMV)</option>
          </select>
          <select class="sb-mode" style="display:none;">
            <option value="fixed">固定权重</option>
            <option value="risk_parity">风险平价</option>
          </select>
          <input class="sb-start" type="date" value="2021-01-01" style="font-size:11px;padding:2px 4px;">
          <button class="btn btn-sm btn-ghost sb-params">参数</button>
          <button class="btn btn-sm btn-ghost sb-params-reset" hidden>重置</button>
          <button class="btn btn-sm btn-ghost sb-walk-forward" style="font-weight:600;">OOS验证</button>
          <button class="btn btn-sm btn-primary sb-run" style="font-weight:600;">▶ 回测</button>
        </div>
      </div>
      <div class="sb-params-body" hidden></div>
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
    const walkForwardBtn = this.el?.querySelector('.sb-walk-forward') as HTMLButtonElement
    const paramsBtn = this.el?.querySelector('.sb-params') as HTMLButtonElement
    const resetBtn = this.el?.querySelector('.sb-params-reset') as HTMLButtonElement

    strategySel?.addEventListener('change', () => {
      modeSel!.style.display = strategySel.value === 'all_weather_aurora' ? '' : 'none'
      this.hideParams()
    })

    paramsBtn?.addEventListener('click', () => void this.toggleParams())
    resetBtn?.addEventListener('click', () => void this.resetParams())
    walkForwardBtn?.addEventListener('click', () => void this.runWalkForward())
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
        this.showError('持仓基金池为空，请先在基金管理中添加持仓基金')
        return
      }
      const unknown = state.get('fundPool').filter(f => !f.market_type || !f.trade_mode)
      if (unknown.length) {
        this.showError(`请先在基金管理中补充交易属性：${unknown.map(f => f.fund_name || f.fund_code).join('、')}`)
        return
      }
      const params = this.readParams()
      if (strategy === 'all_weather_aurora' && params.mode === undefined) params.mode = modeSel?.value || 'fixed'
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

  private async runWalkForward(): Promise<void> {
    const strategy = (this.el?.querySelector('.sb-strategy') as HTMLSelectElement)?.value
    const start = (this.el?.querySelector('.sb-start') as HTMLInputElement)?.value || '2021-01-01'
    const codes = state.get('fundPool').map(f => f.fund_code)
    const msg = this.el?.querySelector('.sb-msg') as HTMLElement | null
    if (!strategy || !codes.length) {
      this.showError('持仓基金池为空，请先在基金管理中添加持仓基金')
      return
    }
    const unknown = state.get('fundPool').filter(f => !f.market_type || !f.trade_mode)
    if (unknown.length) {
      this.showError(`请先在基金管理中补充交易属性：${unknown.map(f => f.fund_name || f.fund_code).join('、')}`)
      return
    }
    const button = this.el?.querySelector('.sb-walk-forward') as HTMLButtonElement | null
    if (button) { button.disabled = true; button.textContent = '验证中…' }
    try {
      const response = await fundQuantApi.runWalkForward({
        strategy_name: strategy,
        fund_codes: codes,
        start_date: start,
        end_date: new Date().toISOString().slice(0, 10),
        initial_capital: 100000,
        params: this.readParams(),
      })
      const summary = response.data?.summary
      if (msg) msg.textContent = summary
        ? `OOS窗口 ${summary.valid_windows}/${summary.total_windows} · 平均收益 ${(summary.avg_return * 100).toFixed(2)}% · 平均Sharpe ${summary.avg_sharpe.toFixed(2)}`
        : (response.data?.message || 'OOS验证完成')
    } catch (error) {
      this.showError(error instanceof Error ? error.message : 'OOS验证失败')
    } finally {
      if (button) { button.disabled = false; button.textContent = 'OOS验证' }
    }
  }

  private async toggleParams(): Promise<void> {
    const body = this.el?.querySelector('.sb-params-body') as HTMLElement | null
    const strategy = (this.el?.querySelector('.sb-strategy') as HTMLSelectElement | null)?.value
    if (!body || !strategy) return
    if (!body.hidden) {
      this.hideParams()
      return
    }
    body.hidden = false
    body.textContent = '加载参数中…'
    try {
      const response = await fundQuantApi.getStrategyParams(strategy)
      this.renderParams(response.data)
    } catch (error) {
      body.textContent = `参数加载失败：${error instanceof Error ? error.message : '未知错误'}`
    }
  }

  private renderParams(strategy: StrategyParams): void {
    const body = this.el?.querySelector('.sb-params-body') as HTMLElement | null
    const resetBtn = this.el?.querySelector('.sb-params-reset') as HTMLButtonElement | null
    if (!body) return
    const saved = this.loadParams(strategy.name)
    const params = Object.entries(strategy.default_params)
      .filter(([name, value]) => typeof value === 'number' || typeof value === 'boolean' || Boolean(strategy.param_choices?.[name]))
      .map(([name, value]) => {
        const current = saved[name] ?? value
        const choices = strategy.param_choices?.[name]
        if (choices) {
          return `<label>${name} <select data-param="${name}">${choices.map(choice =>
            `<option value="${choice}"${current === choice ? ' selected' : ''}>${choice}</option>`).join('')}</select></label>`
        }
        if (typeof value === 'boolean') {
          return `<label>${name} <input data-param="${name}" type="checkbox"${current ? ' checked' : ''}></label>`
        }
        if (typeof value === 'number') {
          const range = strategy.param_ranges[name] || {}
          const step = Number.isInteger(value) ? 1 : 'any'
          return `<label>${name} <input data-param="${name}" type="number" value="${current}" min="${range.min ?? ''}" max="${range.max ?? ''}" step="${step}"></label>`
        }
        return ''
      })
      .filter(Boolean)
    body.innerHTML = params.length ? params.join('') : '该策略没有可编辑的参数'
    body.onchange = () => this.saveParams(strategy.name, this.readParams())
    if (resetBtn) resetBtn.hidden = params.length === 0
  }

  private readParams(): Record<string, unknown> {
    const params: Record<string, unknown> = {}
    this.el?.querySelectorAll<HTMLInputElement | HTMLSelectElement>('.sb-params-body [data-param]').forEach(input => {
      const name = input.dataset.param!
      if (input instanceof HTMLInputElement && input.type === 'checkbox') params[name] = input.checked
      else if (input instanceof HTMLInputElement && input.type === 'number' && input.value !== '') params[name] = Number(input.value)
      else if (input.value !== '') params[name] = input.value
    })
    return params
  }

  private storageKey(strategy: string): string {
    return PARAMS_KEY_PREFIX + strategy
  }

  private loadParams(strategy: string): Record<string, unknown> {
    try {
      const saved = localStorage.getItem(this.storageKey(strategy))
      return saved ? JSON.parse(saved) : {}
    } catch { return {} }
  }

  private saveParams(strategy: string, params: Record<string, unknown>): void {
    try { localStorage.setItem(this.storageKey(strategy), JSON.stringify(params)) } catch { /* 存储不可用时忽略 */ }
  }

  private async resetParams(): Promise<void> {
    const strategy = (this.el?.querySelector('.sb-strategy') as HTMLSelectElement | null)?.value
    const body = this.el?.querySelector('.sb-params-body') as HTMLElement | null
    if (!strategy || !body) return
    try { localStorage.removeItem(this.storageKey(strategy)) } catch { /* 存储不可用时忽略 */ }
    body.textContent = '加载参数中…'
    try {
      const response = await fundQuantApi.getStrategyParams(strategy)
      this.renderParams(response.data)
    } catch (error) {
      body.textContent = `参数加载失败：${error instanceof Error ? error.message : '未知错误'}`
    }
  }

  private hideParams(): void {
    const body = this.el?.querySelector('.sb-params-body') as HTMLElement | null
    if (body) body.hidden = true
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
      trend_following_aurora: '趋势跟踪',
      gmv_aurora: '最小方差(GMV)',
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
