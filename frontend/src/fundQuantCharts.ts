/** FundQuant 图表引擎 — ECharts 封装 */

import * as echarts from 'echarts'

// ── 图表主题 ──

/** 黄金量化风格：浅色/深色通用图层面板 */
export function getChartTheme(isDark: boolean) {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: isDark ? '#f1f5f9' : '#334155' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'cross' as const },
      backgroundColor: isDark ? '#273549' : '#ffffff',
      borderColor: isDark ? '#475569' : '#e2e8f0',
      textStyle: { color: isDark ? '#f1f5f9' : '#334155' },
    },
    legend: {
      textStyle: { color: isDark ? '#cbd5e1' : '#475569', fontSize: 11 },
      pageTextStyle: { color: isDark ? '#94a3b8' : '#64748b' },
    },
  }
}

/** 深色模式颜色映射表 — 系列颜色 */
export const DARK_SERIES = {
  nav: '#60a5fa',       // 净值亮蓝
  navArea: 'rgba(96,165,250,0.2)',
  benchmark: '#475569', // 基准灰
  drawdown: '#fbbf24',  // 回撤亮琥珀
  drawdownArea: 'rgba(251,191,36,0.1)',
  buy: '#ef4444',       // 买入红
  sell: '#10b981',      // 卖出绿
}

/** 浅色模式颜色映射表 */
export const LIGHT_SERIES = {
  nav: '#3b82f6',
  navArea: 'rgba(59,130,246,0.15)',
  benchmark: '#94a3b8',
  drawdown: '#f59e0b',
  drawdownArea: 'rgba(245,158,11,0.1)',
  buy: '#ef4444',
  sell: '#10b981',
}

// ── 择时: 净值曲线 + 买卖信号 ──

export function renderTimingChart(
  container: HTMLElement, navData: { date: string; nav: number }[],
  buySignals?: { date: string; nav: number }[],
  sellSignals?: { date: string; nav: number }[],
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)
  const C = isDark ? DARK_SERIES : LIGHT_SERIES
  const axColor = isDark ? '#94a3b8' : '#64748b'
  const axLineColor = isDark ? '#475569' : '#cbd5e1'
  const gridColor = isDark ? '#334155' : '#e2e8f0'

  const option: echarts.EChartsOption = {
    ...theme,
    title: { text: '净值走势', left: 'center', textStyle: { fontSize: 14, color: isDark ? '#f1f5f9' : '#334155' } },
    xAxis: {
      type: 'category', data: navData.map(d => d.date),
      axisLabel: { rotate: 45, color: axColor }, axisLine: { lineStyle: { color: axLineColor } },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
    },
    yAxis: {
      type: 'value', scale: true,
      axisLabel: { color: axColor }, splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
    },
    series: [
      {
        name: '净值',
        type: 'line',
        data: navData.map(d => d.nav),
        smooth: true,
        lineStyle: { width: 2, color: C.nav },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: C.navArea },
          { offset: 1, color: isDark ? 'rgba(96,165,250,0)' : 'rgba(59,130,246,0)' },
        ]) },
      },
      ...(buySignals?.length ? [{
        name: '买入信号', type: 'scatter' as const,
        data: buySignals.map(d => [d.date, d.nav]),
        symbolSize: 12, itemStyle: { color: C.buy },
      }] : []),
      ...(sellSignals?.length ? [{
        name: '卖出信号', type: 'scatter' as const,
        data: sellSignals.map(d => [d.date, d.nav]),
        symbolSize: 12, itemStyle: { color: C.sell },
      }] : []),
    ],
    tooltip: { ...theme.tooltip, trigger: 'axis' },
    legend: { ...theme.legend, bottom: 0 },
    grid: { ...theme.grid, bottom: '15%' },
  }

  chart.setOption(option)
  return chart
}

// ── 选基: 7因子雷达图 ──

export function renderRadarChart(
  container: HTMLElement, indicators: { name: string; value: number }[],
  title?: string,
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)

  const option: echarts.EChartsOption = {
    ...theme,
    title: title ? { text: title, left: 'center', textStyle: { fontSize: 14 } } : undefined,
    radar: {
      indicator: indicators.map(i => ({ name: i.name, max: 1 })),
      shape: 'polygon',
      splitArea: { areaStyle: { color: isDark
        ? ['rgba(96,165,250,0.05)', 'rgba(96,165,250,0.1)']
        : ['rgba(59,130,246,0.02)', 'rgba(59,130,246,0.05)'] } },
    },
    series: [{
      type: 'radar', data: [{ value: indicators.map(i => i.value), name: '评分' }],
      areaStyle: { color: isDark ? 'rgba(96,165,250,0.3)' : 'rgba(59,130,246,0.2)' },
      lineStyle: { color: isDark ? '#60a5fa' : '#3b82f6', width: 2 },
      itemStyle: { color: isDark ? '#60a5fa' : '#3b82f6' },
    }],
    tooltip: { trigger: 'item' },
  }

  chart.setOption(option)
  return chart
}

// ── 配置: 饼图 ──

export function renderPieChart(
  container: HTMLElement, data: { name: string; value: number }[],
  title?: string,
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)

  const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316']

  const option: echarts.EChartsOption = {
    ...theme,
    title: title ? { text: title, left: 'center', textStyle: { fontSize: 14 } } : undefined,
    tooltip: { trigger: 'item', formatter: '{b}: {c}% ({d}%)' },
    series: [{
      type: 'pie', radius: ['30%', '60%'], center: ['50%', '55%'],
      data: data.map((d, i) => ({ ...d, itemStyle: { color: colors[i % colors.length] } })),
      label: { formatter: '{b}\n{d}%', color: isDark ? '#e2e8f0' : '#334155' },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
    legend: { ...theme.legend, bottom: 0, type: 'scroll' },
    grid: { ...theme.grid, bottom: '20%' },
  }

  chart.setOption(option)
  return chart
}

// ── 回测: 权益曲线 + 回撤 (双轴) ──

export function renderBacktestChart(
  container: HTMLElement,
  equity: { date: string; value: number }[],
  drawdown: { date: string; value: number }[],
  benchmark?: { date: string; value: number }[],
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)
  const C = isDark ? DARK_SERIES : LIGHT_SERIES
  const axColor = isDark ? '#94a3b8' : '#64748b'
  const gridColor = isDark ? '#334155' : '#e2e8f0'

  const dates = equity.map(d => d.date)
  const series: echarts.EChartsOption['series'] = [
    {
      name: '权益曲线', type: 'line', data: equity.map(d => d.value),
      smooth: true, lineStyle: { width: 2, color: C.nav },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: C.navArea }, { offset: 1, color: isDark ? 'rgba(96,165,250,0)' : 'rgba(59,130,246,0)' }]) },
    },
    ...(benchmark ? [{
      name: '基准', type: 'line' as const, data: benchmark.map(d => d.value),
      smooth: true, lineStyle: { width: 1.5, color: C.benchmark, type: 'dashed' as const },
    }] : []),
    {
      name: '回撤', type: 'line' as const, data: drawdown.map(d => d.value),
      smooth: true, yAxisIndex: 1,
      lineStyle: { width: 1.5, color: C.drawdown },
      areaStyle: { color: C.drawdownArea },
    },
  ]

  const option: echarts.EChartsOption = {
    ...theme,
    title: { text: '回测业绩', left: 'center', textStyle: { fontSize: 14, color: isDark ? '#f1f5f9' : '#334155' } },
    xAxis: {
      type: 'category', data: dates, axisLabel: { rotate: 45, color: axColor },
      axisLine: { lineStyle: { color: isDark ? '#475569' : '#cbd5e1' } },
      splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
    },
    yAxis: [
      { type: 'value', scale: true, splitLine: { lineStyle: { color: gridColor } }, axisLabel: { color: axColor } },
      { type: 'value', scale: true, splitLine: { show: false }, min: -0.3, max: 0.05,
        axisLabel: { formatter: '{value}%', color: axColor } },
    ],
    series,
    tooltip: { ...theme.tooltip, trigger: 'axis' },
    legend: { ...theme.legend, bottom: 0 },
    grid: { ...theme.grid, bottom: '15%' },
  }

  chart.setOption(option)
  return chart
}

// ── 风险: VaR分布 + CVaR标记 ──

export function renderRiskChart(
  container: HTMLElement, returnDist: number[],
  var95: number, cvar95: number,
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)

  const minR = Math.min(...returnDist)
  const maxR = Math.max(...returnDist)
  // histogram bins
  const bins = 30
  const binW = (maxR - minR) / bins || 0.01
  const hist: { key: string; count: number }[] = []
  for (let i = 0; i < bins; i++) {
    const start = minR + i * binW
    const end = start + binW
    const count = returnDist.filter(r => r >= start && r < end).length
    hist.push({ key: `${(start * 100).toFixed(1)}~${(end * 100).toFixed(1)}%`, count })
  }

  const option: echarts.EChartsOption = {
    ...theme,
    title: { text: '收益率分布', left: 'center', textStyle: { fontSize: 14 } },
    xAxis: { type: 'category', data: hist.map(h => h.key), axisLabel: { rotate: 45, fontSize: 9 } },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar', data: hist.map(h => h.count),
        itemStyle: { color: isDark ? '#60a5fa' : '#3b82f6' },
      },
      {
        type: 'line', name: `VaR(95%): ${(var95 * 100).toFixed(2)}%`,
        data: [], markLine: {
          data: [{ xAxis: hist.findIndex(h => parseFloat(h.key) >= var95 * 100) || 0,
            label: { formatter: `VaR 95%: ${(var95 * 100).toFixed(2)}%` } }],
          lineStyle: { color: '#f59e0b', width: 2 },
        },
      },
      {
        type: 'line', name: `CVaR(95%): ${(cvar95 * 100).toFixed(2)}%`,
        data: [], markLine: {
          data: [{ xAxis: hist.findIndex(h => parseFloat(h.key) >= cvar95 * 100) || 0,
            label: { formatter: `CVaR: ${(cvar95 * 100).toFixed(2)}%` } }],
          lineStyle: { color: '#ef4444', width: 2 },
        },
      },
    ],
    tooltip: { trigger: 'axis' },
    grid: { ...theme.grid, bottom: '18%' },
  }

  chart.setOption(option)
  return chart
}

// ── 信号时间线 ──

export function renderSignalTimeline(
  container: HTMLElement, signals: { time: string; name: string; type: string; confidence: number }[],
) {
  const isDark = document.body.classList.contains('dark-mode')
  const chart = echarts.init(container, undefined, { renderer: 'canvas' })
  const theme = getChartTheme(isDark)

  const colorMap: Record<string, string> = {
    buy: '#10b981', sell: '#ef4444', hold: '#94a3b8', rebalance: '#f59e0b',
  }

  const option: echarts.EChartsOption = {
    ...theme,
    title: { text: '信号时间线', left: 'center', textStyle: { fontSize: 14 } },
    xAxis: { type: 'category', data: signals.map(s => s.time), axisLabel: { rotate: 45 } },
    yAxis: { type: 'value', min: 0, max: 1.1, axisLabel: { formatter: '{value}' } },
    series: [{
      type: 'scatter',
      data: signals.map(s => ({
        value: [s.time, s.confidence],
        symbolSize: 14 + s.confidence * 8,
        itemStyle: { color: colorMap[s.type] || '#94a3b8' },
      })),
      label: {
        show: true, position: 'top', formatter: (p: any) => p.data.name || '',
        color: isDark ? '#e2e8f0' : '#334155', fontSize: 10,
      },
    }],
    tooltip: {
      formatter: (p: any) => `${p.name}<br/>方向: ${p.data.type}<br/>置信度: ${(p.data.confidence * 100).toFixed(0)}%`,
    },
    grid: { ...theme.grid, bottom: '18%' },
  }

  chart.setOption(option)
  return chart
}
