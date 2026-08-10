import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type StrategyAllocationSignal } from '../api'
import { state } from '../state'

const DIR_LABEL: Record<string, string> = { buy: '建议买入', hold: '建议持有', sell: '建议卖出' }
const DIR_COLOR: Record<string, string> = { buy: '#10b981', hold: '#f59e0b', sell: '#ef4444' }

/**
 * 策略配置面板 — 给投资建议（买什么、买多少、怎么操作）
 *
 * 每个策略给出当前配置建议 + 买入金额 + 动量排名候选。
 */
export class SignalList extends PanelBase {
  private charts: Map<string, echarts.ECharts> = new Map()

  constructor() {
    super({ id: 'signal_list', title: '策略配置', defaultGridPos: { x: 0, y: 2, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-signallist'
    el.innerHTML = `
      <div class="panel-header">
        <h3>📊 策略配置</h3>
        <div class="panel-toolbar">
          <label style="font-size:11px;color:var(--text-tertiary);">总资产</label>
          <input class="alloc-capital" type="number" value="100000" min="0" step="10000"
            style="width:90px;font-size:11px;padding:2px 4px;border:1px solid var(--border-light);border-radius:4px;background:var(--bg-primary);color:var(--text-primary);">
          <button class="btn btn-sm btn-primary btn-refresh-alloc" style="font-weight:600;">🔄 刷新</button>
        </div>
      </div>
      <div class="alloc-strategies"></div>
      <div class="alloc-msg" style="font-size:12px;color:var(--text-tertiary);padding:12px;text-align:center;">加载中…</div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.btn-refresh-alloc')?.addEventListener('click', () => this.refresh())
    this.el?.querySelector('.alloc-capital')?.addEventListener('change', () => this.refresh())
    this.refresh()
  }

  private getCapital(): number {
    const el = this.el?.querySelector('.alloc-capital') as HTMLInputElement
    const v = parseFloat(el?.value || '100000')
    return isNaN(v) || v <= 0 ? 100000 : v
  }

  async refresh(): Promise<void> {
    const container = this.el?.querySelector('.alloc-strategies') as HTMLElement
    const msg = this.el?.querySelector('.alloc-msg') as HTMLElement
    if (!container) return

    const codes = state.get('fundPool').map(f => f.fund_code)
    if (!codes.length) {
      msg!.textContent = '基金池为空，请先加载基金数据'
      return
    }

    try {
      const capital = this.getCapital()
      const res = await fundQuantApi.getStrategyAllocation(codes, capital)
      if (!res.success) throw new Error('请求失败')
      const strategies = res.data.strategies
      if (!strategies.length) {
        msg!.textContent = '暂无策略配置信号'
        return
      }
      msg!.textContent = ''
      this.renderAllocationCards(container, strategies)
    } catch (e: any) {
      msg!.textContent = `❌ ${e?.message || '获取策略配置失败'}`
    }
  }

  private renderAllocationCards(container: HTMLElement, strategies: StrategyAllocationSignal[]): void {
    this.charts.forEach(chart => chart.dispose())
    this.charts.clear()
    container.innerHTML = ''

    for (const s of strategies) {
      const card = document.createElement('div')
      card.style.cssText = 'margin-bottom:10px;border:1px solid var(--border-light);border-radius:8px;padding:12px;background:var(--bg-secondary);'

      const name = s.strategy === 'etf_rotation_aurora' ? 'ETF动量轮动' : '桥水全天候'
      const modeLabel = s.mode ? ` (${s.mode})` : ''
      const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
      const pool = state.get('fundPool')
      // 名称表：优先 top_holdings 提供的 fund_name，其次 fundPool
      const nameMap: Record<string, string> = {}
      for (const th of s.top_holdings || []) {
        if (th.fund_name) nameMap[th.fund_code] = th.fund_name
      }
      const fundName = (code: string) => nameMap[code] || pool.find(f => f.fund_code === code)?.fund_name || code

      card.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">
          <strong style="font-size:14px;">${name}${modeLabel}</strong>
          <span style="font-size:11px;color:var(--text-tertiary);">信心 ${(s.confidence * 100).toFixed(0)}%</span>
        </div>
        ${this.buildActionAdvice(s, entries, fundName)}
        <div style="margin-top:6px;font-size:11px;color:var(--text-tertiary);">${s.reason || ''}</div>`

      // 动量排名候选（etf_rotation）
      if (s.strategy === 'etf_rotation_aurora' && s.momentum_rank && s.momentum_rank.length > 1) {
        const topCode = entries[0]?.[0]
        const rankHtml = s.momentum_rank.slice(0, 5).map(m => {
          const isTop = m.fund_code === topCode
          return `<div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0;${isTop ? 'color:var(--primary-color);font-weight:600;' : 'color:var(--text-secondary);'}">
            <span>${isTop ? '✅ ' : ''}${fundName(m.fund_code)} (${m.fund_code})</span>
            <span>Score ${m.score.toFixed(4)}</span>
          </div>`
        }).join('')
        card.appendChild(this.buildCollapsible('📈 动量排名候选', `查看各资产动量得分`, rankHtml))
      }

      // 权重分布（all_weather 多只）
      if (s.strategy === 'all_weather_aurora' && entries.length > 1) {
        const colors = ['#4a90d9', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#ec4899', '#14b8a6', '#f97316']
        const chartWrap = document.createElement('div')
        chartWrap.style.cssText = 'display:none;height:140px;width:100%;margin-top:4px;'
        const toggle = document.createElement('span')
        toggle.style.cssText = 'font-size:11px;color:var(--primary-color);cursor:pointer;margin-top:4px;display:inline-block;'
        toggle.textContent = '📊 查看权重分布'
        toggle.dataset.expanded = 'false'
        toggle.addEventListener('click', () => {
          const expanded = toggle.dataset.expanded === 'true'
          chartWrap.style.display = expanded ? 'none' : ''
          toggle.dataset.expanded = expanded ? 'false' : 'true'
          toggle.textContent = expanded ? '📊 查看权重分布' : '📊 收起'
          if (!expanded) {
            setTimeout(() => {
              const chart = echarts.init(chartWrap)
              this.charts.set(s.strategy, chart)
              chart.setOption({
                tooltip: { trigger: 'item', formatter: '{b}: {c}%' },
                series: [{
                  type: 'pie', radius: ['25%', '60%'],
                  data: entries.map(([code, weight], i) => ({
                    name: fundName(code),
                    value: parseFloat((weight * 100).toFixed(1)),
                    itemStyle: { color: colors[i % colors.length] },
                  })),
                  label: { show: true, fontSize: 10 },
                }],
              })
            }, 50)
          }
        })
        card.appendChild(toggle)
        card.appendChild(chartWrap)
      }

      container.appendChild(card)
    }
  }

  private buildCollapsible(label: string, hint: string, contentHtml: string): HTMLElement {
    const wrap = document.createElement('div')
    wrap.style.cssText = 'margin-top:6px;'
    const toggle = document.createElement('span')
    toggle.style.cssText = 'font-size:11px;color:var(--primary-color);cursor:pointer;'
    toggle.textContent = `${label}`
    toggle.dataset.expanded = 'false'
    const body = document.createElement('div')
    body.style.cssText = 'display:none;margin-top:4px;padding:6px 8px;background:var(--bg-primary);border-radius:4px;'
    body.innerHTML = contentHtml
    toggle.addEventListener('click', () => {
      const expanded = toggle.dataset.expanded === 'true'
      body.style.display = expanded ? 'none' : ''
      toggle.dataset.expanded = expanded ? 'false' : 'true'
      toggle.title = expanded ? hint : ''
    })
    wrap.appendChild(toggle)
    wrap.appendChild(body)
    return wrap
  }

  /** 生成投资建议文案（核心：买什么、买多少、怎么操作） */
  private buildActionAdvice(s: StrategyAllocationSignal, entries: [string, number][], fundName: (code: string) => string): string {
    const dirColor = DIR_COLOR[s.direction] || 'var(--text-tertiary)'
    const money = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`

    if (s.strategy === 'etf_rotation_aurora') {
      if (s.direction === 'buy' && entries.length > 0) {
        const [code, weight] = entries[0]
        const name = fundName(code)
        const pct = (weight * 100).toFixed(0)
        const amount = s.buy_amounts?.[code] || 0
        return `<div style="padding:8px;background:${dirColor}15;border-radius:6px;border-left:3px solid ${dirColor};">
          <div style="font-size:13px;font-weight:700;color:${dirColor};">买入 <span style="font-size:15px;">${name}</span> (${code})</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">满仓配置（${pct}%），买入金额 <strong style="color:${dirColor};">${money(amount)}</strong></div>
        </div>`
      }
      return `<div style="padding:8px;background:var(--bg-tertiary);border-radius:6px;font-size:13px;color:var(--text-tertiary);">
        ⏸ 建议空仓持币观望，当前无合适标的
      </div>`
    }

    // all_weather
    if (entries.length > 0) {
      const items = entries.map(([code, w]) => {
        const name = fundName(code)
        const pct = (w * 100).toFixed(1)
        const amount = s.buy_amounts?.[code] || 0
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 6px;margin:2px 0;background:var(--bg-primary);border-radius:4px;font-size:12px;">
          <span>${name} <span style="color:var(--text-tertiary);font-size:11px;">${code}</span></span>
          <span style="white-space:nowrap;"><strong>${pct}%</strong> <span style="color:var(--text-tertiary);font-size:11px;">${money(amount)}</span></span>
        </div>`
      }).join('')
      return `<div style="padding:8px;background:var(--bg-tertiary);border-radius:6px;border-left:3px solid #3b82f6;">
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">按以下比例配置资产（总资产 ${money(s.capital || 0)}）：</div>
        <div>${items}</div>
      </div>`
    }
    return ''
  }

  destroy(): void {
    this.charts.forEach(chart => chart.dispose())
    this.charts.clear()
    super.destroy()
  }

  onActivated(): void {
    this.charts.forEach(chart => chart.resize())
    this.refresh()
  }
}