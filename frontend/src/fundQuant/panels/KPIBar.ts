import { PanelBase } from '../layout'
import { fundQuantApi, type PortfolioStatus } from '../api'
import { state } from '../state'

export class KPIBar extends PanelBase {
  constructor() {
    super({ id: 'kpi', title: '组合概览', defaultGridPos: { x: 0, y: 0, w: 3, h: 0 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'kpi-bar'
    el.innerHTML = `
      <div class="kpi-card" data-key="total_return">
        <span class="kpi-label">总收益</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>
      <div class="kpi-card" data-key="annual_return">
        <span class="kpi-label">年化收益</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>
      <div class="kpi-card" data-key="max_drawdown">
        <span class="kpi-label">最大回撤</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>
      <div class="kpi-card" data-key="sharpe_ratio">
        <span class="kpi-label">夏普比率</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>
      <div class="kpi-card" data-key="volatility">
        <span class="kpi-label">年化波动</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>
      <div class="kpi-card kpi-signal" data-key="signals">
        <span class="kpi-label">信号汇总</span>
        <span class="kpi-value">--</span>
        <span class="kpi-sub"></span>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.kpi-signal')?.addEventListener('click', () => {
      state.set('showSignalPopup', true)
    })
  }

  async refresh(): Promise<void> {
    try {
      const res = await fundQuantApi.getPortfolioKPI()
      if (!res.success) return
      const d = res.data
      this.updateCards(d)
      this.el?.classList.remove('skeleton')
      state.set('portfolio', {
        total_return: d.return_pct,
        annual_return: d.annual_return ?? d.return_pct,
        max_drawdown: d.max_drawdown ?? 0,
        sharpe_ratio: d.sharpe_ratio ?? 0,
        volatility: d.volatility ?? 0,
        benchmark_return: d.benchmark_return ?? 0,
        signal_count: d.signal_count ?? { buy: 0, sell: 0, hold: 0 },
      })
    } catch {
      // keep last successful data; show '--' on initial load
    }
  }

  private hasRealData(d: PortfolioStatus): boolean {
    // 判断是否有真实数据（非初始空状态）
    return d.total_value > 100000 || d.return_pct !== 0 || d.position_count > 0
  }

  private updateCards(d: PortfolioStatus): void {
    if (!this.el) return
    const cards = this.el.querySelectorAll<HTMLElement>('.kpi-card[data-key]')
    const hasData = this.hasRealData(d)
    const fmtPct = (v: number) => `${v >= 0 ? '+' : ''}${(v).toFixed(2)}%`
    const cls = (v: number) => v >= 0 ? 'kpi-up' : 'kpi-down'

    cards.forEach(card => {
      const key = card.dataset.key
      if (!key) return
      const valEl = card.querySelector('.kpi-value')
      const subEl = card.querySelector('.kpi-sub')
      if (!valEl) return

      if (!hasData) {
        valEl.textContent = '--'
        valEl.className = 'kpi-value'
        if (subEl) subEl.textContent = ''
        return
      }

      switch (key) {
        case 'total_return':
          valEl.textContent = fmtPct(d.return_pct)
          valEl.className = `kpi-value ${cls(d.return_pct)}`
          break
        case 'annual_return':
          valEl.textContent = d.annual_return != null ? fmtPct(d.annual_return) : '--'
          valEl.className = `kpi-value ${cls(d.annual_return ?? 0)}`
          if (subEl && d.benchmark_return != null) subEl.textContent = `基准: ${fmtPct(d.benchmark_return)}`
          break
        case 'max_drawdown':
          valEl.textContent = d.max_drawdown != null ? fmtPct(d.max_drawdown) : '--'
          valEl.className = 'kpi-value kpi-down'
          break
        case 'sharpe_ratio':
          valEl.textContent = d.sharpe_ratio != null ? d.sharpe_ratio.toFixed(2) : '--'
          break
        case 'volatility':
          valEl.textContent = d.volatility != null ? fmtPct(d.volatility) : '--'
          break
        case 'signals': {
          const sc = d.signal_count
          if (sc) {
            valEl.textContent = `↑${sc.buy} ↓${sc.sell}`
            if (subEl) subEl.textContent = `持有 ${sc.hold}`
          }
          break
        }
      }
    })
  }
}
