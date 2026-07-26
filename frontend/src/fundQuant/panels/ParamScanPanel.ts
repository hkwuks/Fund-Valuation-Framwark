import * as echarts from 'echarts'
import { PanelBase } from '../layout'
import { fundQuantApi, type ParamScanResult } from '../api'
import { state } from '../state'

type ScanMode = 'single_param' | 'grid_search' | 'random_search'

const MODE_INPUTS: Record<ScanMode, string> = {
  single_param: `
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:4px 8px;font-size:11px;margin:4px 0;">
      <label>参数名</label><input class="ps-param-name" placeholder="lookback" style="font-size:11px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
      <label>参数值列表</label><input class="ps-param-values" placeholder="10, 20, 30, 60, 120" style="font-size:11px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
      <label>固定参数(JSON)</label><textarea class="ps-fixed-params" rows="2" placeholder='{"lookback": 252}' style="font-size:10px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);font-family:monospace;resize:vertical;"></textarea>
    </div>`,
  grid_search: `
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:4px 8px;font-size:11px;margin:4px 0;">
      <label>参数网格(JSON)</label><textarea class="ps-param-grid" rows="3" placeholder='{"period": [10,20,30], "threshold": [0.1, 0.2]}' style="font-size:10px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);font-family:monospace;resize:vertical;"></textarea>
    </div>`,
  random_search: `
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:4px 8px;font-size:11px;margin:4px 0;">
      <label>参数分布(JSON)</label><textarea class="ps-param-dist" rows="3" placeholder='{"period": [10, 100], "threshold": [0, 0.5]}' style="font-size:10px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);font-family:monospace;resize:vertical;"></textarea>
      <label>迭代次数</label><input class="ps-n-iter" type="number" value="50" min="10" max="500" style="font-size:11px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
    </div>`,
}

export class ParamScanPanel extends PanelBase {
  private chart: echarts.ECharts | null = null
  private currentMode: ScanMode = 'single_param'

  constructor() {
    super({ id: 'param-scan', title: '参数扫描', defaultGridPos: { x: 2, y: 5, w: 1, h: 1 } })
  }

  render(): HTMLElement {
    const el = document.createElement('div')
    el.className = 'panel-param-scan'
    el.style.display = 'flex'
    el.style.flexDirection = 'column'
    el.innerHTML = `
      <div class="panel-header">
        <h3>参数扫描</h3>
      </div>
      <div class="ps-form" style="padding:8px 12px;border-bottom:1px solid var(--border-light);">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;font-size:11px;margin-bottom:6px;">
          <select class="ps-strategy" style="padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
            <option value="">— 策略 —</option>
          </select>
          <select class="ps-fund" style="padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
            <option value="">— 基金 —</option>
          </select>
          <input type="date" class="ps-start" value="2024-01-01" style="font-size:11px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
          <input type="date" class="ps-end" value="2025-12-31" style="font-size:11px;padding:3px 6px;border:1px solid var(--border-color);border-radius:3px;background:var(--bg-primary);color:var(--text-primary);">
        </div>
        <div style="margin:6px 0;display:flex;gap:4px;font-size:11px;">
          <label><input type="radio" name="ps-mode" value="single_param" checked> 单参数</label>
          <label><input type="radio" name="ps-mode" value="grid_search"> 网格</label>
          <label><input type="radio" name="ps-mode" value="random_search"> 随机</label>
        </div>
        <div class="ps-input-area"></div>
        <button class="btn btn-sm btn-primary ps-run" style="margin-top:4px;">运行扫描</button>
      </div>
      <div class="ps-body" style="overflow-y:auto;flex:1;padding:8px 12px;">
        <div class="ps-chart" style="height:100px;margin-bottom:6px;"></div>
        <div class="ps-table-wrapper"></div>
        <div class="ps-stats" style="font-size:11px;color:var(--text-secondary);margin-top:4px;"></div>
      </div>`
    return el
  }

  protected afterMount(): void {
    this.loadStrategyOptions()
    this.loadFundOptions()
    state.on('fundPool', () => this.loadFundOptions())

    // 模式切换
    this.el?.querySelectorAll('input[name="ps-mode"]').forEach(radio => {
      radio.addEventListener('change', () => {
        const val = this.el?.querySelector('input[name="ps-mode"]:checked') as HTMLInputElement
        if (!val) return
        this.currentMode = val.value as ScanMode
        const inputArea = this.el?.querySelector('.ps-input-area')
        if (inputArea) inputArea.innerHTML = MODE_INPUTS[this.currentMode]
      })
    })
    // 默认显示单参数
    const inputArea = this.el?.querySelector('.ps-input-area')
    if (inputArea) inputArea.innerHTML = MODE_INPUTS.single_param

    this.el?.querySelector('.ps-run')?.addEventListener('click', () => this.runScan())
  }

  private loadStrategyOptions(): void {
    const sel = this.el?.querySelector<HTMLSelectElement>('.ps-strategy')
    if (!sel) return
    const pool = state.get('fundPool')
    sel.innerHTML = `<option value="">— 策略 —</option>
      ${pool.map(f => `<option value="${f.fund_code}">${f.fund_name || f.fund_code}</option>`).join('')}`
    // 从 BacktestPanel 借策略列表—直接从 fundPool 取基金作 placeholder，实际扫描靠 api
  }

  private loadFundOptions(): void {
    const sel = this.el?.querySelector<HTMLSelectElement>('.ps-fund')
    if (!sel) return
    const pool = state.get('fundPool')
    sel.innerHTML = `<option value="">— 基金 —</option>
      ${pool.map(f => `<option value="${f.fund_code}">${f.fund_name || f.fund_code}</option>`).join('')}`
  }

  private async runScan(): Promise<void> {
    const mode = this.currentMode
    const strategySel = this.el?.querySelector('.ps-strategy') as HTMLSelectElement
    const fundSel = this.el?.querySelector('.ps-fund') as HTMLSelectElement
    const start = (this.el?.querySelector('.ps-start') as HTMLInputElement)?.value
    const end = (this.el?.querySelector('.ps-end') as HTMLInputElement)?.value

    let strategyName = strategySel?.value || ''
    // 用户可能在策略下拉填了 fund code 而不是策略名，尝试取第一个
    const fundCode = fundSel?.value
    if (!fundCode) return
    // 用基金代码填充 fund_codes
    strategyName = strategyName || 'momentum'

    const req: any = {
      strategy_name: strategyName,
      fund_codes: [fundCode],
      start_date: start,
      end_date: end,
      mode,
    }

    try {
      if (mode === 'single_param') {
        req.param_name = (this.el?.querySelector('.ps-param-name') as HTMLInputElement)?.value
        const vals = (this.el?.querySelector('.ps-param-values') as HTMLInputElement)?.value
        req.param_values = vals?.split(',').map(v => isNaN(Number(v)) ? v.trim() : Number(v.trim())) || []
        const fixed = (this.el?.querySelector('.ps-fixed-params') as HTMLTextAreaElement)?.value
        if (fixed) req.fixed_params = JSON.parse(fixed)
      } else if (mode === 'grid_search') {
        req.param_grid = JSON.parse((this.el?.querySelector('.ps-param-grid') as HTMLTextAreaElement)?.value || '{}')
      } else if (mode === 'random_search') {
        req.param_dist = JSON.parse((this.el?.querySelector('.ps-param-dist') as HTMLTextAreaElement)?.value || '{}')
        req.n_iter = parseInt((this.el?.querySelector('.ps-n-iter') as HTMLInputElement)?.value || '50')
      }

      const res = await fundQuantApi.runParamScan(req)
      this.renderScanResult(res.data)
    } catch (e: any) {
      const statsEl = this.el?.querySelector('.ps-stats')
      if (statsEl) statsEl.innerHTML = `<span style="color:var(--danger-color);">扫描失败: ${e.message || 'error'}</span>`
    }
  }

  private renderScanResult(data: ParamScanResult): void {
    // 图表
    if (data.mode === 'single_param' && data.param_names.length === 1 && data.results.length) {
      this.renderLineChart(data)
    } else if (data.mode === 'grid_search' && data.param_names.length === 2 && data.results.length) {
      this.renderHeatmap(data)
    } else if (data.results.length) {
      this.renderScatter(data)
    }
    // 表格
    this.renderResultTable(data)
    // 统计
    const statsEl = this.el?.querySelector('.ps-stats')
    if (statsEl) {
      let html = `迭代: ${data.n_iterations} 次`
      if (data.sensitivity_score) {
        html += ' | ' + Object.entries(data.sensitivity_score).map(([k, v]) =>
          `${k} 敏感度: ${typeof v === 'number' ? v.toFixed(4) : v}`
        ).join(' | ')
      }
      if (data.stability_region?.length) {
        html += ` | 稳定区域: ${data.stability_region.length} 组`
      }
      statsEl.innerHTML = html
    }
  }

  private renderLineChart(data: ParamScanResult): void {
    this.chart?.dispose()
    const chartEl = this.el?.querySelector('.ps-chart') as HTMLElement
    if (!chartEl) return
    this.chart = echarts.init(chartEl)
    const paramName = data.param_names[0]
    const sorted = [...data.results].sort((a, b) => a[paramName] - b[paramName])
    if (!sorted.length) return
    const keys = Object.keys(sorted[0]).filter(k => k !== paramName)

    this.chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: keys, bottom: 0, icon: 'circle', textStyle: { fontSize: 9 } },
      grid: { left: 36, right: 6, bottom: 24, top: 6 },
      xAxis: { type: 'category', data: sorted.map(r => String(r[paramName])), axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
      series: keys.map(key => ({
        name: key, type: 'line', smooth: true,
        data: sorted.map(r => r[key]),
        symbol: 'diamond', symbolSize: 6,
      })),
    })
  }

  private renderHeatmap(data: ParamScanResult): void {
    this.chart?.dispose()
    const chartEl = this.el?.querySelector('.ps-chart') as HTMLElement
    if (!chartEl) return
    this.chart = echarts.init(chartEl)
    const [p1, p2] = data.param_names
    const vals1 = [...new Set(data.results.map(r => r[p1]))].sort((a, b) => a - b)
    const vals2 = [...new Set(data.results.map(r => r[p2]))].sort((a, b) => a - b)
    const map = new Map(data.results.map(r => [`${r[p1]}-${r[p2]}`, r.sharpe || 0]))

    const heatData: [number, number, number][] = []
    vals1.forEach((v1, i) => {
      vals2.forEach((v2, j) => {
        heatData.push([i, j, map.get(`${v1}-${v2}`) || 0])
      })
    })

    this.chart.setOption({
      tooltip: { position: 'top', formatter: (p: any) => `${p1}=${vals1[p.value[0]]}, ${p2}=${vals2[p.value[1]]}: ${p.value[2].toFixed(4)}` },
      grid: { left: 40, right: 30, top: 6, bottom: 24 },
      xAxis: { type: 'category', data: vals1.map(String), axisLabel: { fontSize: 9 }, splitArea: { show: true } },
      yAxis: { type: 'category', data: vals2.map(String), axisLabel: { fontSize: 9 }, splitArea: { show: true } },
      visualMap: { min: Math.min(...heatData.map(d => d[2])), max: Math.max(...heatData.map(d => d[2])), calculable: false, inRange: { color: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'] }, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 8, itemHeight: 80 },
      series: [{
        type: 'heatmap', data: heatData,
        label: { show: heatData.length < 30, fontSize: 9 },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } },
      }],
    })
  }

  private renderScatter(data: ParamScanResult): void {
    this.chart?.dispose()
    const chartEl = this.el?.querySelector('.ps-chart') as HTMLElement
    if (!chartEl) return
    this.chart = echarts.init(chartEl)

    this.chart.setOption({
      tooltip: { trigger: 'item', formatter: (p: any) =>
        data.param_names.map(n => `${n}=${p.data[n]}`).join(', ') + `<br/>sharpe=${p.data.sharpe?.toFixed(4)}`
      },
      grid: { left: 36, right: 6, bottom: 6, top: 6 },
      xAxis: { type: 'value', axisLabel: { fontSize: 9 } },
      yAxis: { type: 'value', axisLabel: { fontSize: 9 } },
      series: [{
        type: 'scatter', data: data.results,
        encode: { x: data.param_names[0], y: data.param_names[1] || 'sharpe' },
        symbolSize: (d: any) => Math.max(4, Math.abs(d.sharpe || 0) * 8),
      }],
    })
  }

  private renderResultTable(data: ParamScanResult): void {
    const wrapper = this.el?.querySelector('.ps-table-wrapper')
    if (!wrapper || !data.results.length) return
    const keys = Object.keys(data.results[0])
    wrapper.innerHTML = `<table style="width:100%;font-size:10px;border-collapse:collapse;">
      <thead><tr>${keys.map(k => `<th style="padding:2px 4px;text-align:right;background:var(--bg-tertiary);cursor:pointer;${k === data.param_names[0] ? 'text-align:left;' : ''}">${k}</th>`).join('')}</tr></thead>
      <tbody>${data.results.map(r => `
        <tr>${keys.map(k =>
          `<td style="padding:2px 4px;text-align:right;border-bottom:1px solid var(--border-light);${k === data.param_names[0] ? 'text-align:left;font-weight:600;' : ''}">${typeof r[k] === 'number' ? (r[k] as number).toFixed(4) : r[k]}</td>`
        ).join('')}</tr>`
      ).join('')}</tbody>
    </table>`
  }

  async refresh(): Promise<void> {
    this.loadFundOptions()
  }

  destroy(): void {
    this.chart?.dispose()
    super.destroy()
  }

  onActivated(): void {
    this.chart?.resize()
  }
}
