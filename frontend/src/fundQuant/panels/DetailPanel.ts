// frontend/src/fundQuant/panels/DetailPanel.ts
import { PanelBase } from '../layout'
import { fundQuantApi, type StrategyAllocationSignal } from '../api'
import { state } from '../state'

const NAME_MAP: Record<string, string> = {
  etf_rotation_aurora: 'ETF动量轮动',
  all_weather_aurora: '桥水全天候',
  bl_quadrant_aurora: 'BL四象限观点',
  black_litterman_aurora: 'Black-Litterman',
  risk_parity_aurora: '风险平价',
  hrp_aurora: '层次风险平价(HRP)',
  max_diversification_aurora: '最大多元化(MDP)',
}

/**
 * 策略详情面板 — 展示选中策略的权重、与当前持仓的差异、操作建议
 */
export class DetailPanel extends PanelBase {
  private unsub: (() => void) | null = null
  private strategies: StrategyAllocationSignal[] = []

  constructor() {
    super({ id: 'detail', title: '策略详情', defaultGridPos: { x: 1, y: 2, w: 2, h: 2 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-detail'
    el.style.display = 'none'
    el.innerHTML = `
      <div class="panel-header">
        <h3 id="detail-title">策略详情</h3>
        <button class="detail-close" style="background:none;border:none;cursor:pointer;font-size:18px;color:var(--text-secondary);padding:0 4px;" title="关闭">&#x2715;</button>
      </div>
      <div class="detail-body" style="padding:12px 16px;max-height:400px;overflow-y:auto;"></div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.detail-close')?.addEventListener('click', () => {
      if (this.el) this.el.style.display = 'none'
    })

    this.unsub = state.on('selectedStrategy', async () => {
      const strategy = state.get('selectedStrategy')
      if (!strategy || !this.el) return
      this.el.style.display = ''
      await this.loadStrategyDetail(strategy)
    })
  }

  async refresh(): Promise<void> {
    const strategy = state.get('selectedStrategy')
    if (strategy) await this.loadStrategyDetail(strategy)
  }

  /** 预加载策略列表（从 SignalList 获取） */
  setStrategies(strategies: StrategyAllocationSignal[]): void {
    this.strategies = strategies
  }

  private async loadStrategyDetail(strategyName: string): Promise<void> {
    if (!this.el) return
    const body = this.el.querySelector('.detail-body') as HTMLElement
    const title = this.el.querySelector('#detail-title') as HTMLElement
    if (!body) return

    title.textContent = NAME_MAP[strategyName] || strategyName

    // 拿当前策略数据
    if (!this.strategies.length) {
      const codes = state.get('fundPool').map(f => f.fund_code)
      if (!codes.length) { body.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;">无基金数据</div>'; return }
      try {
        const res = await fundQuantApi.getStrategyAllocation(codes, 100000)
        if (res.success) this.strategies = res.data.strategies
      } catch { /* ignore */ }
    }

    const s = this.strategies.find(x => x.strategy === strategyName)
    if (!s) {
      body.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;">策略数据加载中…</div>'
      return
    }

    // 获取当前持仓
    let currentPositions: Record<string, number> = {}
    try {
      const pRes = await fundQuantApi.getPortfolioKPI()
      if (pRes.success && pRes.data.positions) {
        const totalVal = pRes.data.total_value || 1
        for (const [code, pos] of Object.entries(pRes.data.positions)) {
          currentPositions[code] = pos.value / totalVal
        }
      }
    } catch { /* ignore */ }

    body.innerHTML = this.buildDetailHtml(s, currentPositions)
    this.bindConfirmButtons(s)
  }

  private buildDetailHtml(s: StrategyAllocationSignal, currentPos: Record<string, number>): string {
    const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const money = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
    const capital = s.capital || 100000

    // 1. 策略权重表格
    const weightRows = entries.map(([code, w]) => {
      const cur = currentPos[code] || 0
      const diff = w - cur
      const diffColor = Math.abs(diff) < 0.005 ? 'var(--text-tertiary)' : diff > 0 ? 'var(--primary-color)' : '#ef4444'
      const diffLabel = Math.abs(diff) < 0.005 ? '—' : `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}%`
      const amount = s.buy_amounts?.[code] || 0
      return `<tr style="border-bottom:1px solid var(--border-light);">
        <td style="padding:6px 4px;font-size:12px;">${fundName(code)}<span style="color:var(--text-tertiary);font-size:10px;margin-left:4px;">${code}</span></td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${(w * 100).toFixed(1)}%</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${cur > 0 ? (cur * 100).toFixed(1) + '%' : '—'}</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;color:${diffColor};font-weight:600;">${diffLabel}</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${money(amount)}</td>
      </tr>`
    }).join('')

    // 2. 操作建议
    const suggestions = this.buildSuggestions(s, entries, currentPos)
    const totalWeight = entries.reduce((a, [, w]) => a + w, 0)
    const holdingCount = Object.keys(currentPos).length

    return `
      <div style="margin-bottom:12px;">
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">信心度 <strong>${(s.confidence * 100).toFixed(0)}%</strong> · 总资产 ${money(capital)}</div>
        <div style="font-size:11px;color:var(--text-tertiary);">${s.reason || ''}</div>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="border-bottom:2px solid var(--border-light);">
          <th style="padding:6px 4px;text-align:left;color:var(--text-secondary);font-weight:600;">基金</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">目标</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">当前</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">差异</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">金额</th>
        </tr></thead>
        <tbody>${weightRows}</tbody>
      </table>
      <div style="margin-top:8px;font-size:11px;color:var(--text-tertiary);">目标权重合计 ${(totalWeight * 100).toFixed(1)}% · 当前持仓 ${holdingCount}只</div>
      <div style="margin-top:12px;padding:10px;background:var(--bg-tertiary);border-radius:6px;border-left:3px solid var(--primary-color);">
        <div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">💡 操作建议</div>
        <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;">${suggestions}</div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;">
        <button class="btn-apply-strategy btn btn-sm btn-primary" style="flex:1;font-weight:600;">✅ 采纳策略</button>
        <button class="btn-dismiss-strategy btn btn-sm" style="flex:1;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border-light);">忽略</button>
      </div>`
  }

  private buildSuggestions(_s: StrategyAllocationSignal, entries: [string, number][], currentPos: Record<string, number>): string {
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const lines: string[] = []

    // 新增持仓
    const toBuy = entries.filter(([code]) => !currentPos[code] || currentPos[code] < 0.005)
    if (toBuy.length) {
      lines.push(`<div>📗 <strong>新增配置 ${toBuy.length}只：</strong>${toBuy.map(([c]) => fundName(c)).join('、')}</div>`)
    }

    // 需要增仓
    const toIncrease = entries.filter(([code, w]) => currentPos[code] && w - currentPos[code] > 0.01)
    if (toIncrease.length) {
      lines.push(`<div>📈 <strong>建议增仓 ${toIncrease.length}只：</strong>${toIncrease.map(([c, w]) => `${fundName(c)} → ${(w * 100).toFixed(0)}%`).join('、')}</div>`)
    }

    // 需要减仓
    const toDecrease = entries.filter(([code, w]) => currentPos[code] && currentPos[code] - w > 0.01)
    if (toDecrease.length) {
      lines.push(`<div>📉 <strong>建议减仓 ${toDecrease.length}只：</strong>${toDecrease.map(([c, w]) => `${fundName(c)} → ${(w * 100).toFixed(0)}%`).join('、')}</div>`)
    }

    // 清仓
    const toSell = Object.keys(currentPos).filter(code => !entries.find(([c]) => c === code) && currentPos[code] > 0.005)
    if (toSell.length) {
      lines.push(`<div>🔴 <strong>建议清仓 ${toSell.length}只：</strong>${toSell.map(c => fundName(c)).join('、')}</div>`)
    }

    if (!lines.length) {
      lines.push('<div>✅ 当前持仓与策略建议一致，无需调整</div>')
    }

    return lines.join('')
  }

  private bindConfirmButtons(s: StrategyAllocationSignal): void {
    this.el?.querySelector('.btn-apply-strategy')?.addEventListener('click', () => {
      this.showConfirmDialog(s)
    })
    this.el?.querySelector('.btn-dismiss-strategy')?.addEventListener('click', () => {
      state.set('selectedStrategy', null)
      if (this.el) this.el.style.display = 'none'
    })
  }

  private showConfirmDialog(s: StrategyAllocationSignal): void {
    const name = NAME_MAP[s.strategy] || s.strategy
    const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const money = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
    const capital = s.capital || 100000

    const overlay = document.createElement('div')
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;'

    const dialog = document.createElement('div')
    dialog.style.cssText = 'background:var(--bg-primary);border-radius:12px;padding:24px;max-width:480px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.3);'

    const items = entries.map(([code, w]) => {
      const amount = s.buy_amounts?.[code] || capital * w
      return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-light);font-size:13px;">
        <span>${fundName(code)} <span style="color:var(--text-tertiary);font-size:11px;">${code}</span></span>
        <span><strong>${(w * 100).toFixed(1)}%</strong> <span style="color:var(--text-tertiary);">${money(amount)}</span></span>
      </div>`
    }).join('')

    dialog.innerHTML = `
      <h3 style="font-size:16px;font-weight:700;margin:0 0 12px;">确认采纳「${name}」策略</h3>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">将按以下比例配置总资产 ${money(capital)}：</div>
      <div style="margin-bottom:16px;">${items}</div>
      <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:16px;">⚠️ 采纳后将模拟执行调仓操作，建议在模拟交易中验证</div>
      <div style="display:flex;gap:8px;">
        <button class="btn-confirm-yes" style="flex:1;padding:8px;background:var(--primary-color);color:white;border:none;border-radius:6px;font-weight:600;cursor:pointer;">确认采纳</button>
        <button class="btn-confirm-no" style="flex:1;padding:8px;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border-light);border-radius:6px;cursor:pointer;">取消</button>
      </div>`

    overlay.appendChild(dialog)
    document.body.appendChild(overlay)

    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove() })
    dialog.querySelector('.btn-confirm-no')?.addEventListener('click', () => overlay.remove())
    dialog.querySelector('.btn-confirm-yes')?.addEventListener('click', async () => {
      overlay.remove()
      // TODO: 提交到 paper-trade
      console.log('采纳策略:', s.strategy, s.weights)
    })
  }

  destroy(): void {
    this.unsub?.()
    super.destroy()
  }
}
