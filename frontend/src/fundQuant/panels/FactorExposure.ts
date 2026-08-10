/**
 * P10 策略暴露面板 — L3 研究区内的策略分析面板
 *
 * 功能：
 * 1. 策略暴露雷达图 — 当前基金的因子暴露度可视化
 * 2. 因子历史变化 — 近 1 年各因子暴露度折线图
 * 3. 同类对比 — 同类基金中各因子的百分位
 */

import * as echarts from 'echarts'
import { fundQuantApi } from '../api'
import { getChartTheme } from '../../fundQuantCharts'

interface FactorItem {
  value: number
  weight: number
  rank_pct: number
}

interface FactorExposureData {
  fund_code: string
  fund_name: string
  factors: Record<string, FactorItem>
  total_score: number
  n_funds_in_category: number
}

export class FactorExposure {
  private el: HTMLElement | null = null
  private chart: echarts.ECharts | null = null
  private historyChart: echarts.ECharts | null = null
  private currentFund: string | null = null // @ts-ignore

  init(el: HTMLElement): void {
    this.el = el
    this.el.innerHTML = this.renderHTML()
    this.bindEvents()
  }

  private renderHTML(): string {
    return `
      <div class="research-scroll" style="display:flex;flex-direction:column;gap:12px;">
        <!-- 雷达图 + 表格行 -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div>
            <div class="research-section-title">因子贡献</div>
            <div class="fx-radar-chart" style="height:220px;"></div>
          </div>
          <div>
            <div class="research-section-title">因子明细</div>
            <div class="fx-table-wrapper">
              <table class="data-table" style="width:100%;font-size:12px;">
                <thead><tr><th>因子</th><th style="text-align:right;">值</th><th style="text-align:right;">权重</th><th style="text-align:right;">同类百分位</th></tr></thead>
                <tbody class="fx-tbody"></tbody>
              </table>
            </div>
            <div style="margin-top:8px;font-size:13px;font-weight:600;color:var(--text-primary);">
              总分: <span class="fx-total-score">--</span>
            </div>
          </div>
        </div>

        <!-- 历史变化折线图 -->
        <div>
          <div class="research-section-title">历史策略暴露变化（近1年）</div>
          <div class="fx-history-chart" style="height:160px;"></div>
        </div>
      </div>`
  }

  private bindEvents(): void {
    // no interactive elements for now
  }

  show(fundCode: string): void {
    this.currentFund = fundCode
    this.loadExposure(fundCode)
  }

  private async loadExposure(fundCode: string): Promise<void> {
    if (!this.el) return
    try {
      const res = await fundQuantApi.getFactorExposure(fundCode)
      if (!res.success) {
        this.showNoData()
        return
      }
      this.renderRadar(res.data)
      this.renderTable(res.data)
      this.renderHistory(res.data)
      this.el!.querySelector('.fx-total-score')!.textContent = res.data.total_score.toFixed(2)
    } catch {
      this.showNoData()
    }
  }

  private showNoData(): void {
    const el = this.el?.querySelector('.fx-radar-chart')
    if (el) el.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);font-size:13px;">暂无因子数据<br><span style="font-size:12px;">请先在量化引擎 Tab 中注册基金因子</span></div>'
  }

  private renderRadar(data: FactorExposureData): void {
    const el = this.el?.querySelector<HTMLElement>('.fx-radar-chart')
    if (!el) return
    this.chart?.dispose()
    this.chart = echarts.init(el)

    const entries = Object.entries(data.factors)
    const indicators = entries.map(([name]) => ({
      name,
      max: 1,
    }))
    const values = entries.map(([, item]) => item.value)

    const isDark = document.body.classList.contains('dark-mode')
    const theme = getChartTheme(isDark)

    this.chart.setOption({
      ...theme,
      radar: {
        indicator: indicators,
        shape: 'polygon',
        radius: '65%',
        name: { textStyle: { fontSize: 10 } },
      },
      series: [{
        type: 'radar',
        data: [{
          value: values,
          name: data.fund_name,
          areaStyle: { color: 'rgba(59,130,246,0.2)' },
          lineStyle: { color: '#3b82f6', width: 2 },
        }],
      }],
    })
  }

  private renderTable(data: FactorExposureData): void {
    const tbody = this.el?.querySelector('.fx-tbody')
    if (!tbody) return
    tbody.innerHTML = Object.entries(data.factors)
      .sort(([, a], [, b]) => b.weight - a.weight)
      .map(([name, item]) => {
        const pct = item.rank_pct
        const color = pct >= 80 ? 'var(--danger-color)' : pct >= 60 ? 'var(--primary-color)' : pct >= 40 ? 'var(--text-secondary)' : 'var(--success-color)'
        return `<tr>
          <td style="padding:3px 6px;color:var(--text-primary);font-weight:500;">${name}</td>
          <td style="padding:3px 6px;text-align:right;font-weight:600;color:var(--text-primary);">${item.value.toFixed(2)}</td>
          <td style="padding:3px 6px;text-align:right;">${(item.weight * 100).toFixed(0)}%</td>
          <td style="padding:3px 6px;text-align:right;font-weight:600;color:${color};">${pct}%</td>
        </tr>`
      }).join('')
  }

  private renderHistory(data: FactorExposureData): void {
    const el = this.el?.querySelector<HTMLElement>('.fx-history-chart')
    if (!el) return
    this.historyChart?.dispose()
    this.historyChart = echarts.init(el)

    // 如果后端没返回历史数据，用当前值模拟
    const names = Object.keys(data.factors)
    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316']

    const isDark = document.body.classList.contains('dark-mode')
    const theme = getChartTheme(isDark)

    this.historyChart.setOption({
      ...theme,
      tooltip: { trigger: 'axis' },
      legend: { show: false },
      grid: { left: '3%', right: '4%', bottom: '10%', containLabel: true },
      xAxis: { type: 'category', data: ['当前'], axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', min: 0, max: 1, axisLabel: { fontSize: 10 } },
      series: names.map((name, i) => ({
        name,
        type: 'bar',
        data: [data.factors[name].value],
        barGap: '10%',
        itemStyle: { color: colors[i % colors.length], borderRadius: [3, 3, 0, 0] },
      })),
    })
  }

  onActivated(): void {
    this.chart?.resize()
    this.historyChart?.resize()
  }

  destroy(): void {
    this.chart?.dispose()
    this.historyChart?.dispose()
    this.el = null
  }
}
