// frontend/src/fundQuant/panels/DetailPanel.ts
import { PanelBase } from '../layout'
import { fundQuantApi, type StrategyAllocationSignal } from '../api'
import { state, persistBlViews, type BlView } from '../state'

const NAME_MAP: Record<string, string> = {
  etf_rotation_aurora: 'ETF动量轮动',
  all_weather_aurora: '桥水全天候',
  bl_quadrant_aurora: 'BL四象限观点',
  black_litterman_aurora: 'Black-Litterman',
  risk_parity_aurora: '风险平价',
  hrp_aurora: '层次风险平价(HRP)',
  max_diversification_aurora: '最大多元化(MDP)',
}

const CONF_OPTIONS: Array<{ v: string; label: string }> = [
  { v: 'high', label: '高' },
  { v: 'mid', label: '中' },
  { v: 'low', label: '低' },
]

/** 策略一句话说明 */
const STRATEGY_DESC: Record<string, string> = {
  etf_rotation_aurora: '动量轮动：按近期涨幅排名持有最强ETF，趋势反转时切换，适合单边行情。',
  all_weather_aurora: '桥水全天候：按风险均衡配置股票/债券/商品等资产，不预测涨跌，追求任何环境下都稳健。',
  bl_quadrant_aurora: 'BL四象限：按宏观环境（增长×通胀四象限）给出各资产观点，再经 Black-Litterman 融合定价。',
  black_litterman_aurora: 'Black-Litterman：以市场均衡收益为基准，融合你的主观观点得到后验收益，再做均值-方差优化。观点是对池内具体基金的相对强弱判断（如"A 跑赢 B 3%"），而非行业或指数层面的判断；无观点时退化为纯均值-方差优化（常接近等权）。',
  risk_parity_aurora: '风险平价：让每只基金对组合的风险贡献相等，波动大的配得少、波动小的配得多。',
  hrp_aurora: '层次风险平价：用相关性聚类分层后分配风险，比风险平价更抗相关性突变。',
  max_diversification_aurora: '最大多元化：最大化分散比率，优先挑彼此相关性低的基金组合。',
}

/**
 * 策略详情面板 — 展示选中策略的权重、与当前持仓的差异、操作建议
 * black_litterman_aurora 额外展示 BL 观点设置（相对观点 + 置信度三档）
 */
export class DetailPanel extends PanelBase {
  private unsub: (() => void) | null = null
  private strategies: StrategyAllocationSignal[] = []
  private blUnsub: (() => void) | null = null
  private helpOpen = false // BL 观点帮助折叠态（跨重绘保持）

  constructor() {
    super({ id: 'detail', title: '策略详情', defaultGridPos: { x: 1, y: 2, w: 2, h: 2 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-detail'
    el.style.display = 'none'
    el.innerHTML = `
      <div class="panel-header">
        <h3 id="detail-title">策略详情</h3>
        <button class="detail-close" style="background:none;border:none;cursor:pointer;font-size:18px;color:var(--text-secondary);padding:0 4px;" title="关闭">&#x2715;</button>
      </div>
      <div class="detail-body" style="padding:12px 16px;max-height:520px;overflow-y:auto;"></div>`
    return el
  }

  protected afterMount(): void {
    this.el?.querySelector('.detail-close')?.addEventListener('click', () => {
      if (this.el) this.el.style.display = 'none'
    })

    let seq = 0
    this.unsub = state.on('selectedStrategy', async () => {
      const my = ++seq
      const strategy = state.get('selectedStrategy')
      if (!strategy || !this.el) return
      this.el.style.display = ''
      await this.loadStrategyDetail(strategy)
      // 过期响应丢弃（快速连点只渲染最后一次）
      if (my !== seq) return
    })

    // BL 观点变更时，若当前正是 BL 则刷新
    this.blUnsub = state.on('blViews', async () => {
      const s = state.get('selectedStrategy')
      if (s === 'black_litterman_aurora') await this.loadStrategyDetail(s)
      // 同时让左侧列表同步刷新（带观点）
      const sl = document.querySelector('.panel-signallist') ? (await import('./SignalList')).SignalList : null
      void sl
    })
  }

  async refresh(): Promise<void> {
    const strategy = state.get('selectedStrategy')
    if (strategy) await this.loadStrategyDetail(strategy)
  }

  /** 预加载策略列表（从 SignalList 获取） */
  setStrategies(strategies: StrategyAllocationSignal[]): void {
    this.strategies = strategies
  }

  private allocParams(): Record<string, any> {
    const s = state.get('selectedStrategy')
    if (s === 'black_litterman_aurora') {
      const views = state.get('blViews') || []
      if (views.length) return { views }
    }
    return {}
  }

  private async loadStrategyDetail(strategyName: string): Promise<void> {
    if (!this.el) return
    const body = this.el.querySelector('.detail-body') as HTMLElement
    const title = this.el.querySelector('#detail-title') as HTMLElement
    if (!body) return

    title.textContent = NAME_MAP[strategyName] || strategyName

    // 先用缓存极速渲染（命中则 <50ms），未命中时先展示骨架避免白屏
    const cached = this.strategies.find(x => x.strategy === strategyName)
    const needFetch = !cached || strategyName === 'black_litterman_aurora'
    if (!cached) {
      body.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;padding:12px;">加载中…</div>'
    } else if (!needFetch) {
      // 有缓存且非 BL：先渲染旧数据，再后台静默刷新持仓（不阻塞首帧）
      let currentPositions: Record<string, number> = {}
      body.innerHTML = this.buildDetailHtml(cached, currentPositions)
      this.bindConfirmButtons(cached)
      // 后台补持仓差异，不阻塞
      fundQuantApi.getPortfolioKPI().then(pRes => {
        if (!pRes.success || !pRes.data.positions) return
        const totalVal = pRes.data.total_value || 1
        for (const [code, pos] of Object.entries(pRes.data.positions)) currentPositions[code] = (pos as any).value / totalVal
        // 仅当仍在同一策略时重绘
        if (state.get('selectedStrategy') !== strategyName) return
        body.innerHTML = this.buildDetailHtml(cached, currentPositions)
        this.bindConfirmButtons(cached)
      }).catch(() => {})
      return
    }

    // 需拉取时：分配结果与持仓并行请求
    const codes = state.get('fundPool').map(f => f.fund_code)
    if (!codes.length) { body.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;">无基金数据</div>'; return }
    const params = this.allocParams()
    const allocP = needFetch ? fundQuantApi.getStrategyAllocation(codes, 100000, params) : Promise.resolve(null as any)
    const portfolioP = fundQuantApi.getPortfolioKPI().catch(() => null as any)
    const [allocRes, pRes] = await Promise.all([allocP, portfolioP])
    if (allocRes?.success) this.strategies = allocRes.data.strategies

    const s = this.strategies.find(x => x.strategy === strategyName)
    if (!s) {
      body.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;">策略数据加载中…</div>'
      return
    }

    let currentPositions: Record<string, number> = {}
    if (pRes?.success && pRes.data.positions) {
      const totalVal = pRes.data.total_value || 1
      for (const [code, pos] of Object.entries(pRes.data.positions)) currentPositions[code] = (pos as any).value / totalVal
    }

    // 仍在同一策略才渲染（配合外层 seq 丢弃过期）
    if (state.get('selectedStrategy') !== strategyName) return
    body.innerHTML = this.buildDetailHtml(s, currentPositions)
    this.bindConfirmButtons(s)
    if (s.strategy === 'black_litterman_aurora') this.bindBlViews(s)
  }

  private buildBlViewsHtml(): string {
    const pool = state.get('fundPool')
    const views = state.get('blViews') || []
    // <details> 的展开态保存在 DOM 属性上，重绘后恢复，避免「添加观点」把帮助收起
    const openAttr = (this.helpOpen ? 'open' : '')
    const optHtml = (selCode?: string) => pool.map(f =>
      `<option value="${f.fund_code}" ${f.fund_code===selCode?'selected':''}>${f.fund_name}（${f.fund_code}）</option>`).join('')
    const confOpts = (sel: string) => CONF_OPTIONS.map(o => `<option value="${o.v}" ${o.v===sel?'selected':''}>${o.label}</option>`).join('')
    const rows = views.map((v, idx) => `
      <div class="bl-view-row" data-idx="${idx}" style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <select class="bl-long" style="flex:1;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">
          <option value="">看多…</option>${optHtml(v.fund_long)}
        </select>
        <span style="font-size:11px;color:var(--text-tertiary);">跑赢</span>
        <select class="bl-short" style="flex:1;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">
          <option value="">看空…</option>${optHtml(v.fund_short)}
        </select>
        <input class="bl-excess" type="number" step="0.5" value="${v.excess_return}" style="width:72px;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;" placeholder="%" title="年化超额收益%">
        <span style="font-size:11px;color:var(--text-tertiary);">%</span>
        <select class="bl-conf" style="width:64px;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">${confOpts(v.confidence)}</select>
        <button class="bl-del" style="background:none;border:none;cursor:pointer;color:#ef4444;font-size:14px;padding:2px 4px;" title="删除">✕</button>
      </div>`).join('')

    const emptyTip = views.length === 0
      ? `<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px;">未设置观点时，Black-Litterman 退化为纯均值-方差优化（结果常接近等权）。设置观点后才会体现你的判断。</div>`
      : ''

    return `
      <div style="margin:10px 0 14px;padding:10px;border:1px dashed var(--border-light);border-radius:8px;background:var(--bg-secondary);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <div style="font-size:12px;font-weight:600;">📝 BL 观点（相对观点）</div>
          <span style="font-size:11px;color:var(--text-tertiary);">${views.length} 条</span>
        </div>
        ${emptyTip}
        <details class="bl-help" ${openAttr} style="font-size:11px;color:var(--text-tertiary);margin-bottom:8px;">
          <summary style="cursor:pointer;color:var(--text-secondary);">什么是观点？怎么填？</summary>
          <div style="line-height:1.7;margin-top:6px;">
            观点 = 你对<strong>池内两只基金</strong>相对强弱的判断，例如「沪深300ETF 未来一年跑赢 国债ETF 5%」。模型会把它融合进均衡收益，看多的权重升高、看空的降低；超额越大、置信度越高，权重偏移越明显。<br/>
            • 看多/看空：从基金池里选具体品种（不是行业或指数）<br/>
            • 超额 %：预期年化跑赢幅度<br/>
            • 置信度：高=90%、中=60%、低=30%，决定该观点的话语权
          </div>
        </details>
        <div class="bl-views-list">${rows || '<div style="font-size:11px;color:var(--text-tertiary);padding:6px 0;">暂无观点，点击下方添加</div>'}</div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <button class="bl-add btn btn-sm" style="font-size:12px;padding:4px 10px;background:var(--bg-tertiary);border:1px solid var(--border-light);border-radius:6px;cursor:pointer;">＋ 添加观点</button>
          <button class="bl-clear btn btn-sm" style="font-size:12px;padding:4px 10px;background:none;border:1px solid var(--border-light);border-radius:6px;cursor:pointer;color:var(--text-tertiary);">清空</button>
          <button class="bl-apply btn btn-sm btn-primary" style="font-size:12px;padding:4px 12px;margin-left:auto;">应用并刷新</button>
        </div>
        <div class="bl-msg" style="font-size:11px;color:#ef4444;margin-top:6px;min-height:14px;"></div>
      </div>`
  }

  private bindBlViews(_s: StrategyAllocationSignal): void {
    if (!this.el) return
    const body = this.el.querySelector('.detail-body') as HTMLElement
    if (!body) return

    // 帮助折叠态跨重绘保持
    body.querySelector('.bl-help')?.addEventListener('toggle', (e) => {
      this.helpOpen = (e.target as HTMLDetailsElement).open
    })

    // 原样读取所有行（含未填完的），用于静默持久化，避免丢用户编辑中的内容
    const rawRowsFromDom = (): BlView[] => {
      const out: BlView[] = []
      body.querySelectorAll('.bl-view-row').forEach(row => {
        out.push({
          fund_long: (row.querySelector('.bl-long') as HTMLSelectElement)?.value?.trim() || '',
          fund_short: (row.querySelector('.bl-short') as HTMLSelectElement)?.value?.trim() || '',
          excess_return: parseFloat((row.querySelector('.bl-excess') as HTMLInputElement)?.value || '') || 0,
          confidence: (row.querySelector('.bl-conf') as HTMLSelectElement)?.value || 'mid',
        })
      })
      return out
    }

    // 只取有效行，用于应用计算
    const readViewsFromDom = (): BlView[] =>
      rawRowsFromDom().filter(v => v.fund_long && v.fund_short && v.fund_long !== v.fund_short && v.excess_return !== 0)

    const showMsg = (msg: string, ok = false) => {
      const el = body.querySelector('.bl-msg') as HTMLElement
      if (el) { el.textContent = msg; el.style.color = ok ? 'var(--primary-color)' : '#ef4444' }
    }

    // 行级操作（增删改）只改 DOM + 静默保存，不触发重算 —— 点「应用并刷新」才重算
    const persistSilent = () => persistBlViews(rawRowsFromDom())

    const bindRowEvents = (row: Element) => {
      row.querySelector('.bl-del')?.addEventListener('click', () => {
        row.remove()
        const list = body.querySelector('.bl-views-list') as HTMLElement
        if (list && !list.querySelector('.bl-view-row')) {
          list.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);padding:6px 0;">暂无观点，点击下方添加</div>'
        }
        persistSilent()
      })
    }

    body.querySelector('.bl-add')?.addEventListener('click', () => {
      const pool = state.get('fundPool')
      if (pool.length < 2) { showMsg('基金池不足2只，无法添加'); return }
      const list = body.querySelector('.bl-views-list') as HTMLElement
      list.querySelector('div[style*="padding:6px 0"]')?.remove() // 移除「暂无观点」占位
      const row = document.createElement('div')
      row.className = 'bl-view-row'
      row.dataset.idx = String(list.querySelectorAll('.bl-view-row').length)
      row.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:6px;'
      const optHtml = pool.map(f => `<option value="${f.fund_code}">${f.fund_name}（${f.fund_code}）</option>`).join('')
      row.innerHTML = `
        <select class="bl-long" style="flex:1;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">
          <option value="">看多…</option>${optHtml}
        </select>
        <span style="font-size:11px;color:var(--text-tertiary);">跑赢</span>
        <select class="bl-short" style="flex:1;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">
          <option value="">看空…</option>${optHtml}
        </select>
        <input class="bl-excess" type="number" step="0.5" value="3" style="width:72px;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;" placeholder="%" title="年化超额收益%">
        <span style="font-size:11px;color:var(--text-tertiary);">%</span>
        <select class="bl-conf" style="width:64px;font-size:12px;padding:4px;border:1px solid var(--border-light);border-radius:4px;">${CONF_OPTIONS.map(o => `<option value="${o.v}" ${o.v==='mid'?'selected':''}>${o.label}</option>`).join('')}</select>
        <button class="bl-del" style="background:none;border:none;cursor:pointer;color:#ef4444;font-size:14px;padding:2px 4px;" title="删除">✕</button>`
      list.appendChild(row)
      bindRowEvents(row)
      persistSilent()
    })

    body.querySelector('.bl-clear')?.addEventListener('click', () => {
      const list = body.querySelector('.bl-views-list') as HTMLElement
      if (list) list.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);padding:6px 0;">暂无观点，点击下方添加</div>'
      persistSilent()
    })

    body.querySelectorAll('.bl-view-row').forEach(bindRowEvents)

    // 应用：读取全部行校验后广播（左侧列表+详情带新观点重算）
    body.querySelector('.bl-apply')?.addEventListener('click', () => {
      const domViews = readViewsFromDom()
      const poolCodes = new Set(state.get('fundPool').map(f => f.fund_code))
      const valid = domViews.filter(v => poolCodes.has(v.fund_long) && poolCodes.has(v.fund_short))
      if (domViews.length !== valid.length) showMsg('部分观点含未知基金代码，已忽略')
      else if (valid.length === 0 && domViews.length > 0) { showMsg('观点无效：需选择两只不同基金且超额收益≠0'); return }
      persistBlViews(valid, true)
      showMsg(valid.length ? `已应用 ${valid.length} 条观点，正在刷新…` : '已清空观点（退化为均衡收益）', true)
    })
  }

  private buildDetailHtml(s: StrategyAllocationSignal, currentPos: Record<string, number>): string {
    const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const money = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
    const capital = s.capital || 100000

    // 1. 策略权重表格
    const weightRows = entries.map(([code, w]) => {
      const cur = currentPos[code] || 0
      const diff = w - cur
      const diffColor = Math.abs(diff) < 0.005 ? 'var(--text-tertiary)' : diff > 0 ? 'var(--primary-color)' : '#ef4444'
      const diffLabel = Math.abs(diff) < 0.005 ? '—' : `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}%`
      const amount = s.buy_amounts?.[code] || 0
      return `<tr style="border-bottom:1px solid var(--border-light);">
        <td style="padding:6px 4px;font-size:12px;">${fundName(code)}<span style="color:var(--text-tertiary);font-size:10px;margin-left:4px;">${code}</span></td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${(w * 100).toFixed(1)}%</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${cur > 0 ? (cur * 100).toFixed(1) + '%' : '—'}</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;color:${diffColor};font-weight:600;">${diffLabel}</td>
        <td style="padding:6px 4px;font-size:12px;text-align:right;">${money(amount)}</td>
      </tr>`
    }).join('')

    // 2. 操作建议
    const suggestions = this.buildSuggestions(s, entries, currentPos)
    const totalWeight = entries.reduce((a, [, w]) => a + w, 0)
    const holdingCount = Object.keys(currentPos).length
    const blSection = s.strategy === 'black_litterman_aurora' ? this.buildBlViewsHtml() : ''
    const desc = STRATEGY_DESC[s.strategy]

    return `
      ${blSection}
      <div style="margin-bottom:12px;">
        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">信心度 <strong>${(s.confidence * 100).toFixed(0)}%</strong> · 总资产 ${money(capital)}</div>
        <div style="font-size:11px;color:var(--text-tertiary);">${s.reason || ''}</div>
      </div>
      ${desc ? `<div style="margin-bottom:12px;padding:8px 10px;background:var(--bg-tertiary);border-radius:6px;font-size:11px;line-height:1.6;color:var(--text-secondary);">
        <span style="font-weight:600;">📖 策略说明：</span>${desc}
      </div>` : ''}
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="border-bottom:2px solid var(--border-light);">
          <th style="padding:6px 4px;text-align:left;color:var(--text-secondary);font-weight:600;">基金</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">目标</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">当前</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">差异</th>
          <th style="padding:6px 4px;text-align:right;color:var(--text-secondary);font-weight:600;">金额</th>
        </tr></thead>
        <tbody>${weightRows}</tbody>
      </table>
      <div style="margin-top:8px;font-size:11px;color:var(--text-tertiary);">目标权重合计 ${(totalWeight * 100).toFixed(1)}% · 当前持仓 ${holdingCount}只</div>
      <div style="margin-top:12px;padding:10px;background:var(--bg-tertiary);border-radius:6px;border-left:3px solid var(--primary-color);">
        <div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">💡 操作建议</div>
        <div style="font-size:12px;color:var(--text-secondary);line-height:1.6;">${suggestions}</div>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px;">
        <button class="btn-apply-strategy btn btn-sm btn-primary" style="flex:1;font-weight:600;">✅ 采纳策略</button>
        <button class="btn-dismiss-strategy btn btn-sm" style="flex:1;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border-light);">忽略</button>
      </div>`
  }

  private buildSuggestions(_s: StrategyAllocationSignal, entries: [string, number][], currentPos: Record<string, number>): string {
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const lines: string[] = []

    // 新增持仓
    const toBuy = entries.filter(([code]) => !currentPos[code] || currentPos[code] < 0.005)
    if (toBuy.length) {
      lines.push(`<div>📗 <strong>新增配置 ${toBuy.length}只：</strong>${toBuy.map(([c]) => fundName(c)).join('、')}</div>`)
    }

    // 需要增仓
    const toIncrease = entries.filter(([code, w]) => currentPos[code] && w - currentPos[code] > 0.01)
    if (toIncrease.length) {
      lines.push(`<div>📈 <strong>建议增仓 ${toIncrease.length}只：</strong>${toIncrease.map(([c, w]) => `${fundName(c)} → ${(w * 100).toFixed(0)}%`).join('、')}</div>`)
    }

    // 需要减仓
    const toDecrease = entries.filter(([code, w]) => currentPos[code] && currentPos[code] - w > 0.01)
    if (toDecrease.length) {
      lines.push(`<div>📉 <strong>建议减仓 ${toDecrease.length}只：</strong>${toDecrease.map(([c, w]) => `${fundName(c)} → ${(w * 100).toFixed(0)}%`).join('、')}</div>`)
    }

    // 清仓
    const toSell = Object.keys(currentPos).filter(code => !entries.find(([c]) => c === code) && currentPos[code] > 0.005)
    if (toSell.length) {
      lines.push(`<div>🔴 <strong>建议清仓 ${toSell.length}只：</strong>${toSell.map(c => fundName(c)).join('、')}</div>`)
    }

    if (!lines.length) {
      lines.push('<div>✅ 当前持仓与策略建议一致，无需调整</div>')
    }

    return lines.join('')
  }

  private bindConfirmButtons(s: StrategyAllocationSignal): void {
    this.el?.querySelector('.btn-apply-strategy')?.addEventListener('click', () => {
      this.showConfirmDialog(s)
    })
    this.el?.querySelector('.btn-dismiss-strategy')?.addEventListener('click', () => {
      state.set('selectedStrategy', null)
      if (this.el) this.el.style.display = 'none'
    })
  }

  private showConfirmDialog(s: StrategyAllocationSignal): void {
    const name = NAME_MAP[s.strategy] || s.strategy
    const entries = Object.entries(s.weights).filter(([, w]) => w > 0)
    const pool = state.get('fundPool')
    const fundName = (code: string) => pool.find(f => f.fund_code === code)?.fund_name || code
    const money = (v: number) => `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`
    const capital = s.capital || 100000

    const overlay = document.createElement('div')
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;'

    const dialog = document.createElement('div')
    dialog.style.cssText = 'background:var(--bg-primary);border-radius:12px;padding:24px;max-width:480px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.3);'

    const items = entries.map(([code, w]) => {
      const amount = s.buy_amounts?.[code] || capital * w
      return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-light);font-size:13px;">
        <span>${fundName(code)} <span style="color:var(--text-tertiary);font-size:11px;">${code}</span></span>
        <span><strong>${(w * 100).toFixed(1)}%</strong> <span style="color:var(--text-tertiary);">${money(amount)}</span></span>
      </div>`
    }).join('')

    dialog.innerHTML = `
      <h3 style="font-size:16px;font-weight:700;margin:0 0 12px;">确认采纳「${name}」策略</h3>
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">将按以下比例配置总资产 ${money(capital)}：</div>
      <div style="margin-bottom:16px;">${items}</div>
      <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:16px;">⚠️ 采纳后将模拟执行调仓操作，建议在模拟交易中验证</div>
      <div style="display:flex;gap:8px;">
        <button class="btn-confirm-yes" style="flex:1;padding:8px;background:var(--primary-color);color:white;border:none;border-radius:6px;font-weight:600;cursor:pointer;">确认采纳</button>
        <button class="btn-confirm-no" style="flex:1;padding:8px;background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border-light);border-radius:6px;cursor:pointer;">取消</button>
      </div>`

    overlay.appendChild(dialog)
    document.body.appendChild(overlay)

    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove() })
    dialog.querySelector('.btn-confirm-no')?.addEventListener('click', () => overlay.remove())
    dialog.querySelector('.btn-confirm-yes')?.addEventListener('click', async () => {
      overlay.remove()
      console.log('采纳策略:', s.strategy, s.weights)
    })
  }

  destroy(): void {
    this.unsub?.()
    this.blUnsub?.()
    super.destroy()
  }
}
