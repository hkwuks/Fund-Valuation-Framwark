/**
 * P9 择时研究面板 — L3 研究区内的核心面板
 *
 * 功能：
 * 1. 信号解释 — 信号产生逻辑 + 数据快照
 * 2. 参数调试 — 动态渲染策略参数滑块
 * 3. 信号预览 — 调参后内嵌图预览
 */

import * as echarts from 'echarts'
import { fundQuantApi } from '../api'
import { state, type SignalSummary } from '../state'
import { getChartTheme } from '../../fundQuantCharts'

interface ParamSchema {
  name: string
  label: string
  type: 'int' | 'float' | 'select'
  default: number | string
  min?: number
  max?: number
  step?: number
  options?: { label: string; value: string }[]
  description: string
}

export class TimingResearch {
  private el: HTMLElement | null = null
  private currentFund: string | null = null
  private currentStrategyName: string = ''
  private paramSchemas: ParamSchema[] = []
  private paramValues: Record<string, any> = {}
  private chart: echarts.ECharts | null = null
  private navCache: any[] = []

  init(el: HTMLElement): void {
    this.el = el
    this.el.innerHTML = this.renderHTML()
    this.bindEvents()
  }

  private renderHTML(): string {
    return `
      <div class="research-scroll">
        <!-- 信号解释区 -->
        <div class="research-section">
          <div class="research-section-title">信号解释</div>
          <div class="sig-explain-body" style="padding:4px 0;">
            <div class="sig-explain-loading" style="color:var(--text-tertiary);font-size:13px;">加载信号解释...</div>
          </div>
        </div>

        <div class="research-divider"></div>

        <!-- 参数调试区 -->
        <div class="research-section">
          <div class="research-section-title">参数调试</div>
          <div class="param-debug-body" style="padding:4px 0;">
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
              <select class="param-strategy-select" style="flex:1;padding:4px 8px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);"></select>
            </div>
            <div class="param-sliders"></div>
            <div style="margin-top:8px;display:flex;gap:6px;">
              <button class="btn btn-sm btn-primary param-apply-btn">应用并预览</button>
              <button class="btn btn-sm btn-ghost param-reset-btn">重置</button>
            </div>
          </div>
        </div>

        <div class="research-divider"></div>

        <!-- 信号预览区 -->
        <div class="research-section">
          <div class="research-section-title">信号预览</div>
          <div class="sig-preview-body">
            <div class="sig-preview-chart" style="height:200px;"></div>
            <div class="sig-preview-stats" style="padding:6px 0;font-size:12px;color:var(--text-secondary);"></div>
          </div>
        </div>

        <div class="research-divider"></div>

        <!-- 操作区 -->
        <div class="research-section" style="display:flex;gap:8px;padding:8px 0;">
          <button class="btn btn-sm btn-outline action-backtest">在量化引擎中回测</button>
        </div>
      </div>`
  }

  private bindEvents(): void {
    if (!this.el) return
    // 策略切换
    this.el.querySelector('.param-strategy-select')?.addEventListener('change', (e) => {
      const name = (e.target as HTMLSelectElement).value
      if (name) this.switchStrategy(name)
    })
    // 应用预览
    this.el.querySelector('.param-apply-btn')?.addEventListener('click', () => this.applyPreview())
    // 重置
    this.el.querySelector('.param-reset-btn')?.addEventListener('click', () => this.resetParams())
    // 回测跳转
    this.el.querySelector('.action-backtest')?.addEventListener('click', () => this.jumpToBacktest())
  }

  show(fundCode: string, signal?: SignalSummary | null): void {
    this.currentFund = fundCode
    this.currentStrategyName = signal?.strategy_name || ''
    if (!this.el) return

    // 加载策略列表
    this.loadStrategyList(fundCode)

    // 如果已知策略 → 直接加载该策略的参数和解释
    if (signal?.strategy_name) {
      this.selectStrategy(signal.strategy_name)
    }

    // 加载净值数据（用于预览图）
    this.loadNavData(fundCode)
  }

  private async loadStrategyList(_fundCode: string): Promise<void> {
    const select = this.el?.querySelector<HTMLSelectElement>('.param-strategy-select')
    if (!select) return
    try {
      const res = await fundQuantApi.getStrategyList()
      let strategies = (res.data || []).filter(s => s.type === 'timing')
      // 转换 param_ranges 为前端数组格式
      strategies = strategies.map(s => ({
        ...s,
        param_ranges: this.convertParamRanges(s.param_ranges, s.default_params),
      }))
      select.innerHTML = strategies.map(s =>
        `<option value="${s.name}"${s.name === this.currentStrategyName ? ' selected' : ''}>${s.display_name || s.name}</option>`
      ).join('')

      // 加载默认策略的参数 schema
      if (!this.currentStrategyName && strategies.length) {
        this.currentStrategyName = strategies[0].name
        if (strategies[0].param_ranges) {
          this.paramSchemas = strategies[0].param_ranges
          this.initParamValues()
          this.renderSliders()
          this.loadExplain()
        }
      }
    } catch {
      select.innerHTML = '<option>加载失败</option>'
    }
  }

  private async selectStrategy(name: string): Promise<void> {
    this.currentStrategyName = name
    try {
      const res = await fundQuantApi.getStrategyList()
      const s = (res.data || []).find(s => s.name === name)
      if (s?.param_ranges) {
        this.paramSchemas = this.convertParamRanges(s.param_ranges, s.default_params)
        this.initParamValues()
        this.renderSliders()
      }
    } catch { /* ignore */ }
    this.loadExplain()
  }

  private switchStrategy(name: string): void {
    this.currentStrategyName = name
    this.selectStrategy(name)
  }

  /** 后端返回的 param_ranges 是 {name: {min, max}} 对象，转为 ParamSchema[] */
  private convertParamRanges(paramRanges: any, defaultParams?: Record<string, any>): ParamSchema[] {
    if (!paramRanges || Array.isArray(paramRanges)) return paramRanges || []
    return Object.entries(paramRanges).map(([name, range]: [string, any]) => {
      const defaultValue = defaultParams?.[name]
      const type: 'int' | 'float' | 'select' =
        typeof defaultValue === 'number' ? (Number.isInteger(defaultValue) ? 'int' : 'float') : 'select'
      return {
        name,
        label: name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
        type,
        default: defaultValue ?? 0,
        min: range.min,
        max: range.max,
        step: type === 'float' ? 0.01 : type === 'int' ? 1 : undefined,
        description: range.description ?? '',
      }
    })
  }

  private initParamValues(): void {
    const saved = state.get('customParams')[this.currentStrategyName] || {}
    this.paramValues = {}
    this.paramSchemas.forEach(p => {
      this.paramValues[p.name] = saved[p.name] ?? p.default
    })
  }

  private renderSliders(): void {
    const container = this.el?.querySelector('.param-sliders')
    if (!container) return
    if (!this.paramSchemas.length) {
      container.innerHTML = '<div style="color:var(--text-tertiary);font-size:12px;">该策略无可调参数</div>'
      return
    }
    container.innerHTML = this.paramSchemas.map(p => {
      if (p.type === 'select' && p.options) {
        const opts = p.options.map(o =>
          `<option value="${o.value}"${this.paramValues[p.name] === o.value ? ' selected' : ''}>${o.label}</option>`
        ).join('')
        return this.paramRowHTML(p, `<select class="param-slider-input" data-param="${p.name}" style="width:100px;padding:2px 4px;border:1px solid var(--border-color);border-radius:4px;font-size:12px;background:var(--bg-primary);color:var(--text-primary);">${opts}</select>`)
      }
      const val = Number(this.paramValues[p.name])
      const pct = p.min != null && p.max != null && p.max !== p.min
        ? ((val - p.min) / (p.max - p.min)) * 100
        : 50
      return this.paramRowHTML(p, `
        <div style="display:flex;align-items:center;gap:8px;width:200px;">
          <input type="range" class="param-slider-input" data-param="${p.name}"
            min="${p.min ?? 0}" max="${p.max ?? 100}" step="${p.step ?? 1}" value="${val}"
            style="flex:1;height:4px;">
          <span class="param-slider-val" style="min-width:40px;text-align:right;font-size:12px;font-weight:600;color:var(--text-primary);">${p.type === 'float' ? Number(val).toFixed(2) : val}</span>
        </div>
      `, pct)
    }).join('')

    // 绑定滑块事件
    container.querySelectorAll('.param-slider-input').forEach(input => {
      input.addEventListener('input', (e) => {
        const el = e.target as HTMLInputElement
        const name = el.dataset.param
        if (!name) return
        const schema = this.paramSchemas.find(p => p.name === name)
        const val = schema?.type === 'float' ? parseFloat(el.value) : parseInt(el.value, 10)
        this.paramValues[name] = val
        // 更新显示值
        const valSpan = el.parentElement?.querySelector('.param-slider-val')
        if (valSpan) valSpan.textContent = String(schema?.type === 'float' ? Number(val).toFixed(2) : val)
      })
    })
  }

  private paramRowHTML(param: ParamSchema, controlHTML: string, _pct?: number): string {
    return `<div class="param-row" style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--border-light);">
      <div style="width:80px;flex-shrink:0;">
        <div style="font-size:12px;font-weight:600;color:var(--text-primary);">${param.label}</div>
        <div style="font-size:10px;color:var(--text-tertiary);">${param.name}</div>
      </div>
      ${controlHTML}
      <div style="flex:1;font-size:11px;color:var(--text-tertiary);">${param.description}</div>
    </div>`
  }

  private async loadExplain(): Promise<void> {
    const el = this.el?.querySelector('.sig-explain-body')
    if (!el || !this.currentFund || !this.currentStrategyName) return
    el.innerHTML = '<div style="color:var(--text-tertiary);font-size:13px;">加载信号解释...</div>'
    try {
      const res = await fundQuantApi.explainTiming(this.currentFund, this.currentStrategyName, this.paramValues)
      if (!res.success) { el.innerHTML = '<div style="color:var(--text-tertiary);">暂无解释数据</div>'; return }
      const d = res.data
      const vals = Object.entries(d.key_values || {}).map(([, v]) => {
        const fmt = v.format === 'pct' ? `${(v.value * 100).toFixed(2)}%` : v.format === 'number' ? v.value.toFixed(4) : v.value
        return `<span style="display:inline-flex;align-items:center;gap:4px;background:var(--bg-tertiary);padding:2px 8px;border-radius:4px;font-size:12px;">
          <span style="color:var(--text-tertiary);">${v.label}:</span>
          <span style="font-weight:600;color:${v.highlight === 'positive' ? 'var(--danger-color)' : v.highlight === 'negative' ? 'var(--success-color)' : 'var(--text-primary)'}">${fmt}</span>
        </span>`
      }).join(' ')
      el.innerHTML = `
        <div style="margin-bottom:6px;">
          <span style="font-size:12px;font-weight:600;color:var(--text-primary);">${d.strategy_display_name || d.strategy_name}</span>
          <span style="font-size:11px;color:var(--text-tertiary);margin-left:6px;">公式: ${d.formula_description}</span>
        </div>
        <div style="font-size:13px;color:var(--text-primary);padding:6px 8px;background:var(--bg-tertiary);border-radius:4px;margin-bottom:6px;">${d.verdict}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;">${vals}</div>`
    } catch {
      el.innerHTML = '<div style="color:var(--text-tertiary);">加载解释失败</div>'
    }
  }

  private async loadNavData(fundCode: string): Promise<void> {
    if (!fundCode || this.navCache.length) return
    try {
      const res = await fundQuantApi.getNav(fundCode)
      this.navCache = res.data?.nav_history || []
    } catch { /* ignore */ }
  }

  private async applyPreview(): Promise<void> {
    if (!this.currentFund || !this.currentStrategyName) return
    const statsEl = this.el?.querySelector('.sig-preview-stats')
    statsEl!.innerHTML = '计算中...'
    try {
      // 用新参数评估信号
      const res = await fundQuantApi.evaluateTiming(this.currentFund, this.paramValues, this.currentStrategyName)
      const signals = (res.data?.signals || [])
      const buyCount = signals.filter(s => s.direction === 'buy').length
      const sellCount = signals.filter(s => s.direction === 'sell').length
      const holdCount = signals.filter(s => s.direction === 'hold').length

      // 渲染预览图
      this.renderPreviewChart(signals.map(s => ({
        date: (s.timestamp || '').slice(0, 10),
        nav: 0,
        direction: s.direction,
      })))

      if (statsEl) {
        statsEl.innerHTML = `预览: 买入 ${buyCount}  |  卖出 ${sellCount}  |  持有 ${holdCount}  |  总信号 ${signals.length}`
      }

      // 保存自定义参数
      state.set('customParams', {
        ...state.get('customParams'),
        [this.currentStrategyName]: { ...this.paramValues },
      })
    } catch {
      if (statsEl) statsEl.innerHTML = '预览计算失败，请稍后重试'
    }
  }

  private renderPreviewChart(signals: { date: string; nav: number; direction: string }[]): void {
    const chartEl = this.el?.querySelector<HTMLElement>('.sig-preview-chart')
    if (!chartEl) return
    this.chart?.dispose()
    this.chart = echarts.init(chartEl)

    const dates = this.navCache.map((d: any) => (d.date || '').slice(0, 10))
    const navValues = this.navCache.map((d: any) => d.nav || d.adjusted_nav || 0)

    // 将信号映射到净值
    const signalMap = new Map(signals.map(s => [s.date, s]))
    const buyData: Array<[string, number]> = []
    const sellData: Array<[string, number]> = []
    dates.forEach((date: string, i: number) => {
      const sig = signalMap.get(date)
      if (sig?.direction === 'buy') buyData.push([date, navValues[i]])
      else if (sig?.direction === 'sell') sellData.push([date, navValues[i]])
    })

    const isDark = document.body.classList.contains('dark-mode')
    const theme = getChartTheme(isDark)

    this.chart.setOption({
      ...theme,
      tooltip: { trigger: 'axis' },
      grid: { left: '3%', right: '4%', bottom: '10%', containLabel: true },
      xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, rotate: 30 } },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          name: '净值', type: 'line', data: navValues,
          smooth: true, lineStyle: { width: 1.5, color: '#3b82f6' },
          symbol: 'none',
        },
        {
          name: '买入信号', type: 'scatter',
          data: buyData,
          symbol: 'circle', symbolSize: 8,
          itemStyle: { color: '#ef4444' },
        },
        {
          name: '卖出信号', type: 'scatter',
          data: sellData,
          symbol: 'circle', symbolSize: 8,
          itemStyle: { color: '#10b981' },
        },
      ],
    })
  }

  private resetParams(): void {
    this.initParamValues()
    this.renderSliders()
  }

  private jumpToBacktest(): void {
    // 切换到量化引擎 Tab
    const btn = document.querySelector<HTMLElement>('.tab-button[data-tab="quant-engine"]')
    btn?.click()
  }

  onActivated(): void {
    this.chart?.resize()
  }

  destroy(): void {
    this.chart?.dispose()
    this.chart = null
    this.el = null
  }
}
