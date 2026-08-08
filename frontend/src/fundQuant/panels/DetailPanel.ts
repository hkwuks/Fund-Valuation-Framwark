// frontend/src/fundQuant/panels/DetailPanel.ts
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'

export class DetailPanel extends PanelBase {
  private unsub: (() => void) | null = null

  constructor() {
    super({ id: 'detail', title: '基金详情', defaultGridPos: { x: 0, y: 4, w: 2, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-detail'
    el.style.display = 'none'
    el.innerHTML = `
      <div class="panel-header">
        <h3>基金详情</h3>
        <button class="detail-close" style="background:none;border:none;cursor:pointer;font-size:18px;color:var(--text-secondary);padding:0 4px;" title="关闭">&#x2715;</button>
      </div>
      <div class="detail-body" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;padding:12px 16px;">
        <div class="detail-section detail-section-signals">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">信号历史</h4>
          <div class="detail-signals-body" style="max-height:160px;overflow-y:auto;"></div>
        </div>
        <div class="detail-section detail-section-monthly">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">月度收益</h4>
          <div class="detail-monthly-body" style="max-height:160px;overflow-y:auto;"></div>
        </div>
        <div class="detail-section detail-section-info">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">基金信息</h4>
          <div class="detail-info-body"></div>
        </div>
        <div class="detail-section detail-section-accuracy" style="grid-column:1;">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">信号准确率</h4>
          <div class="detail-accuracy-body"></div>
        </div>
        <div class="detail-section detail-section-factors" style="grid-column:2/4;">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">因子暴露</h4>
          <div class="detail-factors-body" style="height:140px;"></div>
        </div>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.detail-close')?.addEventListener('click', () => {
      if (this.el) this.el.style.display = 'none'
    })

    this.unsub = state.on('selectedFund', () => {
      const code = state.get('selectedFund')
      if (code && this.el) {
        this.el.style.display = ''
        this.refresh()
      }
    })
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const code = state.get('selectedFund')
    if (!code) return
    const signalsP = this.loadSignals(code)
    const monthlyP = this.loadMonthlyReturns(code)
    const accuracyP = this.loadAccuracy(code)
    const factorsP = this.loadFactorExposure(code)
    this.loadFundInfo(code)
    await Promise.allSettled([signalsP, monthlyP, accuracyP, factorsP])
  }

  private async loadSignals(code: string): Promise<void> {
    try {
      const res = await fundQuantApi.getSignals(code, 10)
      const signals = res.data || []
      const container = this.el?.querySelector('.detail-signals-body')
      if (!container) return
      if (!signals.length) {
        container.innerHTML = '<span style="color:var(--text-tertiary);font-size:12px;">无信号数据</span>'
        return
      }
      const dirLabel: Record<string, string> = { buy: '↑买入', sell: '↓卖出', hold: '→持有', short: '🛑做空', close_short: '↩平空' }
      const dirColor: Record<string, string> = { buy: 'var(--danger-color)', sell: 'var(--success-color)', short: 'var(--warning-color)', hold: 'var(--text-secondary)' }
      container.innerHTML = signals.map(s => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-light);font-size:12px;">
          <span style="color:${dirColor[s.direction] || 'var(--text-secondary)'};font-weight:600;">${dirLabel[s.direction] || s.direction}</span>
          <span style="color:var(--text-secondary);">${s.strategy_name || '-'}</span>
          <span style="color:var(--text-tertiary);">${(s.created_at || '').slice(5, 16)}</span>
        </div>
      `).join('')
    } catch { /* leave failed section blank */ }
  }

  private async loadMonthlyReturns(code: string): Promise<void> {
    try {
      const res = await fundQuantApi.getMonthlyReturns(code)
      if (!res.success) return
      const matrix = res.data.matrix || []
      const container = this.el?.querySelector('.detail-monthly-body')
      if (!container) return
      if (!matrix.length) {
        container.innerHTML = '<span style="color:var(--text-tertiary);font-size:12px;">无月度数据</span>'
        return
      }
      const recent = matrix.slice(-6).reverse()
      container.innerHTML = recent.map(m => `
        <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border-light);font-size:12px;">
          <span style="color:var(--text-primary);">${m.year}-${String(m.month).padStart(2, '0')}</span>
          <span style="font-weight:600;color:${m.return >= 0 ? 'var(--danger-color)' : 'var(--success-color)'};">${m.return >= 0 ? '+' : ''}${m.return.toFixed(1)}%</span>
        </div>
      `).join('')
    } catch { /* leave failed section blank */ }
  }

  private async loadAccuracy(code: string): Promise<void> {
    const container = this.el?.querySelector('.detail-accuracy-body')
    if (!container) return
    try {
      // 获取所有信号计算准确率
      const res = await fundQuantApi.getSignals(code, 100)
      const signals = res.data || []
      if (!signals.length) {
        container.innerHTML = '<span style="color:var(--text-tertiary);font-size:12px;">无足够信号数据</span>'
        return
      }
      const total = signals.length
      const buySignals = signals.filter(s => s.direction === 'buy')
      const sellSignals = signals.filter(s => s.direction === 'sell')
      const holdSignals = signals.filter(s => s.direction === 'hold')

      // 简化版：假设最近一次的方向与未来净值变化匹配作为正确信号
      // 实际上需要后端返回准确率统计
      container.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;">
          <div style="padding:4px 0;color:var(--text-tertiary);">总信号</div>
          <div style="font-weight:600;text-align:right;">${total}</div>
          <div style="padding:4px 0;color:var(--text-tertiary);">买入信号</div>
          <div style="color:var(--danger-color);font-weight:600;text-align:right;">${buySignals.length}</div>
          <div style="padding:4px 0;color:var(--text-tertiary);">卖出信号</div>
          <div style="color:var(--success-color);font-weight:600;text-align:right;">${sellSignals.length}</div>
          <div style="padding:4px 0;color:var(--text-tertiary);">持有信号</div>
          <div style="color:var(--text-secondary);font-weight:600;text-align:right;">${holdSignals.length}</div>
        </div>`
    } catch {
      container.innerHTML = '<span style="color:var(--text-tertiary);font-size:12px;">准确率计算暂不可用</span>'
    }
  }

  private async loadFactorExposure(_code: string): Promise<void> {
    const container = this.el?.querySelector('.detail-factors-body')
    if (!container) return
    container.innerHTML = `
      <div style="padding:12px;text-align:center;">
        <button class="btn btn-sm btn-outline open-exposure-btn" style="font-size:12px;">
          🔬 打开因子暴露面板
        </button>
        <div style="margin-top:8px;font-size:11px;color:var(--text-tertiary);">
          查看当前基金的因子贡献雷达图 + 同类对比
        </div>
      </div>`
    container.querySelector('.open-exposure-btn')?.addEventListener('click', () => {
      const code = state.get('selectedFund')
      if (code) {
        state.set('researchPanel', {
          visible: true, activeTab: 'exposure', fundCode: code, signal: null,
        })
      }
    })
  }

  private loadFundInfo(code: string): void {
    const container = this.el?.querySelector('.detail-info-body')
    if (!container) return
    const fund = state.get('fundPool').find(f => f.fund_code === code)
    if (!fund) {
      container.innerHTML = '<span style="color:var(--text-tertiary);font-size:12px;">无基金信息</span>'
      return
    }
    container.innerHTML = `
      <div style="padding:4px 0;font-size:12px;">
        <div style="display:flex;justify-content:space-between;padding:4px 0;"><span style="color:var(--text-secondary);">代码</span><span style="color:var(--text-primary);font-weight:600;">${fund.fund_code}</span></div>
        <div style="display:flex;justify-content:space-between;padding:4px 0;"><span style="color:var(--text-secondary);">名称</span><span style="color:var(--text-primary);font-weight:600;">${fund.fund_name || '-'}</span></div>
        <div style="display:flex;justify-content:space-between;padding:4px 0;"><span style="color:var(--text-secondary);">类型</span><span style="color:var(--text-primary);font-weight:600;">${fund.fund_type || '-'}</span></div>
      </div>`
  }

  destroy(): void {
    this.unsub?.()
    super.destroy()
  }
}
