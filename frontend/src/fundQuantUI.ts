/**
 * fundQuantUI.ts — 重构版
 *
 * 薄委托层，将控制权交给 FundQuantDashboard。
 */

import { FundQuantDashboard } from './fundQuant'

export class FundQuantUI {
  private dashboard: FundQuantDashboard | null = null

  init(container: HTMLDivElement) {
    this.dashboard = new FundQuantDashboard()
    this.dashboard.init(container)
  }

  /** 由 main.ts 在 tab 切换时调用 */
  onActivated(): void {
    this.dashboard?.onActivated()
  }

  /** 由 main.ts 在 tab 切离时调用 */
  onDeactivated(): void {
    this.dashboard?.onDeactivated()
  }

  destroy() {
    this.dashboard?.destroy()
    this.dashboard = null
  }
}
