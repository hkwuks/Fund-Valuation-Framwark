/**
 * L3 研究区容器 — 在 Dashboard 网格下方展开，不占用 grid 位置
 *
 * 包含 Tab 切换：择时研究 | 因子暴露
 * 从信号列表/净值图点击信号时展开。
 */

import { state, type SignalSummary } from './state'
import { TimingResearch } from './panels/TimingResearch'
import { FactorExposure } from './panels/FactorExposure'

export class ResearchPanel {
  private container: HTMLElement | null = null
  private timingResearch: TimingResearch | null = null
  private factorExposure: FactorExposure | null = null
  private unsub: (() => void) | null = null

  init(container: HTMLElement): void {
    this.container = container
    this.render()
    this.bindEvents()
    // 监听研究区状态变化
    this.unsub = state.on('researchPanel', () => {
      const rp = state.get('researchPanel')
      if (rp.visible && rp.fundCode) {
        this.show(rp.fundCode, rp.signal)
      } else {
        this.hide()
      }
    })
  }

  private render(): void {
    if (!this.container) return
    this.container.innerHTML = `
      <div class="research-panel" style="display:none;">
        <div class="research-header">
          <div class="research-tabs">
            <button class="research-tab active" data-tab="timing">择时研究</button>
            <button class="research-tab" data-tab="exposure">因子暴露</button>
          </div>
          <div class="research-info">
            <span class="research-fund-name"></span>
            <span class="research-signal-info"></span>
          </div>
          <button class="btn btn-sm btn-ghost research-close" title="关闭">✕</button>
        </div>
        <div class="research-body">
          <div class="research-tab-content active" data-tab="timing"></div>
          <div class="research-tab-content" data-tab="exposure"></div>
        </div>
      </div>`
  }

  private bindEvents(): void {
    if (!this.container) return
    // Tab 切换
    this.container.querySelector('.research-tabs')?.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('.research-tab') as HTMLElement
      if (!btn) return
      const tab = btn.dataset.tab
      if (!tab) return
      this.container?.querySelectorAll('.research-tab').forEach(b => b.classList.remove('active'))
      btn.classList.add('active')
      this.container?.querySelectorAll('.research-tab-content').forEach(c => c.classList.remove('active'))
      this.container?.querySelector(`.research-tab-content[data-tab="${tab}"]`)?.classList.add('active')
      if (tab === 'timing') this.timingResearch?.onActivated()
      else if (tab === 'exposure') this.factorExposure?.onActivated()
    })

    // 关闭
    this.container?.querySelector('.research-close')?.addEventListener('click', () => {
      state.set('researchPanel', { ...state.get('researchPanel'), visible: false })
    })
  }

  show(fundCode: string, signal?: SignalSummary | null): void {
    const panel = this.container?.querySelector('.research-panel') as HTMLElement
    if (!panel) return
    panel.style.display = ''
    panel.style.boxShadow = '0 0 0 3px #3b82f6, 0 0 20px rgba(59,130,246,0.3)'
    setTimeout(() => { panel.style.boxShadow = '' }, 1500)

    // 滚动到研究区
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

    // 标记选中 Tab
    const rp = state.get('researchPanel')
    this.container?.querySelectorAll('.research-tab').forEach(b => {
      (b as HTMLElement).classList.toggle('active', (b as HTMLElement).dataset.tab === rp.activeTab)
    })
    this.container?.querySelectorAll('.research-tab-content').forEach(c => {
      (c as HTMLElement).classList.toggle('active', (c as HTMLElement).dataset.tab === rp.activeTab)
    })

    // 更新标题
    const fundName = state.get('fundPool').find(f => f.fund_code === fundCode)?.fund_name || fundCode
    const nameEl = this.container!.querySelector('.research-fund-name') as HTMLElement; if (nameEl) nameEl.textContent = fundName
    const sigInfo = this.container!.querySelector('.research-signal-info')
    if (signal) {
      const dirLabel: Record<string, string> = { buy: '↑买入', sell: '↓卖出', hold: '→持有' }
      sigInfo!.textContent = `${dirLabel[signal.direction] || signal.direction}  ${(signal.confidence * 100).toFixed(0)}%  ${signal.strategy_name || ''}`
      sigInfo!.setAttribute('style', '')
    } else {
      sigInfo!.textContent = '查看详情'
      sigInfo!.setAttribute('style', 'color:var(--text-tertiary)')
    }

    // 初始化择时研究面板
    if (!this.timingResearch) {
      const timingEl = this.container!.querySelector('.research-tab-content[data-tab="timing"]') as HTMLElement
      if (timingEl) {
        this.timingResearch = new TimingResearch()
        this.timingResearch.init(timingEl)
      }
    }
    this.timingResearch?.show(fundCode, signal)

    // 初始化因子暴露面板
    if (!this.factorExposure) {
      const exposureEl = this.container!.querySelector('.research-tab-content[data-tab="exposure"]') as HTMLElement
      if (exposureEl) {
        this.factorExposure = new FactorExposure()
        this.factorExposure.init(exposureEl)
      }
    }
    this.factorExposure?.show(fundCode)
  }

  hide(): void {
    const panel = this.container?.querySelector('.research-panel') as HTMLElement
    if (panel) panel.style.display = 'none'
  }

  destroy(): void {
    this.timingResearch?.destroy()
    this.factorExposure?.destroy()
    this.unsub?.()
  }
}
