import { PanelBase } from '../layout'
import { fundQuantApi, type StrategyAllocationSignal } from '../api'
import { state, persistConfigCapital } from '../state'

const NAME_MAP: Record<string, string> = {
  etf_rotation_aurora: 'ETF动量轮动',
  all_weather_aurora: '桥水全天候',
  bl_quadrant_aurora: 'BL四象限观点',
  black_litterman_aurora: 'Black-Litterman',
  risk_parity_aurora: '风险平价',
  dynamic_risk_parity_aurora: '动态风险平价',
  vol_targeting_aurora: '波动率目标',
  trend_following_aurora: '趋势跟踪',
  gmv_aurora: '最小方差(GMV)',
  hrp_aurora: '层次风险平价(HRP)',
  max_diversification_aurora: '最大多元化(MDP)',
}

/**
 * 策略选择列表 — 左侧面板，用户点选策略后右侧展示详情
 */
export class SignalList extends PanelBase {
  private strategies: StrategyAllocationSignal[] = []

  constructor() {
    super({ id: 'signal_list', title: '策略配置', defaultGridPos: { x: 0, y: 2, w: 1, h: 2 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-signallist'
    el.style.cssText = 'display:flex;flex-direction:column;overflow:hidden;min-height:0;'
    el.innerHTML = `
      <div class="panel-header">
        <h3>📊 策略列表</h3>
        <button class="btn btn-sm btn-primary btn-refresh-alloc" title="刷新">🔄</button>
      </div>
      <div style="padding:6px 8px 0;display:flex;align-items:center;gap:6px;">
        <label for="alloc-capital" style="font-size:11px;color:var(--text-secondary);white-space:nowrap;">配置基准</label>
        <input id="alloc-capital" type="number" min="0" step="10000" style="width:100%;min-width:0;font-size:12px;padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-primary);color:var(--text-primary);" title="用于计算各策略买入金额的基准资金，不会影响你的真实持仓">
      </div>
      <div class="alloc-list" style="flex:1;min-height:0;overflow-y:auto;padding:8px;"></div>
      <div class="alloc-msg" style="font-size:12px;color:var(--text-tertiary);padding:12px;text-align:center;">加载中…</div>`
    return el
  }

  private blUnsub: (() => void) | null = null
  private capUnsub: (() => void) | null = null

  protected afterMount(): void {
    this.el?.querySelector('.btn-refresh-alloc')?.addEventListener('click', () => this.refresh())
    const capInput = this.el?.querySelector('#alloc-capital') as HTMLInputElement | null
    if (capInput) {
      capInput.value = String(state.get('configCapital'))
      let timer: ReturnType<typeof setTimeout> | null = null
      capInput.addEventListener('input', () => {
        if (timer) clearTimeout(timer)
        timer = setTimeout(() => {
          const v = parseFloat(capInput.value)
          if (!Number.isFinite(v) || v <= 0) return
          persistConfigCapital(v)   // 持久化并广播，右侧详情面板随金额刷新
          this.refresh()            // 左侧金额列随新基准重算
        }, 400)
      })
    }
    this.blUnsub = state.on('blViews', () => this.refresh())
    this.capUnsub = state.on('configCapital', () => {
      if (capInput) capInput.value = String(state.get('configCapital'))
    })
    this.refresh()
  }

  private allocParams(): Record<string, any> {
    const views = state.get('blViews') || []
    return views.length ? { views } : {}
  }

  async refresh(): Promise<void> {
    const container = this.el?.querySelector('.alloc-list') as HTMLElement
    const msg = this.el?.querySelector('.alloc-msg') as HTMLElement
    if (!container) return

    const codes = state.get('fundPool').map(f => f.fund_code)
    if (!codes.length) {
      msg!.textContent = '基金池为空，请先加载基金数据'
      return
    }

    try {
      const params = this.allocParams()
      const capital = state.get('configCapital')
      const res = await fundQuantApi.getStrategyAllocation(codes, capital, params)
      if (!res.success) throw new Error('请求失败')
      this.strategies = res.data.strategies
      if (!this.strategies.length) {
        msg!.textContent = '暂无可用策略'
        return
      }
      msg!.textContent = ''
      this.renderList(container)
    } catch (e: any) {
      msg!.textContent = `❌ ${e?.message || '获取策略失败'}`
    }
  }

  private renderList(container: HTMLElement): void {
    const selected = state.get('selectedStrategy')
    container.innerHTML = ''

    for (const s of this.strategies) {
      const name = NAME_MAP[s.strategy] || s.strategy
      const isSelected = s.strategy === selected
      const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
      const topHoldings = entries.slice(0, 3).map(([, w]) => `${(w * 100).toFixed(0)}%`).join(' / ')

      const card = document.createElement('div')
      card.style.cssText = `padding:10px 12px;margin-bottom:6px;border-radius:8px;cursor:pointer;transition:all .15s;
        border:1.5px solid ${isSelected ? 'var(--primary-color)' : 'var(--border-light)'};
        background:${isSelected ? 'var(--primary-color)10' : 'var(--bg-secondary)'};`
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <strong style="font-size:13px;color:${isSelected ? 'var(--primary-color)' : 'var(--text-primary)'};">${name}</strong>
          <span style="font-size:11px;color:var(--text-tertiary);">信心 ${(s.confidence * 100).toFixed(0)}%</span>
        </div>
        <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;">
          ${entries.length}只基金 · ${topHoldings || '—'}
        </div>`
      card.addEventListener('click', () => {
        state.set('selectedStrategy', s.strategy)
        this.renderList(container)
      })
      container.appendChild(card)
    }
  }

  destroy(): void {
    this.blUnsub?.()
    this.capUnsub?.()
    super.destroy()
  }
}
