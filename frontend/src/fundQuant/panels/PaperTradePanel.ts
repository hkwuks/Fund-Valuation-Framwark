import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type PaperTradeSession, type PaperTradeSummary } from '../api'
import { state } from '../state'

export class PaperTradePanel extends PanelBase {
  private activeId: string | null = null
  private miniChart: echarts.ECharts | null = null

  constructor() {
    super({ id: 'paper-trade', title: '模拟交易', defaultGridPos: { x: 0, y: 6, w: 3, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-paper-trade'
    el.style.display = 'flex'
    el.style.flexDirection = 'column'
    el.innerHTML = `
      <div class="panel-header">
        <h3>模拟交易</h3>
        <div class="panel-toolbar">
          <button class="btn btn-sm btn-outline pt-refresh" title="刷新列表">🔄</button>
        </div>
      </div>
      <div class="pt-body" style="display:flex;flex:1;overflow:hidden;">
        <div class="pt-left" style="width:200px;border-right:1px solid var(--border-light);display:flex;flex-direction:column;flex-shrink:0;">
          <div class="pt-start-area" style="padding:6px 8px;border-bottom:1px solid var(--border-light);">
            <select class="pt-strategy" style="width:100%;font-size:11px;padding:3px 4px;margin-bottom:4px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
              <option value="">策略</option>
            </select>
            <select class="pt-fund" multiple style="width:100%;font-size:10px;height:50px;padding:2px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
            </select>
            <input class="pt-capital" type="number" value="100000" style="width:100%;font-size:11px;padding:3px 4px;margin-bottom:4px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
            <button class="btn btn-sm btn-primary pt-start-btn" style="width:100%;font-size:11px;">启动</button>
          </div>
          <div class="pt-list" style="flex:1;overflow-y:auto;padding:4px 0;">
            <div style="text-align:center;color:var(--text-tertiary);font-size:11px;padding:12px;">加载中...</div>
          </div>
        </div>
        <div class="pt-right" style="flex:1;display:flex;flex-direction:column;">
          <div class="pt-detail" style="flex:1;padding:8px 12px;overflow-y:auto;">
            <div class="pt-placeholder" style="text-align:center;color:var(--text-tertiary);font-size:12px;padding:20px;">
              选择一个会话查看详情
            </div>
          </div>
        </div>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.loadStrategyOptions()
    this.loadFundOptions()
    state.on('fundPool', () => this.loadFundOptions())

    this.el?.querySelector('.pt-start-btn')?.addEventListener('click', () => this.startSession())
    this.el?.querySelector('.pt-refresh')?.addEventListener('click', () => this.refreshList())

    this.refreshList()
  }

  private loadStrategyOptions(): void {
    const sel = this.el?.querySelector<HTMLSelectElement>('.pt-strategy')
    if (!sel) return
    try {
      fundQuantApi.getStrategyList().then(res => {
        const strategies = (res.data || []).filter(s => s.type === 'timing')
        sel.innerHTML = `<option value="">策略</option>
          ${strategies.map(s => `<option value="${s.name}">${s.display_name || s.name}</option>`).join('')}`
      }).catch(() => {})
    } catch {}
  }

  private loadFundOptions(): void {
    const sel = this.el?.querySelector<HTMLSelectElement>('.pt-fund')
    if (!sel) return
    const pool = state.get('fundPool')
    sel.innerHTML = pool.map(f =>
      `<option value="${f.fund_code}">${f.fund_name || f.fund_code}</option>`
    ).join('')
  }

  private async startSession(): Promise<void> {
    const strategyName = (this.el?.querySelector('.pt-strategy') as HTMLSelectElement)?.value
    const fundSel = this.el?.querySelector('.pt-fund') as HTMLSelectElement
    const fundCodes = fundSel ? Array.from(fundSel.selectedOptions).map(o => o.value) : []
    const capital = parseFloat((this.el?.querySelector('.pt-capital') as HTMLInputElement)?.value || '100000')

    if (!strategyName || !fundCodes.length) return

    try {
      await fundQuantApi.paperTradeStart({
        strategy_name: strategyName, fund_codes: fundCodes, initial_capital: capital,
      })
      this.refreshList()
    } catch {}
  }

  private async refreshList(): Promise<void> {
    const listEl = this.el?.querySelector('.pt-list')
    if (!listEl) return
    try {
      const res = await fundQuantApi.paperTradeList()
      const sessions = res.data
      if (!sessions?.length) {
        listEl.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);font-size:11px;padding:12px;">暂无会话，启动一个新会话</div>'
        return
      }
      listEl.innerHTML = sessions.map(s => `
        <div class="pt-list-item ${this.activeId === s.paper_trade_id ? 'pt-item-active' : ''}"
             data-id="${s.paper_trade_id}"
             style="padding:6px 8px;cursor:pointer;border-bottom:1px solid var(--border-light);font-size:11px;${this.activeId === s.paper_trade_id ? 'background:var(--bg-tertiary);border-left:2px solid var(--danger-color);' : ''}">
          <div style="display:flex;justify-content:space-between;">
            <span style="font-weight:600;">${s.strategy_name}</span>
            <span style="color:${s.status === 'running' ? 'var(--success-color)' : 'var(--text-tertiary)'}">${s.status === 'running' ? '●' : '○'}</span>
          </div>
          <div style="color:var(--text-secondary);font-size:10px;">
            ${s.days_run}d | 收益 ${(s.total_return * 100).toFixed(1)}% | ¥${(s.current_value || 0).toLocaleString()}
          </div>
        </div>
      `).join('')

      listEl.querySelectorAll('.pt-list-item').forEach(item => {
        item.addEventListener('click', () => {
          this.activeId = (item as HTMLElement).dataset.id || null
          this.loadDetail(this.activeId!)
          this.refreshList() // re-render to update active
        })
      })
    } catch { /* ignore */ }
  }

  private async loadDetail(id: string): Promise<void> {
    const detailEl = this.el?.querySelector('.pt-detail')
    if (!detailEl) return
    try {
      const res = await fundQuantApi.paperTradeStatus(id)
      const s = res.data
      detailEl.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;margin-bottom:8px;">
          <div><span style="font-size:11px;color:var(--text-secondary);">现金</span><span style="font-size:14px;font-weight:600;margin-left:8px;">¥${(s.cash || 0).toLocaleString()}</span></div>
          <div><span style="font-size:11px;color:var(--text-secondary);">持仓</span><span style="font-size:14px;font-weight:600;margin-left:8px;">${Object.keys(s.positions || {}).length} 只</span></div>
          <div><span style="font-size:11px;color:var(--text-secondary);">待确认</span><span style="font-size:14px;font-weight:600;margin-left:8px;color:${(s.pending_orders?.length || 0) > 0 ? 'var(--warning-color)' : 'inherit'}">${s.pending_orders?.length || 0}</span></div>
          <div><span style="font-size:11px;color:var(--text-secondary);">最后运行</span><span style="font-size:12px;font-weight:600;margin-left:8px;">${s.last_run_date || '-'}</span></div>
        </div>
        <div style="font-size:11px;font-weight:600;color:var(--text-secondary);margin-bottom:4px;">持仓明细</div>
        <table style="width:100%;font-size:11px;margin-bottom:8px;border-collapse:collapse;">
          <thead><tr><th style="text-align:left;padding:2px 4px;color:var(--text-secondary);border-bottom:1px solid var(--border-light);">基金</th>
            <th style="text-align:right;padding:2px 4px;color:var(--text-secondary);border-bottom:1px solid var(--border-light);">份额</th></tr></thead>
          <tbody>${Object.entries(s.positions || {}).map(([code, shares]) => `
            <tr><td style="padding:2px 4px;border-bottom:1px solid var(--border-light);">${code}</td>
            <td style="text-align:right;padding:2px 4px;border-bottom:1px solid var(--border-light);">${(shares as number).toFixed(0)}</td></tr>
          `).join('')}</tbody>
        </table>
        <div style="display:flex;gap:4px;margin-bottom:6px;">
          <button class="btn btn-sm btn-primary pt-step-btn">▶ 单步运行</button>
          <button class="btn btn-sm btn-outline pt-stop-btn">⏹ 停止</button>
        </div>
        <div class="pt-mini-chart" style="height:60px;"></div>`

      detailEl.querySelector('.pt-step-btn')?.addEventListener('click', () => this.stepRun())
      detailEl.querySelector('.pt-stop-btn')?.addEventListener('click', () => this.stopSession())

      const miniEl = detailEl.querySelector<HTMLElement>('.pt-mini-chart')
      if (miniEl && s.equity_curve?.length >= 2) {
        this.renderMiniChart(miniEl, s.equity_curve)
      }
    } catch { /* ignore */ }
  }

  private async stepRun(): Promise<void> {
    if (!this.activeId) return
    try {
      await fundQuantApi.paperTradeRun(this.activeId)
      this.loadDetail(this.activeId)
      this.refreshList()
    } catch {}
  }

  private async stopSession(): Promise<void> {
    if (!this.activeId) return
    try {
      await fundQuantApi.paperTradeStop(this.activeId)
      this.loadDetail(this.activeId)
      this.refreshList()
    } catch {}
  }

  private renderMiniChart(chartEl: HTMLElement, equity: { date?: string; total_value?: number; cash?: number }[]): void {
    this.miniChart?.dispose()
    this.miniChart = echarts.init(chartEl)
    const values = equity.map(e => e.total_value || 0)
    const base = values[0] || 1
    const pct = values.map(v => ((v - base) / base * 100))

    this.miniChart.setOption({
      grid: { left: 2, right: 2, top: 2, bottom: 2 },
      xAxis: { show: false, type: 'category', data: equity.map(() => '') },
      yAxis: { show: false },
      series: [{
        type: 'line', data: pct, smooth: true,
        lineStyle: { color: '#3b82f6', width: 1.5 },
        areaStyle: { color: 'rgba(59,130,246,0.15)' },
        symbol: 'none',
      }],
    })
  }

  async refresh(): Promise<void> {
    this.refreshList()
    this.loadFundOptions()
  }

  destroy(): void {
    this.miniChart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.miniChart?.resize()
  }
}
