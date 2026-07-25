/**
 * FundQuant 主入口 — 面板式仪表盘
 *
 * 集成 LayoutManager 与各 Panel，提供生命周期管理。
 */

import { state } from './state'
import { LayoutManager } from './layout'
import { KPIBar } from './panels/KPIBar'
import { NavChart } from './panels/NavChart'
import { SignalList } from './panels/SignalList'
import { Allocation } from './panels/Allocation'
import { FundRanking } from './panels/FundRanking'
import { MonthlyReturns } from './panels/MonthlyReturns'

export class FundQuantDashboard {
  private layout: LayoutManager | null = null
  private refreshTimer: ReturnType<typeof setInterval> | null = null

  init(container: HTMLElement): void {
    const grid = document.createElement('div')
    grid.className = 'fq-dashboard-grid'
    container.innerHTML = ''
    container.appendChild(grid)
    this.layout = new LayoutManager(grid)

    // 注册面板
    this.layout.register(new KPIBar())
    this.layout.register(new NavChart())
    this.layout.register(new SignalList())
    this.layout.register(new Allocation())
    this.layout.register(new FundRanking())
    this.layout.register(new MonthlyReturns())

    this.loadFundPool()
    this.layout.refreshAll()
    this.startRefreshTimer()
  }

  /** 当 tab 切换到基金量化时调用 */
  onActivated(): void {
    this.startRefreshTimer()
    this.layout?.refreshAll()
  }

  /** 当 tab 切离基金量化时调用 */
  onDeactivated(): void {
    this.stopRefreshTimer()
  }

  private startRefreshTimer(): void {
    this.stopRefreshTimer()
    this.refreshTimer = setInterval(() => this.layout?.refreshAll(), 30000)
  }

  private stopRefreshTimer(): void {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer)
      this.refreshTimer = null
    }
  }

  private async loadFundPool(): Promise<void> {
    try {
      const { fundManager } = await import('../fundManager')
      const funds = fundManager.getFunds()
      state.set('fundPool', funds.map((f: any) => ({
        fund_code: f.fund_code,
        fund_name: f.fund_name || '',
        fund_type: f.fund_type || '',
      })))
      if (funds.length > 0 && !state.get('selectedFund')) {
        state.set('selectedFund', funds[0].fund_code)
      }
    } catch { /* fundManager not available */ }
  }

  destroy(): void {
    this.stopRefreshTimer()
    this.layout?.destroy()
  }
}
