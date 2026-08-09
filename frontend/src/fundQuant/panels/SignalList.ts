import { PanelBase } from '../layout'
import { fundQuantApi } from '../api'
import { state } from '../state'

const DIR_LABEL: Record<string, string> = { buy: '↑买入', sell: '↓卖出', hold: '→持有' }
const DIR_CLASS: Record<string, string> = { buy: 'tag-buy', sell: 'tag-sell', hold: 'tag-hold' }

export class SignalList extends PanelBase {
  private sseSource: EventSource | null = null
  private refreshTimer: ReturnType<typeof setInterval> | null = null

  constructor() {
    super({ id: 'signal_list', title: '信号列表', defaultGridPos: { x: 0, y: 2, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-signallist'
    el.innerHTML = `
      <div class="panel-header">
        <h3>信号列表</h3>
        <div class="panel-toolbar">
          <select class="sig-filter">
            <option value="all">全部</option>
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
          <span class="sig-sse-dot"></span>
        </div>
      </div>
      <div class="sig-table-wrapper">
        <table class="sig-table">
          <thead>
            <tr>
              <th>基金</th>
              <th>方向</th>
              <th class="text-right">置信度</th>
              <th>策略</th>
              <th>建议</th>
              <th class="text-right">时间</th>
              <th style="width:40px;"></th>
            </tr>
          </thead>
          <tbody class="sig-tbody"></tbody>
        </table>
      </div>
      <div class="sig-footer">
        <span>实时推送 SSE</span>
      </div>`
    return el
  }

  protected afterMount(): void {
    // 过滤切换 → 客户端过滤（调用 refresh 重新获取后由 render 过滤）
    this.el?.querySelector('.sig-filter')?.addEventListener('change', () => this.refresh())

    // 行点击 → 选中基金
    this.el?.querySelector('.sig-tbody')?.addEventListener('click', (e) => {
      const studyBtn = (e.target as HTMLElement).closest('.sig-study-btn')
      if (studyBtn) {
        // 研究按钮 → 打开研究区
        const btn = studyBtn as HTMLElement
        const code = btn.dataset.code || ''
        const signal = {
          direction: btn.dataset.direction || 'hold',
          confidence: parseFloat(btn.dataset.confidence || '0'),
          strategy_name: btn.dataset.strategy || '',
          timestamp: btn.dataset.timestamp || '',
          fund_code: code,
          fund_name: '',
        } as any
        const fundName = state.get('fundPool').find(f => f.fund_code === code)?.fund_name || ''
        signal.fund_name = fundName
        state.set('researchPanel', { visible: true, activeTab: 'timing', fundCode: code, signal })
        return
      }
      const row = (e.target as HTMLElement).closest<HTMLElement>('[data-code]')
      if (row) state.set('selectedFund', row.dataset.code || null)
    })

    // SSE
    this.startSSE()

    // 定时刷新
    this.refreshTimer = setInterval(() => this.refresh(), 30000)
  }

  private setDotColor(color: string): void {
    const dot = this.el?.querySelector('.sig-sse-dot') as HTMLElement
    if (dot) dot.style.background = color
  }

  private startSSE(): void {
    this.sseSource = new EventSource('/api/fund-quant/signal/stream')
    this.sseSource.onopen = () => this.setDotColor('#10b981')
    this.sseSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'heartbeat') return
        if (data.fund?.code) {
          this.refresh()
          // 闪烁琥珀色提示新信号
          this.setDotColor('#fbbf24')
          setTimeout(() => this.setDotColor('#10b981'), 2000)
        }
      } catch { /* ignore parse errors */ }
    }
    this.sseSource.onerror = () => {
      // 标记断开 + 10s 重连
      this.setDotColor('#94a3b8')
      this.sseSource?.close()
      setTimeout(() => this.startSSE(), 10000)
    }
  }

  async refresh(): Promise<void> {
    if (!this.el) return
    const filter = (this.el.querySelector('.sig-filter') as HTMLSelectElement)?.value || 'all'
    try {
      const res = await fundQuantApi.getLatestSignals()
      let signals = res.data || []

      // 只显示 fundPool 中存在的基金信号，过滤测试数据
      const poolCodes = new Set(state.get('fundPool').map(f => f.fund_code))
      if (poolCodes.size > 0) {
        signals = signals.filter(s => poolCodes.has(s.fund_code))
      }

      // 去重：每只基金只保留一条信号
      // 融合信号(signal_fusion)最优先——它是所有策略的加权综合，含贡献策略说明；
      // 其余信号作为兜底（旧 timing 策略已废弃删除）
      const FUSION_STRATEGY = 'signal_fusion'
      const TIMING_STRATEGIES = new Set<string>([])
      const bestByFund = new Map<string, any>()
      for (const s of signals) {
        const score = s.strategy_name === FUSION_STRATEGY ? 3
          : TIMING_STRATEGIES.has(s.strategy_name) ? 2
          : 0
        const existing = bestByFund.get(s.fund_code)
        if (!existing) {
          bestByFund.set(s.fund_code, s)
        } else {
          const existingScore = existing.strategy_name === FUSION_STRATEGY ? 3
            : TIMING_STRATEGIES.has(existing.strategy_name) ? 2
            : 0
          if (score > existingScore || (score === existingScore && (s.confidence || 0) > (existing.confidence || 0))) {
            bestByFund.set(s.fund_code, s)
          }
        }
      }
      signals = Array.from(bestByFund.values())

      if (filter !== 'all') signals = signals.filter(s => s.direction === filter)

      const tbody = this.el.querySelector('.sig-tbody')
      if (!tbody) return

      if (!signals.length) {
        tbody.innerHTML = `<tr><td colspan="7" style="padding:24px;text-align:center;color:var(--text-tertiary);font-size:13px;">暂无信号数据</td></tr>`
        state.set('signals', [])
        return
      }

      // 生成行html，如果是新数据则标记 glow class
      const existingRows = tbody.querySelectorAll('tr[data-code]')
      const existingKeys = new Set<string>()
      existingRows.forEach(r => existingKeys.add(r.getAttribute('data-key') || ''))

      tbody.innerHTML = signals.slice(0, 50).map(s => {
        const key = `${s.fund_code}|${s.direction}|${s.confidence}`
        const isNew = existingRows.length > 0 && !existingKeys.has(key)
        return `<tr data-code="${s.fund_code}" data-key="${key}" class="${isNew ? 'sig-row-new' : ''}">
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);">
            <span style="font-weight:600;color:var(--text-primary);">${s.fund_name || s.fund_code}</span>
            <span style="font-size:11px;color:var(--text-tertiary);margin-left:4px;">${s.fund_code}</span>
          </td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);">
            <span class="${DIR_CLASS[s.direction] || ''}" style="font-weight:600;">${DIR_LABEL[s.direction] || s.direction}</span>
          </td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);text-align:right;">
            <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;">
              <span style="color:var(--text-primary);font-weight:600;">${(s.confidence * 100).toFixed(0)}%</span>
              <div style="width:50px;height:6px;background:var(--bg-tertiary);border-radius:3px;">
                <div style="height:100%;width:${s.confidence * 100}%;background:${s.confidence > 0.7 ? '#10b981' : s.confidence > 0.5 ? '#f59e0b' : '#94a3b8'};border-radius:3px;transition:width 0.3s;"></div>
              </div>
            </div>
          </td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);color:var(--text-secondary);font-size:12px;">
            ${s.strategy_name === 'signal_fusion'
              ? `<span title="${s.reason || ''}" style="cursor:help;color:var(--primary-color);font-weight:600;">融合</span>`
              : s.strategy_name || '-'}
          </td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);font-size:12px;">
            ${this.adviceHtml(s)}
          </td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);text-align:right;color:var(--text-tertiary);font-size:11px;">${(s.created_at || '').slice(5, 16)}</td>
          <td style="padding:2px 6px;border-bottom:1px solid var(--border-light);text-align:center;">
            <button class="btn btn-sm btn-ghost sig-study-btn" data-code="${s.fund_code}" data-direction="${s.direction}" data-confidence="${s.confidence}" data-strategy="${s.strategy_name || ''}" data-timestamp="${s.created_at || ''}" title="研究此信号" style="font-size:11px;padding:1px 4px;">🔍</button>
          </td>
        </tr>`
      }).join('')

      state.set('signals', signals.map(s => ({
        direction: s.direction as any,
        confidence: s.confidence,
        strategy_name: s.strategy_name,
        timestamp: s.created_at,
        fund_code: s.fund_code,
        fund_name: s.fund_name || '',
      })))
    } catch { /* 静默保留旧数据 */ }
  }

  destroy(): void {
    this.sseSource?.close()
    if (this.refreshTimer) clearInterval(this.refreshTimer)
    super.destroy()
  }

  onActivated(): void {
    this.refresh()
  }

  /** 根据方向+置信度+策略数生成建议文字 */
  private adviceHtml(s: any): string {
    const conf = s.confidence || 0
    const dir = s.direction
    if (dir === 'hold') return '<span style="color:var(--text-tertiary);">建议持有</span>'

    // 定投信号单独文案（smart_dca 已废弃）

    // 融合信号：提取策略数
    const isFusion = s.strategy_name === 'signal_fusion'
    let n = 1
    if (isFusion && s.reason) {
      const m = s.reason.match(/融合 (\d+) 个策略/)
      if (m) n = parseInt(m[1], 10)
    }

    const buy = dir === 'buy'
    const isStrong = conf >= 0.7
    const isMedium = conf >= 0.5

    let text: string
    let color: string

    if (isStrong) {
      text = buy ? '建议加仓/买入' : '建议减仓/卖出'
      color = buy ? '#10b981' : '#ef4444'
    } else if (isMedium && isFusion && n >= 2) {
      // 多策略一致 → 升级
      text = buy ? '建议加仓' : '建议减仓'
      color = buy ? '#10b981' : '#ef4444'
    } else if (isMedium) {
      text = buy ? '可小幅加仓' : '可小幅减仓'
      color = '#f59e0b'
    } else {
      text = '建议观望'
      color = 'var(--text-tertiary)'
    }

    return `<span style="color:${color};font-weight:600;">${text}</span>`
  }
}
