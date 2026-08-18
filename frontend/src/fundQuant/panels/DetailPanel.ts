// frontend/src/fundQuant/panels/DetailPanel.ts
import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'

export class DetailPanel extends PanelBase {
  private unsub: (() => void) | null = null

  constructor() {
    super({ id: 'detail', title: '策略详情', defaultGridPos: { x: 0, y: 4, w: 2, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-detail'
    el.style.display = 'none'
    el.innerHTML = `
      <div class="panel-header">
        <h3>策略详情</h3>
        <button class="detail-close" style="background:none;border:none;cursor:pointer;font-size:18px;color:var(--text-secondary);padding:0 4px;" title="关闭">&#x2715;</button>
      </div>
      <div class="detail-body" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px 16px;">
        <div class="detail-section detail-section-monthly">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">月度收益</h4>
          <div class="detail-monthly-body" style="max-height:160px;overflow-y:auto;"></div>
        </div>
        <div class="detail-section detail-section-info">
          <h4 style="font-size:13px;font-weight:600;color:var(--text-primary);margin:0 0 8px;">基金信息</h4>
          <div class="detail-info-body"></div>
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
    this.loadMonthlyReturns(code)
    this.loadFundInfo(code)
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
