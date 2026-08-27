/**
 * L3 研究区容器 — 在 Dashboard 网格下方展开，不占用 grid 位置
 *
 * 包含 Tab 切换：策略暴露、因子评价
 * 从信号列表/净值图点击信号时展开。
 */

import { state, type SignalSummary } from './state'
import { fundQuantApi, type FactorEvaluationReport, type FactorMeta } from './api'
import { FactorExposure } from './panels/FactorExposure'

export class ResearchPanel {
  private container: HTMLElement | null = null
  private factorExposure: FactorExposure | null = null
  private factorMeta: FactorMeta[] = []
  private factorListLoaded = false
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
            <button class="research-tab active" data-tab="exposure">策略暴露</button>
            <button class="research-tab" data-tab="evaluation">因子评价</button>
          </div>
          <div class="research-info">
            <span class="research-fund-name"></span>
            <span class="research-signal-info"></span>
          </div>
          <button class="btn btn-sm btn-ghost research-close" title="关闭">✕</button>
        </div>
        <div class="research-body">
          <div class="research-tab-content active" data-tab="exposure"></div>
          <div class="research-tab-content" data-tab="evaluation">
            <div class="factor-evaluation-controls">
              <label>因子 <select class="factor-select"><option value="">加载中…</option></select></label>
              <label>开始日期 <input class="factor-start-date" type="date"></label>
              <label>结束日期 <input class="factor-end-date" type="date"></label>
              <button class="btn btn-sm factor-evaluate-btn">开始评价</button>
            </div>
            <div class="factor-evaluation-status" style="color:var(--text-tertiary)">打开页签后加载因子列表</div>
            <div class="factor-evaluation-report" hidden></div>
          </div>
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
      state.set('researchPanel', { ...state.get('researchPanel'), activeTab: tab as 'exposure' | 'evaluation' })
      if (tab === 'exposure') this.factorExposure?.onActivated()
      if (tab === 'evaluation') void this.loadFactorList()
    })

    this.container?.querySelector('.factor-evaluate-btn')?.addEventListener('click', () => {
      void this.evaluateFactor()
    })

    // 关闭
    this.container?.querySelector('.research-close')?.addEventListener('click', () => {
      state.set('researchPanel', { ...state.get('researchPanel'), visible: false })
    })
  }

  private async loadFactorList(): Promise<void> {
    if (this.factorListLoaded) return
    const status = this.container?.querySelector('.factor-evaluation-status') as HTMLElement | null
    try {
      const response = await fundQuantApi.getFactorList()
      this.factorMeta = response.data || []
      const select = this.container?.querySelector('.factor-select') as HTMLSelectElement | null
      if (!select) return
      select.innerHTML = ''
      for (const factor of this.factorMeta) {
        const option = document.createElement('option')
        option.value = factor.name
        option.textContent = `${factor.display_name}（${factor.name}）`
        select.appendChild(option)
      }
      this.factorListLoaded = true
      if (this.factorMeta.length) {
        this.setEvaluationDates()
        if (status) status.textContent = '请选择因子并开始评价'
      } else if (status) {
        status.textContent = '暂无可评价的基金因子'
      }
    } catch (error) {
      if (status) status.textContent = `因子列表加载失败：${error instanceof Error ? error.message : '未知错误'}`
    }
  }

  private setEvaluationDates(): void {
    const end = new Date()
    const start = new Date(end)
    start.setFullYear(start.getFullYear() - 3)
    const format = (date: Date) => date.toISOString().slice(0, 10)
    const startInput = this.container?.querySelector('.factor-start-date') as HTMLInputElement | null
    const endInput = this.container?.querySelector('.factor-end-date') as HTMLInputElement | null
    if (startInput && !startInput.value) startInput.value = format(start)
    if (endInput && !endInput.value) endInput.value = format(end)
  }

  private async evaluateFactor(): Promise<void> {
    const status = this.container?.querySelector('.factor-evaluation-status') as HTMLElement | null
    const reportEl = this.container?.querySelector('.factor-evaluation-report') as HTMLElement | null
    const select = this.container?.querySelector('.factor-select') as HTMLSelectElement | null
    const start = (this.container?.querySelector('.factor-start-date') as HTMLInputElement | null)?.value
    const end = (this.container?.querySelector('.factor-end-date') as HTMLInputElement | null)?.value
    const fundCodes = state.get('fundPool').map(f => f.fund_code)
    if (!select?.value || !start || !end) {
      if (status) status.textContent = '请先选择因子和评价区间'
      return
    }
    if (!fundCodes.length) {
      if (status) status.textContent = '基金池为空，无法评价因子'
      return
    }
    if (start > end) {
      if (status) status.textContent = '开始日期不能晚于结束日期'
      return
    }
    if (status) status.textContent = '评价计算中…'
    if (reportEl) reportEl.hidden = true
    try {
      const response = await fundQuantApi.evaluateFactor({
        factor_name: select.value,
        fund_codes: fundCodes,
        start_date: start,
        end_date: end,
      })
      this.renderFactorReport(response.data)
      if (status) status.textContent = response.data.n_periods ? '评价完成' : '评价完成，但有效样本期不足'
    } catch (error) {
      if (status) status.textContent = `因子评价失败：${error instanceof Error ? error.message : '未知错误'}`
    }
  }

  private renderFactorReport(report: FactorEvaluationReport): void {
    const el = this.container?.querySelector('.factor-evaluation-report') as HTMLElement | null
    if (!el) return
    const pct = (value: number) => `${(value * 100).toFixed(2)}%`
    const num = (value: number | undefined) => Number.isFinite(value) ? value!.toFixed(4) : '—'
    const groups = report.group_mean_returns.map(pct).join(' / ')
    const decay = report.ic_decay.map(num).join(' / ')
    el.innerHTML = `
      <div class="factor-report-grid">
        <div><span>结论</span><strong>${report.verdict || '—'}</strong></div>
        <div><span>有效期数</span><strong>${report.n_periods}</strong></div>
        <div><span>平均截面数</span><strong>${report.avg_n_stocks}</strong></div>
        <div><span>Rank IC</span><strong>${num(report.rank_ic_mean)}</strong></div>
        <div><span>IC IR</span><strong>${num(report.ic_ir)}</strong></div>
        <div><span>IC 正向比例</span><strong>${pct(report.ic_positive_ratio)}</strong></div>
        <div><span>多空价差</span><strong>${pct(report.long_short_spread)}</strong></div>
        <div><span>多空 t 统计</span><strong>${num(report.long_short_t_stat)}</strong></div>
        <div><span>单调性</span><strong>${num(report.monotonicity_score)}</strong></div>
        <div><span>因子换手率</span><strong>${pct(report.factor_turnover)}</strong></div>
        <div><span>头部四分位换手</span><strong>${pct(report.top_quarter_turnover)}</strong></div>
        <div><span>衰减半衰期</span><strong>${report.decay_half_life >= 0 ? `${report.decay_half_life}期` : '—'}</strong></div>
      </div>
      <div class="factor-report-detail"><b>分组收益（低 → 高）</b><span>${groups || '—'}</span></div>
      <div class="factor-report-detail"><b>IC 衰减</b><span>${decay || '—'}</span></div>`
    el.hidden = false
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

    // 初始化因子暴露面板
    if (!this.factorExposure) {
      const exposureEl = this.container!.querySelector('.research-tab-content[data-tab="exposure"]') as HTMLElement
      if (exposureEl) {
        this.factorExposure = new FactorExposure()
        this.factorExposure.init(exposureEl)
      }
    }
    this.factorExposure?.show(fundCode)
    if (rp.activeTab === 'evaluation') void this.loadFactorList()
  }

  hide(): void {
    const panel = this.container?.querySelector('.research-panel') as HTMLElement
    if (panel) panel.style.display = 'none'
  }

  destroy(): void {
    this.factorExposure?.destroy()
    this.unsub?.()
  }
}
