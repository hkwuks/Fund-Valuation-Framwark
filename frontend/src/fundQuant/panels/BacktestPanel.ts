import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type BacktestResult, type AnalysisResult } from '../api'
import { state } from '../state'

export class BacktestPanel extends PanelBase {
  private chart: echarts.ECharts | null = null
  private pollTimer: ReturnType<typeof setInterval> | null = null
  private analysisChart: echarts.ECharts | null = null
  private backtestId: string = ''

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
    area.innerHTML = '<div class="bt-loading" style="text-align:center;color:var(--text-tertiary);padding:12px;">⏳ 回测运行中...</div>'

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

      // 轮询结果
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

        this.backtestId = backtestId
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
      <div class="bt-metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:6px;">
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
      <div class="bt-tabs" style="display:flex;gap:0;margin-bottom:6px;border-bottom:1px solid var(--border-light);">
        <button class="bt-tab bt-tab-active" data-tab="equity">权益曲线</button>
        <button class="bt-tab" data-tab="analysis">分析</button>
        <button class="bt-tab" data-tab="log">交易日志</button>
      </div>
      <div class="bt-tab-content">
        <div class="bt-tab-pane bt-pane-equity bt-pane-active">
          <div class="bt-chart" style="height:150px;"></div>
        </div>
        <div class="bt-tab-pane bt-pane-analysis" style="display:none;">
          <div style="margin-bottom:6px;">
            <button class="btn btn-sm btn-primary bt-run-analysis">运行分析</button>
            <span class="bt-analysis-loading" style="display:none;margin-left:8px;font-size:12px;color:var(--text-tertiary);">⏳ 计算中...</span>
          </div>
          <div class="bt-analysis-results"></div>
        </div>
        <div class="bt-tab-pane bt-pane-log" style="display:none;">
          <div class="bt-log-list" style="font-size:11px;max-height:180px;overflow-y:auto;"></div>
        </div>
      </div>
      <div class="bt-info" style="font-size:11px;color:var(--text-tertiary);text-align:right;margin-top:4px;">回测ID: ${data.backtest_id?.slice(0, 16)}…</div>`

    // 渲染权益曲线
    const equity = r.equity_curve || []
    const chartEl = area.querySelector<HTMLElement>('.bt-chart')
    if (chartEl && equity.length) {
      this.renderEquityChart(chartEl, equity)
    }

    // 渲染交易日志
    const tradeLog = r.trade_log || []
    const logEl = area.querySelector('.bt-log-list')
    if (logEl) {
      if (tradeLog.length) {
        logEl.innerHTML = tradeLog.map((t: any) =>
          `<div style="padding:2px 0;border-bottom:1px solid var(--border-light);">
            ${t.date || ''} | ${t.action || t.type || ''} | ${t.fund_code || t.symbol || ''} | ${t.shares ? t.shares + '份' : ''} ${t.amount ? '¥'+t.amount : ''}
          </div>`
        ).join('')
      } else {
        logEl.innerHTML = '<div style="padding:8px;color:var(--text-tertiary);text-align:center;">无交易记录</div>'
      }
    }

    // Tab 切换
    this.bindTabEvents()

    // 分析按钮
    area.querySelector('.bt-run-analysis')?.addEventListener('click', () => this.runAnalysis())
  }

  private bindTabEvents(): void {
    this.el?.querySelector('.bt-tabs')?.addEventListener('click', (e) => {
      const tab = (e.target as HTMLElement)?.closest('.bt-tab')
      if (!tab) return
      const target = (tab as HTMLElement).dataset.tab
      this.el?.querySelectorAll('.bt-tab').forEach(t => t.classList.remove('bt-tab-active'))
      tab.classList.add('bt-tab-active')
      this.el?.querySelectorAll('.bt-tab-pane').forEach(p => (p as HTMLElement).style.display = 'none')
      const pane = this.el?.querySelector(`.bt-pane-${target}`) as HTMLElement
      if (pane) pane.style.display = 'block'
      if (target === 'equity') setTimeout(() => this.chart?.resize(), 50)
    })
  }

  private async runAnalysis(): Promise<void> {
    if (!this.backtestId) return
    const btn = this.el?.querySelector('.bt-run-analysis') as HTMLButtonElement
    const loading = this.el?.querySelector('.bt-analysis-loading') as HTMLElement
    if (btn) btn.disabled = true
    if (loading) loading.style.display = 'inline'
    try {
      const res = await fundQuantApi.runAnalysis(this.backtestId)
      if (res.success) {
        this.renderAnalysis(res.data.analysis)
        if (btn) btn.textContent = '重新运行'
      }
    } catch {
      const container = this.el?.querySelector('.bt-analysis-results')
      if (container) container.innerHTML = '<div style="color:var(--danger-color);padding:8px;font-size:12px;">分析运行失败</div>'
    } finally {
      if (btn) btn.disabled = false
      if (loading) loading.style.display = 'none'
    }
  }

  private renderAnalysis(data: AnalysisResult): void {
    const container = this.el?.querySelector('.bt-analysis-results')
    if (!container) return

    const sections: { title: string; render: () => string }[] = []

    // 过拟合检测
    if (data.overfitting) {
      const o = data.overfitting
      const btlWarn = o.min_btl_warning
        ? `<span style="color:var(--warning-color);font-size:11px;">⚠ ${o.min_btl_warning}</span>`
        : ''
      sections.push({
        title: '过拟合检测',
        render: () => `
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;">
            <div class="ana-card"><div class="ana-label">Deflated Sharpe</div><div class="ana-value">${o.deflated_sharpe.toFixed(2)}</div></div>
            <div class="ana-card"><div class="ana-label">MinBTL</div><div class="ana-value">${o.min_btl_years.toFixed(1)}y ${btlWarn}</div></div>
            <div class="ana-card"><div class="ana-label">Shuffle p</div><div class="ana-value">${o.shuffle_p_value.toFixed(4)}</div></div>
            <div class="ana-card"><div class="ana-label">结论</div><div class="ana-value" style="color:${o.is_significant ? 'var(--danger-color)' : 'var(--text-tertiary)'}">${o.is_significant ? '✅ 显著' : '❌ 不显著'}</div></div>
          </div>`
      })
    }

    // 显著性检验
    if (data.significance) {
      const s = data.significance
      sections.push({
        title: '显著性检验',
        render: () => `
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;">
            <div class="ana-card"><div class="ana-label">观测 Sharpe</div><div class="ana-value">${s.sharpe.toFixed(2)}</div></div>
            <div class="ana-card"><div class="ana-label">p-value</div><div class="ana-value">${s.p_value.toFixed(4)}</div></div>
            <div class="ana-card"><div class="ana-label">95% CI</div><div class="ana-value" style="font-size:12px;">[${s.ci_lower.toFixed(2)}, ${s.ci_upper.toFixed(2)}]</div></div>
          </div>`
      })
    }

    // Monte Carlo
    if (data.monte_carlo) {
      const mc = data.monte_carlo
      const rp = mc.return_pct
      sections.push({
        title: 'Monte Carlo 模拟',
        render: () => {
          // 延迟渲染直方图
          setTimeout(() => this.renderMcChart(mc), 50)
          return `
            <div class="bt-mc-chart" style="height:80px;margin-bottom:6px;"></div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;">
              <div class="ana-card"><div class="ana-label">收益均值/中位</div><div class="ana-value">${rp.mean}% / ${rp.median}%</div></div>
              <div class="ana-card"><div class="ana-label">p5 - p95</div><div class="ana-value">${rp.p5}% ~ ${rp.p95}%</div></div>
              <div class="ana-card"><div class="ana-label">亏损概率</div><div class="ana-value" style="color:${mc.probability_of_loss > 30 ? 'var(--danger-color)' : 'var(--text-primary)'}">${mc.probability_of_loss}%</div></div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:4px;">
              <div class="ana-card"><div class="ana-label">Sharpe 均值</div><div class="ana-value">${(mc.sharpe_ratio.mean||0).toFixed(2)}</div></div>
              <div class="ana-card"><div class="ana-label">最大回撤均值</div><div class="ana-value">${mc.max_drawdown_pct.mean}%</div></div>
              <div class="ana-card"><div class="ana-label">Ulcer</div><div class="ana-value">${(mc.ulcer_index?.mean || '-')}</div></div>
            </div>`
        }
      })
    }

    // 市场状态
    if (data.regime) {
      const r = data.regime
      sections.push({
        title: '市场状态',
        render: () => {
          const bar = r.regimes.map(rg =>
            `<div style="margin:2px 0;font-size:11px;">
              <span class="regime-dot regime-${rg.label}"></span>
              <span style="font-weight:600;">${{
                'high_volatility': '高波动', 'normal': '正常', 'low_volatility': '低波动'
              }[rg.label] || rg.label}</span>
              <span style="color:var(--text-secondary);margin-left:8px;">${rg.duration_days}天</span>
              <span style="${rg.ann_return >= 0 ? 'color:var(--danger-color)' : 'color:var(--success-color)'};margin-left:8px;">${(rg.ann_return * 100).toFixed(1)}%</span>
              <span style="color:var(--text-secondary);margin-left:8px;">Sharpe ${rg.sharpe.toFixed(2)}</span>
            </div>`
          ).join('')
          const warn = r.warning ? `<div style="font-size:11px;color:var(--warning-color);margin-top:4px;">⚠ ${r.warning}</div>` : ''
          return bar + warn
        }
      })
    }

    // 因子归因
    if (data.factor_attribution) {
      const fa = data.factor_attribution
      const betas = Object.entries(fa.betas).map(([k, v]) =>
        `<span style="font-size:11px;margin-right:12px;">${k} β=${v.toFixed(2)}</span>`
      ).join('')
      sections.push({
        title: '因子归因',
        render: () => `
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px;">
            <div class="ana-card"><div class="ana-label">Alpha (年化)</div><div class="ana-value">${(fa.alpha * 100).toFixed(2)}%</div></div>
            <div class="ana-card"><div class="ana-label">Alpha t 值</div><div class="ana-value">${fa.alpha_tstat.toFixed(2)}</div></div>
            <div class="ana-card"><div class="ana-label">R²</div><div class="ana-value">${fa.r_squared.toFixed(2)}</div></div>
          </div>
          <div style="margin-top:4px;font-size:11px;color:var(--text-secondary);">因子暴露: ${betas}</div>`
      })
    }

    if (!sections.length) {
      container.innerHTML = '<div style="padding:8px;color:var(--text-tertiary);font-size:12px;">无分析数据（净值序列或回测数据不足）</div>'
      return
    }

    container.innerHTML = sections.map((s, i) => `
      <details class="ana-section" ${i === 0 ? 'open' : ''}>
        <summary class="ana-summary" style="cursor:pointer;padding:6px 8px;background:var(--bg-tertiary);border-radius:4px;font-size:12px;font-weight:600;margin-bottom:4px;">
          ${s.title}
        </summary>
        <div class="ana-body" style="padding:6px 8px;">
          ${s.render()}
        </div>
      </details>
    `).join('')
  }

  private renderMcChart(mc: NonNullable<AnalysisResult['monte_carlo']>): void {
    const chartEl = this.el?.querySelector<HTMLElement>('.bt-mc-chart')
    if (!chartEl) return
    this.analysisChart?.dispose()
    this.analysisChart = echarts.init(chartEl)
    // 合成一个近似正态分布展示
    const rp = mc.return_pct
    const mean = rp.mean || 0
    const std = rp.std || 15
    const bins: { name: string; value: number }[] = []
    for (let i = -3; i <= 3; i++) {
      const v = mean + i * std * 0.5
      bins.push({ name: v.toFixed(0), value: Math.max(0, 100 - Math.abs(i) * 20) })
    }
    this.analysisChart.setOption({
      tooltip: { trigger: 'axis', formatter: (p: any) => `${p[0].name}%` },
      grid: { left: 2, right: 2, top: 4, bottom: 12 },
      xAxis: { type: 'category', data: bins.map(b => b.name + '%'), axisLabel: { fontSize: 8 } },
      yAxis: { show: false },
      series: [{
        type: 'bar', data: bins.map(b => b.value),
        itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#3b82f6' }, { offset: 1, color: 'rgba(59,130,246,0.3)' }
        ]) },
        barWidth: '70%',
      }],
    })
  }

  private renderEquityChart(chartEl: HTMLElement, equity: { date?: string; total_value?: number; equity?: number }[]): void {
    this.chart?.dispose()
    this.chart = echarts.init(chartEl)
    const dates = equity.map((e: any) => (e.date || '').slice(5, 10))
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
    this.analysisChart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
    this.analysisChart?.resize()
  }
}
