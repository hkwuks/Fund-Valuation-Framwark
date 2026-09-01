/**
 * FundQuant 主入口 — 面板式仪表盘
 *
 * 集成 LayoutManager 与各 Panel，提供生命周期管理。
 */

import { state } from './state'
import { LayoutManager } from './layout'
import { fundQuantApi } from './api'
import { KPIBar } from './panels/KPIBar'
import { NavChart } from './panels/NavChart'
import { SignalList } from './panels/SignalList'
import { FundRanking } from './panels/FundRanking'
import { MonthlyReturns } from './panels/MonthlyReturns'
import { Attribution } from './panels/Attribution'
import { DetailPanel } from './panels/DetailPanel'
import { PaperTradePanel } from './panels/PaperTradePanel'
import { StrategyBacktest } from './panels/StrategyBacktest'
import { ResearchPanel } from './research-panel'
import { fundManager } from '../fundManager'

export class FundQuantDashboard {
  private layout: LayoutManager | null = null
  private refreshTimer: ReturnType<typeof setInterval> | null = null
  private researchPanel: ResearchPanel | null = null

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
    this.layout.register(new FundRanking())
    this.layout.register(new MonthlyReturns())
    this.layout.register(new Attribution())
    this.layout.register(new DetailPanel())
    this.layout.register(new PaperTradePanel())
    this.layout.register(new StrategyBacktest())

    // L3 研究区容器（在 grid 下方）
    const researchContainer = document.createElement('div')
    researchContainer.className = 'fq-research-container'
    container.appendChild(researchContainer)
    this.researchPanel = new ResearchPanel()
    this.researchPanel.init(researchContainer)

    this.loadFundPool().then(() => {
      // 后台预加载全部基金的净值数据
      const codes = state.get('fundPool').map(f => f.fund_code)
      if (codes.length) {
        fundQuantApi.collectNavData(codes, 5).catch(() => {})
      }
      this.layout!.refreshAll()
      this.startRefreshTimer()
    })
  }

  /** 当 tab 切换到基金量化时调用 */
  onActivated(): void {
    this.startRefreshTimer()
    this.layout?.refreshAll()
    this.layout?.activateAll()
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
      // 等待基金数据加载完成（重试直到有数据或超时）
      let funds: any[] = []
      for (let retry = 0; retry < 10; retry++) {
        if (fundManager.getFunds().length > 0) break
        await new Promise(r => setTimeout(r, 200))
        if (retry === 0) await fundManager.loadFunds()
      }
      funds = fundManager.getFunds()
      state.set('fundPool', funds.map((f: any) => ({
        fund_code: f.fund_code,
        fund_name: f.fund_name || '',
        fund_type: f.fund_type || '',
        market_type: f.market_type || 'unknown',
        trade_mode: f.trade_mode,
        subscription_confirm_days: f.subscription_confirm_days,
        redemption_confirm_days: f.redemption_confirm_days,
        cash_arrival_days: f.cash_arrival_days,
        trading_profile_source: f.trading_profile_source,
        trading_profile_confidence: f.trading_profile_confidence,
        trading_profile_needs_confirmation: f.trading_profile_needs_confirmation,
      })))
      if (funds.length > 0 && !state.get('selectedFund')) {
        state.set('selectedFund', funds[0].fund_code)
      }
    } catch { /* fundManager not available */ }
  }

  destroy(): void {
    this.stopRefreshTimer()
    this.researchPanel?.destroy()
    this.layout?.destroy()
  }
}
