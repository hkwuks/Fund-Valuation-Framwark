import { api } from './api'
import { toast } from './toast'
import {
  createChart, ColorType,
  CandlestickSeries, LineSeries, HistogramSeries,
  type IChartApi, type ISeriesApi, type CandlestickData, type LineData, type HistogramData,
  type Time,
} from 'lightweight-charts'

// ===== 策略标签 =====
const STRATEGY_LABELS: Record<string, { name: string; icon: string }> = {
  trend_following: { name: '趋势跟踪', icon: '📈' },
  mean_reversion: { name: '均值回归', icon: '🔄' },
  ml_predictor: { name: 'ML预测', icon: '🤖' },
  rl_ppo: { name: 'RL强化学习', icon: '🧠' },
}

const DIR_LABEL: Record<string, string> = {
  long: '做多', short: '做空', close_long: '平多', close_short: '平空',
}

const PERIOD_LABELS: Record<string, string> = {
  'd': '日线', '60': '60分钟', '30': '30分钟', '15': '15分钟', '5': '5分钟', '1': '1分钟',
}

// K线周期列表
const PERIODS = [
  { value: 'd', label: '日线' },
  { value: '60', label: '60分钟' },
  { value: '30', label: '30分钟' },
  { value: '15', label: '15分钟' },
  { value: '5', label: '5分钟' },
  { value: '1', label: '1分钟' },
]


function isDark(): boolean {
  return document.body.classList.contains('dark-mode') ||
    window.matchMedia('(prefers-color-scheme: dark)').matches
}

function formatPrice(v: number | null | undefined): string {
  if (v == null) return '--'
  return v.toFixed(2)
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return '--'
  const s = v >= 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function fmtNum(v: number, decimals?: number): string {
  if (v == null || isNaN(v)) return '--'
  if (decimals != null) return v.toFixed(decimals)
  if (Math.abs(v) >= 1000000) return (v / 1000000).toFixed(1) + 'M'
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + 'K'
  return v.toFixed(2)
}

// ===== 主类 =====
export class GoldTradingUI {
  private container: HTMLDivElement | null = null
  private chart: IChartApi | null = null
  private candlestickSeries: ISeriesApi<'Candlestick'> | null = null
  private volumeSeries: ISeriesApi<'Histogram'> | null = null
  private maSeries: Record<string, ISeriesApi<'Line'> | null> = {}
  private marketTimer: ReturnType<typeof setInterval> | null = null
  private chartTimer: ReturnType<typeof setInterval> | null = null
  private currentPeriod = 'd'
  private currentBars: any[] = []
  private strategies: any[] = []
  private isWorking = false
  private signalTimer: ReturnType<typeof setInterval> | null = null
  private ctpTimer: ReturnType<typeof setInterval> | null = null
  private riskTimer: ReturnType<typeof setInterval> | null = null

  init(container: HTMLDivElement) {
    this.container = container
    this.render()
    this.bindEvents()
    this.loadStrategies()
    this.loadMarketData()
    this.loadStrategyComparison()

    // 加载最近信号
    this.loadSignals()

    // 加载风险监控
    this.loadRiskOverview()

    // 市场数据 + K线 轮询刷新
    this.marketTimer = setInterval(() => this.loadMarketData(), 30000)
    this.chartTimer = setInterval(() => {
      if (this.chart) this.loadKlineData()
    }, 60000)
    this.riskTimer = setInterval(() => this.loadRiskOverview(), 120000)
    new MutationObserver(() => this.onThemeChange()).observe(document.body, { attributes: true, attributeFilter: ['class'] })
    window.addEventListener('resize', () => this.onResize())

    // CTP 定时刷新
    this.ctpTimer = setInterval(() => this.loadCtpData(), 10000)
    this.loadCtpData()
    const savedInterval = localStorage.getItem('gold_signal_auto_interval')
    if (savedInterval) {
      const sel = document.getElementById('sig-auto-select') as HTMLSelectElement
      if (sel) { sel.value = savedInterval }
    }
    this.restartSignalTimer()
  }

  /** 当tab切换到此页面时由 main.ts 调用 */
  onActivated() {
    // requestAnimationFrame 确保 display:block 后 DOM 已完成布局
    requestAnimationFrame(() => {
      if (!this.chart) {
        this.loadKlineData()
      }
    })
  }

  private onResize() {
    if (this.chart) {
      const el = document.getElementById('kline-chart')
      if (el) {
        this.chart.applyOptions({ width: el.clientWidth, height: el.clientHeight })
      }
    }
  }

  /** 重建chart前清理所有旧引用 */
  private destroyChart() {
    if (this.chart) {
      this.chart.remove()
    }
    this.chart = null
    this.candlestickSeries = null
    this.volumeSeries = null
    this.maSeries = {}
  }

  destroy() {
    if (this.marketTimer) clearInterval(this.marketTimer)
    if (this.chartTimer) clearInterval(this.chartTimer)
    if (this.signalTimer) clearInterval(this.signalTimer)
    if (this.ctpTimer) clearInterval(this.ctpTimer)
    if (this.riskTimer) clearInterval(this.riskTimer)
    this.destroyChart()
  }

  // ===== 渲染 =====
  private render() {
    if (!this.container) return

    this.container.innerHTML = `
      <div class="quant-page">
        <!-- 实时数据栏 -->
        <div class="quant-ticker" id="quant-ticker">
          <div style="display:flex;align-items:center;gap:0;overflow-x:auto;flex:1">
            <div class="ticker-item ticker-main">
              <div class="ticker-label">AU0 主力</div>
              <div class="ticker-price" id="ticker-price">--</div>
              <div class="ticker-change" id="ticker-change">--</div>
            </div>
            <div class="ticker-separator"></div>
            <div class="ticker-item">
              <div class="ticker-label">开盘</div>
              <div class="ticker-value" id="ticker-open">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">最高</div>
              <div class="ticker-value" id="ticker-high">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">最低</div>
              <div class="ticker-value" id="ticker-low">--</div>
            </div>
            <div class="ticker-separator"></div>
            <div class="ticker-item">
              <div class="ticker-label">20日最高</div>
              <div class="ticker-value" id="ticker-high20">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">20日最低</div>
              <div class="ticker-value" id="ticker-low20">--</div>
            </div>
            <div class="ticker-separator"></div>
            <div class="ticker-item">
              <div class="ticker-label">成交量</div>
              <div class="ticker-value" id="ticker-vol">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">量比(5/20)</div>
              <div class="ticker-value" id="ticker-volratio">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">RSI(14)</div>
              <div class="ticker-value" id="ticker-rsi">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">ATR(14)</div>
              <div class="ticker-value" id="ticker-atr">--</div>
            </div>
            <div class="ticker-separator"></div>
            <div class="ticker-item">
              <div class="ticker-label">美元指数</div>
              <div class="ticker-value" id="macro-dxy">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">VIX恐慌</div>
              <div class="ticker-value" id="macro-vix">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">美债10Y</div>
              <div class="ticker-value" id="macro-us10y">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">TIPS利率</div>
              <div class="ticker-value" id="macro-tips">--</div>
            </div>
            <div class="ticker-item">
              <div class="ticker-label">通胀预期</div>
              <div class="ticker-value" id="macro-breakeven">--</div>
            </div>
          </div>
          <div style="text-align:right;font-size:12px;color:var(--text-tertiary);padding-top:4px" id="ticker-time">--</div>
        </div>

        <!-- K线图 -->
        <div class="quant-chart-section">
          <div class="chart-toolbar">
            <div class="chart-symbol-label">AU0 沪金主力</div>
            <div class="chart-periods" id="chart-periods">
              ${PERIODS.map(p => `<button class="chart-period-btn${p.value === 'd' ? ' active' : ''}" data-period="${p.value}">${p.label}</button>`).join('')}
            </div>
            <div class="chart-indicators">
              <label class="indicator-toggle"><input type="checkbox" id="toggle-ma5" checked /> MA5</label>
              <label class="indicator-toggle"><input type="checkbox" id="toggle-ma10" checked /> MA10</label>
              <label class="indicator-toggle"><input type="checkbox" id="toggle-ma20" checked /> MA20</label>
              <label class="indicator-toggle"><input type="checkbox" id="toggle-ma60" /> MA60</label>
              <label class="indicator-toggle"><input type="checkbox" id="toggle-volume" checked /> 成交量</label>
            </div>
            <div class="chart-toolbar-right">
              <span class="chart-update-time" id="chart-update-time"></span>
              <button class="btn btn-ghost btn-sm" id="chart-refresh-btn" title="刷新K线">🔄</button>
            </div>
          </div>
          <div class="chart-container" id="kline-chart"></div>
        </div>

        <!-- K线技术解读 -->
        <div class="quant-analysis" id="kline-analysis">
          <div class="analysis-header">
            <h3>📊 K线技术解读</h3>
            <span class="analysis-period-label" id="analysis-period-label">日线</span>
          </div>
          <div class="analysis-body" id="analysis-body">
            <div class="analysis-loading">加载分析中...</div>
          </div>
        </div>

        <!-- 策略对比（当前市场环境） -->
        <div class="quant-section">
          <div class="section-title-bar">
            <h3>⚔️ 策略对比 — 当前市场适配度</h3>
            <span class="regime-badge" id="regime-badge">--</span>
          </div>
          <div class="strategy-compare" id="strategy-compare">
            <div class="analysis-loading">加载中...</div>
          </div>
        </div>

        <!-- 信号 + 控制面板 -->
        <div class="quant-panels">
          <!-- 左侧：交易信号区 + 风控 -->
          <div class="quant-panel">
            <div class="panel-header">
              <h3>💡 交易信号生成</h3>
            </div>
            <div class="panel-body">
              <div class="form-row">
                <select id="sig-strategy-select">
                  <option value="trend_following">趋势跟踪</option>
                  <option value="mean_reversion">均值回归</option>
                  <option value="ml_predictor">ML预测</option>
                  <option value="rl_ppo">RL强化学习</option>
                </select>
                <button class="btn btn-primary btn-sm" id="sig-generate-btn">生成信号</button>
                <select id="sig-auto-select" style="width:90px">
                  <option value="0">⏱ 关闭</option>
                  <option value="60">⏱ 1分钟</option>
                  <option value="300">⏱ 5分钟</option>
                  <option value="600">⏱ 10分钟</option>
                  <option value="1800">⏱ 30分钟</option>
                  <option value="3600">⏱ 1小时</option>
                </select>
                <span class="auto-indicator" id="sig-auto-indicator" style="display:none;font-size:10px;color:#10b981;white-space:nowrap">●</span>
              </div>
              <div id="sig-result" class="panel-result"></div>
            </div>

            <div class="panel-header" style="margin-top:16px;">
              <h3>📡 最近信号</h3>
            </div>
            <div class="panel-body" id="signals-panel">
              <div class="empty-text">暂无信号</div>
            </div>

            <div class="panel-header" style="margin-top:16px;">
              <h3>🛡️ 风控状态</h3>
              <button class="btn btn-secondary btn-sm btn-ghost" id="risk-btn">查看</button>
            </div>
            <div class="panel-body" id="risk-panel"></div>
          </div>

          <!-- 右侧：回测 + 对比 -->
          <div class="quant-panel">
            <div class="panel-header">
              <h3>🔬 策略回测</h3>
            </div>
            <div class="panel-body">
              <div class="form-row">
                <select id="bt-strategy-select">
                  <option value="trend_following">趋势跟踪</option>
                  <option value="mean_reversion">均值回归</option>
                  <option value="ml_predictor">ML预测</option>
                  <option value="rl_ppo">RL强化学习</option>
                </select>
                <input type="date" id="bt-start-date" value="2025-01-01" style="width:120px" />
                <input type="date" id="bt-end-date" value="2026-06-26" style="width:120px" />
                <input type="number" id="bt-capital" value="1000000" min="100000" step="100000" style="width:100px" placeholder="资金" />
                <button class="btn btn-primary btn-sm" id="bt-run-btn">运行回测</button>
              </div>
              <div id="bt-params-container" class="form-row hidden" style="margin-top:8px;">
                <div id="bt-params-row"></div>
              </div>
              <div id="bt-result" class="panel-result"></div>
            </div>

            <div class="panel-header" style="margin-top:16px;">
              <h3>⚖️ 多策略对比</h3>
              <button class="btn btn-secondary btn-sm" id="cmp-run-btn">运行对比</button>
            </div>
            <div class="panel-body">
              <div id="cmp-result" class="panel-result"></div>
            </div>
          </div>
        </div>

        <!-- 高级分析 -->
        <div class="quant-section">
          <div class="section-title-bar">
            <h3>📐 高级分析</h3>
          </div>
          <div class="quant-advanced">
            <div class="advanced-block">
              <h4>参数敏感性分析</h4>
              <div class="form-row">
                <select id="sens-strategy-select">
                  <option value="trend_following">趋势跟踪</option>
                  <option value="mean_reversion">均值回归</option>
                </select>
                <button class="btn btn-secondary btn-sm" id="sens-run-btn">运行</button>
              </div>
              <div id="sens-result" class="panel-result"></div>
            </div>
            <div class="advanced-block">
              <h4>In/Out样本验证</h4>
              <div class="form-row">
                <select id="val-strategy-select">
                  <option value="trend_following">趋势跟踪</option>
                  <option value="mean_reversion">均值回归</option>
                </select>
                <button class="btn btn-secondary btn-sm" id="val-run-btn">运行</button>
              </div>
              <div id="val-result" class="panel-result"></div>
            </div>
          </div>
        </div>

        <!-- RL强化学习面板 -->
        <div class="quant-section">
          <div class="section-title-bar">
            <h3>🧠 RL强化学习 — PPO交易Agent</h3>
            <div style="display:flex;align-items:center;gap:8px">
              <span class="rl-status" id="rl-status">⚪ 未训练</span>
              <button class="btn btn-primary btn-sm" id="rl-train-btn">训练模型</button>
              <button class="btn btn-secondary btn-sm" id="rl-signal-btn">生成信号</button>
              <button class="btn btn-ghost btn-xs" id="rl-refresh-btn" title="刷新状态">🔄</button>
            </div>
          </div>
          <div class="rl-panel" id="rl-panel">
            <div class="rl-config">
              <div class="rl-config-row">
                <label>迭代次数</label>
                <input type="number" id="rl-iterations" value="50" min="10" max="500" style="width:70px" />
                <label>每轮步数</label>
                <input type="number" id="rl-steps" value="1024" min="256" max="4096" style="width:80px" />
                <span class="rl-progress" id="rl-progress"></span>
              </div>
            </div>
            <div class="rl-grid" id="rl-grid">
              <div class="rl-card" id="rl-card-metrics">
                <div class="rl-card-title">📊 绩效指标</div>
                <div class="rl-card-body"><div class="empty-text">请先训练模型</div></div>
              </div>
              <div class="rl-card" id="rl-card-signal">
                <div class="rl-card-title">💡 当前信号</div>
                <div class="rl-card-body"><div class="empty-text">暂无信号</div></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 风险监控面板 -->
        <div class="quant-section">
          <div class="section-title-bar">
            <h3>🛡️ 风险监控面板</h3>
            <div style="display:flex;align-items:center;gap:8px">
              <button class="btn btn-ghost btn-xs" id="risk-overview-btn" title="刷新风险概览">🔄</button>
            </div>
          </div>
          <div class="risk-monitor-grid" id="risk-monitor-grid">
            <div class="rm-card" id="rm-overview">
              <div class="rm-card-title">📊 风控概览</div>
              <div class="rm-card-body"><div class="empty-text">加载中...</div></div>
            </div>
            <div class="rm-card" id="rm-regime">
              <div class="rm-card-title">🌤 市场状态</div>
              <div class="rm-card-body"><div class="empty-text">加载中...</div></div>
            </div>
            <div class="rm-card" id="rm-overfitting">
              <div class="rm-card-title">⚡ 过拟合检测</div>
              <div class="rm-card-body"><div class="empty-text">加载中...</div></div>
            </div>
            <div class="rm-card" id="rm-decay">
              <div class="rm-card-title">📉 策略退化</div>
              <div class="rm-card-body"><div class="empty-text">加载中...</div></div>
            </div>
          </div>
        </div>

        <!-- 模拟交易面板 -->
        <div class="quant-section">
          <div class="section-title-bar">
            <h3>🖥️ 模拟交易</h3>
            <div style="display:flex;align-items:center;gap:8px">
              <select id="trading-mode-select" style="font-size:12px;padding:3px 8px;border:1px solid var(--border-color);border-radius:6px;background:var(--bg-primary);color:var(--text-primary)">
                <option value="simnow">SimNow</option>
                <option value="openctp">openctp TTS</option>
              </select>
              <button class="btn btn-ghost btn-xs" id="trading-mode-switch" style="font-size:11px">切换</button>
              <span class="ctp-badge" id="ctp-badge">⏳ 连接中...</span>
            </div>
          </div>
          <div class="ctp-grid" id="ctp-grid">
            <div class="ctp-card">
              <div class="ctp-card-title">💰 资金账户</div>
              <div class="ctp-card-body" id="ctp-account">
                <div class="empty-text">加载中...</div>
              </div>
            </div>
            <div class="ctp-card">
              <div class="ctp-card-title">📦 实时持仓</div>
              <div class="ctp-card-body" id="ctp-positions">
                <div class="empty-text">加载中...</div>
              </div>
            </div>
            <div class="ctp-card">
              <div class="ctp-card-title">📋 今日委托</div>
              <div class="ctp-card-body" id="ctp-orders">
                <div class="empty-text">加载中...</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `
  }

  // ===== 事件绑定 =====
  private bindEvents() {
    // K线周期切换
    document.querySelectorAll('.chart-period-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const period = (btn as HTMLElement).dataset.period!
        if (period === this.currentPeriod) return
        this.currentPeriod = period
        document.querySelectorAll('.chart-period-btn').forEach(b => b.classList.remove('active'))
        btn.classList.add('active')
        // 切换周期时重建chart（不同周期时间格式不同，不能复用）
        this.destroyChart()
        this.loadKlineData()
      })
    })

    // 指标开关
    ;['ma5', 'ma10', 'ma20', 'ma60', 'volume'].forEach(id => {
      document.getElementById(`toggle-${id}`)?.addEventListener('change', () => this.applyIndicatorVisibility())
    })

    // 图表刷新
    document.getElementById('chart-refresh-btn')?.addEventListener('click', () => this.loadKlineData())

    // 信号生成
    document.getElementById('sig-generate-btn')?.addEventListener('click', () => {
      this.generateSignal()
      // 生成后自动刷新信号列表
      setTimeout(() => this.loadSignals(), 500)
    })
    document.getElementById('sig-auto-select')?.addEventListener('change', (e) => {
      const val = (e.target as HTMLSelectElement).value
      localStorage.setItem('gold_signal_auto_interval', val)
      this.restartSignalTimer()
    })
    document.getElementById('bt-run-btn')?.addEventListener('click', () => this.runBacktest())
    document.getElementById('cmp-run-btn')?.addEventListener('click', () => this.runCompare())
    document.getElementById('risk-btn')?.addEventListener('click', () => this.loadRisk())
    document.getElementById('sens-run-btn')?.addEventListener('click', () => this.runSensitivity())
    document.getElementById('val-run-btn')?.addEventListener('click', () => this.runValidation())
    document.getElementById('bt-strategy-select')?.addEventListener('change', (e) => {
      this.renderParams((e.target as HTMLSelectElement).value)
    })

    // 交易模式切换
    document.getElementById('trading-mode-switch')?.addEventListener('click', () => this.switchTradingMode())

    // 风险监控刷新
    document.getElementById('risk-overview-btn')?.addEventListener('click', () => this.loadRiskOverview())

    // RL 强化学习
    document.getElementById('rl-train-btn')?.addEventListener('click', () => this.runRLTraining())
    document.getElementById('rl-signal-btn')?.addEventListener('click', () => {
      const sel = document.getElementById('sig-strategy-select') as HTMLSelectElement
      if (sel) sel.value = 'rl_ppo'
      this.generateSignal()
    })
    document.getElementById('rl-refresh-btn')?.addEventListener('click', () => this.loadRLStatus())
  }

  // ===== K线图 =====
  private initChart() {
    const container = document.getElementById('kline-chart')
    if (!container) return

    // 确保容器有尺寸
    if (container.clientWidth === 0 || container.clientHeight === 0) {
      console.warn('Chart container has 0 size, retrying...')
      requestAnimationFrame(() => this.initChart())
      return
    }

    const dark = isDark()

    if (this.chart) {
      this.chart.remove()
      this.chart = null
    }

    this.chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: dark ? '#1e293b' : '#ffffff' },
        textColor: dark ? '#94a3b8' : '#475569',
      },
      grid: {
        vertLines: { color: dark ? '#334155' : '#e2e8f0' },
        horzLines: { color: dark ? '#334155' : '#e2e8f0' },
      },
      crosshair: {
        mode: 0,
        vertLine: {
          color: dark ? '#64748b' : '#94a3b8',
          style: 2, width: 1,
          labelVisible: true,
          labelBackgroundColor: dark ? '#475569' : '#94a3b8',
        },
        horzLine: {
          color: dark ? '#64748b' : '#94a3b8',
          style: 2, width: 1,
          labelVisible: true,
          labelBackgroundColor: dark ? '#475569' : '#94a3b8',
        },
      },
      rightPriceScale: {
        borderColor: dark ? '#475569' : '#cbd5e1',
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: dark ? '#475569' : '#cbd5e1',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: true },
    })

    // 主图 K 线
    this.candlestickSeries = this.chart.addSeries(CandlestickSeries, {
      upColor: '#ef4444', downColor: '#10b981',
      borderUpColor: '#ef4444', borderDownColor: '#10b981',
      wickUpColor: '#ef4444', wickDownColor: '#10b981',
    })

    // MA 线
    const maColors: Record<string, string> = {
      ma5: '#f59e0b', ma10: '#3b82f6', ma20: '#8b5cf6', ma60: '#ec4899',
    }
    ;['ma5', 'ma10', 'ma20', 'ma60'].forEach(key => {
      this.maSeries[key] = this.chart!.addSeries(LineSeries, {
        color: maColors[key], lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      } as any)
    })

    // 副图成交量
    this.volumeSeries = this.chart.addSeries(HistogramSeries, {
      color: dark ? '#334155' : '#cbd5e1',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume-scale',
    } as any)
    this.chart.priceScale('volume-scale').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })
  }

  private async loadKlineData() {
    try {
      const resp = await api.getGoldBars('AU0', this.currentPeriod, 300)
      if (resp.success && resp.data) {
        this.currentBars = resp.data.bars || []
        this.updateKlineChart(resp.data)
      }
    } catch (e) {
      console.error('Kline load failed:', e)
    }
    // 加载技术分析
    this.loadKlineAnalysis()
  }

  /** 加载K线技术解读 */
  private async loadKlineAnalysis() {
    try {
      const resp = await api.getGoldAnalysis('AU0', this.currentPeriod, 500)
      if (resp.success && resp.data) {
        this.renderAnalysis(resp.data)
      }
    } catch (e) {
      console.error('Kline analysis load failed:', e)
    }
  }

  private renderAnalysis(d: any) {
    const body = document.getElementById('analysis-body')
    const label = document.getElementById('analysis-period-label')
    if (!body) return
    if (label) {
      const p = this.currentPeriod
      label.textContent = PERIOD_LABELS[p] || (p === 'd' ? '日线' : p + '分钟')
    }

    const trend = d.trend || {}
    const ind = d.indicators || {}
    const mas = d.mas || {}
    const levels = d.levels || {}
    const vol = d.volume || {}
    const patterns = d.patterns || []
    const judgment = d.judgment || ''

    const dirClass = trend.direction === '上涨' ? 'bullish' : trend.direction === '下跌' ? 'bearish' : 'neutral'
    const maClass = trend.ma_alignment === '多头排列' ? 'bullish' : trend.ma_alignment === '空头排列' ? 'bearish' : ''

    // 简化fib显示 — 只看0.618和0.786
    const fib = levels.fib || {}
    const fib618 = fib['0.618']
    const fib786 = fib['0.786']

    // 生成形态标签
    const patternHtml = patterns.length > 0
      ? patterns.slice(-4).map((p: any) =>
          `<span class="analysis-pattern-tag pattern-${p.type === '大阳线' ? 'bull' : p.type === '大阴线' ? 'bear' : 'doji'}">${p.type}</span>`
        ).join('')
      : '<span class="analysis-pattern-tag pattern-none">无明显形态</span>'

    body.innerHTML = `
      <div class="analysis-grid">
        <div class="analysis-card">
          <div class="analysis-card-title">📈 趋势研判</div>
          <table class="analysis-table">
            <tr><td>方向</td><td class="${dirClass}"><b>${trend.direction || '--'}</b></td></tr>
            <tr><td>均线排列</td><td class="${maClass}">${trend.ma_alignment || '--'}</td></tr>
            <tr><td>均线位置</td><td class="${dirClass}">${trend.ma_position || '--'}</td></tr>
            <tr><td>斜率</td><td>${trend.slope != null ? trend.slope.toFixed(1) : '--'}</td></tr>
            <tr><td>近1周</td><td class="${(trend.change_1w||0) >= 0 ? 'bullish' : 'bearish'}">${trend.change_1w != null ? trend.change_1w.toFixed(1) + '%' : '--'}</td></tr>
            <tr><td>近1月</td><td class="${(trend.change_1m||0) >= 0 ? 'bullish' : 'bearish'}">${trend.change_1m != null ? trend.change_1m.toFixed(1) + '%' : '--'}</td></tr>
            <tr><td>近3月</td><td class="${(trend.change_3m||0) >= 0 ? 'bullish' : 'bearish'}">${trend.change_3m != null ? trend.change_3m.toFixed(1) + '%' : '--'}</td></tr>
            <tr><td>连涨跌</td><td>${trend.streak || '--'}</td></tr>
          </table>
        </div>

        <div class="analysis-card">
          <div class="analysis-card-title">🔧 技术指标</div>
          <table class="analysis-table">
            <tr><td>RSI(14)</td><td class="${(ind.rsi_signal||'') === '超卖' ? 'bearish' : (ind.rsi_signal||'') === '超买' ? 'bullish' : ''}"><b>${ind.rsi14 != null ? ind.rsi14 : '--'}</b> <span class="signal-tag ${ind.rsi_signal === '超卖' ? 'tag-oversold' : ind.rsi_signal === '超买' ? 'tag-overbought' : 'tag-neutral'}">${ind.rsi_signal || '--'}</span></td></tr>
            <tr><td>布林上轨</td><td>${ind.bb_upper != null ? ind.bb_upper.toFixed(1) : '--'}</td></tr>
            <tr><td>布林中轨</td><td>${ind.bb_mid != null ? ind.bb_mid.toFixed(1) : '--'}</td></tr>
            <tr><td>布林下轨</td><td>${ind.bb_lower != null ? ind.bb_lower.toFixed(1) : '--'}</td></tr>
            <tr><td>布林带宽</td><td>${ind.bb_width != null ? ind.bb_width.toFixed(1) + '%' : '--'}</td></tr>
            <tr><td>价格位置</td><td class="${(ind.bb_position||0) < 20 ? 'bearish' : (ind.bb_position||0) > 80 ? 'bullish' : ''}">${ind.bb_position != null ? ind.bb_position.toFixed(1) + '%' : '--'} <span class="signal-tag tag-neutral">${ind.bb_signal||'--'}</span></td></tr>
            <tr><td>ATR(14)</td><td>${ind.atr14 != null ? ind.atr14.toFixed(1) : '--'}</td></tr>
          </table>
        </div>

        <div class="analysis-card">
          <div class="analysis-card-title">💰 关键价位</div>
          <table class="analysis-table">
            <tr><td>近60日高</td><td class="bearish">${levels.recent_high != null ? levels.recent_high.toFixed(1) : '--'}</td></tr>
            <tr><td>近60日低</td><td class="bullish">${levels.recent_low != null ? levels.recent_low.toFixed(1) : '--'}</td></tr>
            <tr><td>52周高</td><td class="bearish">${levels.high_52w != null ? levels.high_52w.toFixed(1) : '--'}</td></tr>
            <tr><td>52周低</td><td class="bullish">${levels.low_52w != null ? levels.low_52w.toFixed(1) : '--'}</td></tr>
            <tr><td>价格分位</td><td>${levels.price_position_52w != null ? levels.price_position_52w.toFixed(0) + '%' : '--'}</td></tr>
            <tr><td>FIB 61.8%</td><td class="bearish">${fib618 != null ? fib618.toFixed(1) : '--'}</td></tr>
            <tr><td>FIB 78.6%</td><td class="bearish">${fib786 != null ? fib786.toFixed(1) : '--'}</td></tr>
          </table>
        </div>

        <div class="analysis-card">
          <div class="analysis-card-title">📊 量能形态</div>
          <table class="analysis-table">
            <tr><td>当日量</td><td>${fmtNum(vol.latest)}</td></tr>
            <tr><td>20日均量</td><td>${fmtNum(vol.avg_20)}</td></tr>
            <tr><td>量比(20/60)</td><td>${vol.ratio_20_60 != null ? vol.ratio_20_60.toFixed(2) : '--'} <span class="signal-tag ${vol.trend === '缩量' ? 'tag-oversold' : vol.trend === '放量' ? 'tag-overbought' : 'tag-neutral'}">${vol.trend||'--'}</span></td></tr>
            <tr><td>近期量比</td><td>${vol.recent_vol_ratio != null ? vol.recent_vol_ratio.toFixed(1) + 'x' : '--'}</td></tr>
            <tr><td>K线形态</td><td class="pattern-cell">${patternHtml}</td></tr>
            <tr><td>均线MA5</td><td>${mas.ma5 != null ? mas.ma5.toFixed(1) : '--'}</td></tr>
            <tr><td>均线MA10</td><td>${mas.ma10 != null ? mas.ma10.toFixed(1) : '--'}</td></tr>
            <tr><td>均线MA60</td><td>${mas.ma60 != null ? mas.ma60.toFixed(1) : '--'}</td></tr>
          </table>
        </div>
      </div>

      <div class="analysis-judgment">
        <div class="judgment-icon">💡</div>
        <div class="judgment-text">${judgment}</div>
      </div>
    `
  }

  // ===== 策略对比面板 =====

  private async loadStrategyComparison() {
    try {
      const resp = await api.getGoldStrategyComparison('AU0')
      if (resp.success && resp.data) {
        this.renderStrategyComparison(resp.data)
      }
    } catch (e) {
      console.error('Strategy comparison load failed:', e)
    }
  }

  private renderStrategyComparison(d: any) {
    const container = document.getElementById('strategy-compare')
    const badge = document.getElementById('regime-badge')
    if (!container) return

    const regime = d.market_regime || '--'
    const regimeDesc = d.regime_description || ''
    const strategies: any[] = d.strategies || []
    const indicators = d.indicators_summary || {}

    if (badge) {
      badge.textContent = regime
      badge.className = 'regime-badge ' + (
        regime.includes('多头') ? 'regime-bull' :
        regime.includes('空头') ? 'regime-bear' :
        regime.includes('超卖') ? 'regime-oversold' :
        regime.includes('超买') ? 'regime-overbought' : 'regime-neutral'
      )
    }

    container.innerHTML = `
      <div class="compare-header">
        <div class="compare-regime">
          <span class="regime-desc">${regimeDesc}</span>
          <div class="compare-indicators">
            <span class="indicator-chip"><b>RSI</b> ${indicators.rsi14 ?? '--'}</span>
            <span class="indicator-chip"><b>趋势强度</b> ${indicators.trend_strength ?? '--'}%</span>
            <span class="indicator-chip"><b>均线</b> ${indicators.ma_alignment || '--'}</span>
            <span class="indicator-chip"><b>波动</b> ${indicators.vol_anomaly_pct ?? '--'}%</span>
          </div>
        </div>
        <div class="compare-best">
          推荐: <span class="best-strategy-name">${d.best_icon || ''} ${d.best_strategy || '--'}</span>
        </div>
      </div>

      <div class="compare-cards">
        ${strategies.map(s => {
          const score = s.score ?? 0
          const barClass = score >= 70 ? 'bar-high' : score >= 45 ? 'bar-mid' : 'bar-low'
          return `
            <div class="compare-card">
              <div class="compare-card-top">
                <span class="compare-icon">${s.icon || '📊'}</span>
                <div class="compare-card-title">${s.strategy_name || '--'}</div>
                <div class="compare-score ${score >= 70 ? 'score-high' : score >= 45 ? 'score-mid' : 'score-low'}">${score}<span class="score-unit">分</span></div>
              </div>
              <div class="compare-bar-bg">
                <div class="compare-bar ${barClass}" style="width:${score}%"></div>
              </div>
              <div class="compare-tags">
                ${(s.tags || []).map((t: string) => `<span class="compare-tag">${t}</span>`).join('')}
              </div>
              <div class="compare-desc">${s.description || ''}</div>
              <div class="compare-reasons">
                ${(s.reasons || []).map((r: string) => `<div class="compare-reason">${r}</div>`).join('')}
              </div>
            </div>
          `
        }).join('')}
      </div>
    `
  }

  private updateKlineChart(data: any) {
    if (!this.candlestickSeries) {
      this.initChart()
    }
    if (!this.candlestickSeries || !this.chart) return

    const bars: any[] = data.bars || []
    const indicators = data.indicators || {}
    const isDaily = this.currentPeriod === 'd'

    // 记录更新时间
    const timeEl = document.getElementById('chart-update-time')
    if (timeEl) {
      timeEl.textContent = '更新: ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    }

    // 时间转换：日线→字符串, 分钟线→Unix秒戳
    // 数据源为CST(北京时间)，用本地时间构造确保显示正确
    const toChartTime = (t: string): Time => {
      if (isDaily) return t as Time
      // t = "2026-06-27T00:15:00"
      const p = t.split(/[-T:]/)
      return Math.floor(new Date(
        Number(p[0]), Number(p[1]) - 1, Number(p[2]),
        Number(p[3]), Number(p[4]), Number(p[5] || 0)
      ).getTime() / 1000) as Time
    }

    // K线
    const candleData: CandlestickData[] = bars.map(b => ({
      time: toChartTime(b.time),
      open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    this.candlestickSeries.setData(candleData)

    // MA指标
    ;['ma5', 'ma10', 'ma20', 'ma60'].forEach(key => {
      const series = this.maSeries[key]
      if (!series || !indicators[key]) return
      const lineData: LineData[] = []
      for (let i = 0; i < bars.length; i++) {
        const v = indicators[key][i]
        if (v != null) {
          lineData.push({ time: toChartTime(bars[i].time), value: v })
        }
      }
      series.setData(lineData)
    })

    // 成交量
    if (this.volumeSeries && bars.length > 0) {
      const volData: HistogramData[] = bars.map(b => {
        const isUp = b.close >= b.open
        return {
          time: toChartTime(b.time),
          value: b.volume,
          color: isUp ? 'rgba(239,68,68,0.3)' : 'rgba(16,185,129,0.3)',
        }
      })
      this.volumeSeries.setData(volData)
    }

    this.chart.timeScale().fitContent()

    // 应用指标可见性
    this.applyIndicatorVisibility()
  }

  private applyIndicatorVisibility() {
    ;['ma5', 'ma10', 'ma20', 'ma60', 'volume'].forEach(key => {
      const series = (this as any)[`${key}Series`] as ISeriesApi<any>
      if (!series) return
      const checked = (document.getElementById(`toggle-${key}`) as HTMLInputElement)?.checked ?? true
      series.applyOptions({ visible: checked })
    })
  }

  private onThemeChange() {
    if (this.chart && this.candlestickSeries) {
      this.destroyChart()
      this.initChart()
      if (this.currentBars.length > 0) {
        this.updateKlineChart({ bars: this.currentBars, indicators: this.calcIndicatorsFromBars() })
      }
    }
  }

  private calcIndicatorsFromBars(): Record<string, (number | null)[]> {
    const closes = this.currentBars.map(b => b.close)
    const indicator = (n: number) => {
      const r: (number | null)[] = []
      let sum = 0
      for (let i = 0; i < closes.length; i++) {
        sum += closes[i]
        if (i >= n) sum -= closes[i - n]
        r.push(i >= n - 1 ? parseFloat((sum / n).toFixed(2)) : null)
      }
      return r
    }
    return {
      ma5: indicator(5), ma10: indicator(10),
      ma20: indicator(20), ma60: indicator(60),
    }
  }

  // ===== 市场数据 =====
  private async loadMarketData() {
    try {
      const resp = await api.getGoldMarketData()
      if (resp.success && resp.data) {
        this.updateTicker(resp.data)
      }
    } catch (e) {
      // 静默失败，市场数据非关键
    }
  }

  private updateTicker(data: any) {
    const price = data.price
    const changePct = data.change_pct

    const set = (id: string, text: string, cls?: string) => {
      const el = document.getElementById(id)
      if (el) {
        el.textContent = text
        if (cls) { el.className = cls; return }
      }
    }

    const now = new Date()
    const timeStr = now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

    set('ticker-price', formatPrice(price))
    const changeEl = document.getElementById('ticker-change')
    if (changeEl) {
      const isUp = changePct >= 0
      changeEl.textContent = `${isUp ? '+' : ''}${data.change?.toFixed(2) ?? '--'} (${formatPct(changePct)})`
      changeEl.className = isUp ? 'ticker-change up' : 'ticker-change down'
    }
    set('ticker-open', formatPrice(data.open))
    set('ticker-high', formatPrice(data.high))
    set('ticker-low', formatPrice(data.low))
    set('ticker-high20', formatPrice(data.high_20))
    set('ticker-low20', formatPrice(data.low_20))
    set('ticker-vol', fmtNum(data.volume))
    set('ticker-volratio', data.vol_ratio?.toFixed(2) ?? '--')
    set('ticker-rsi', data.rsi_14?.toFixed(1) ?? '--')
    set('ticker-atr', formatPrice(data.atr_14))

    // 宏观指标
    set('macro-dxy', data.dxy != null ? data.dxy.toFixed(2) : '--')
    set('macro-vix', data.vix != null ? data.vix.toFixed(2) : '--')
    set('macro-us10y', data.us10y != null ? data.us10y.toFixed(2) + '%' : '--')
    set('macro-tips', data.tips != null ? data.tips.toFixed(2) + '%' : '--')
    set('macro-breakeven', data.breakeven != null ? data.breakeven.toFixed(2) + '%' : '--')

    const timeEl = document.getElementById('ticker-time')
    if (timeEl) timeEl.textContent = `${data.date} ${timeStr}`
  }

  // ===== 信号生成 =====

  /** 重启信号自动生成定时器 */
  private restartSignalTimer() {
    if (this.signalTimer) {
      clearInterval(this.signalTimer)
      this.signalTimer = null
    }
    const sel = document.getElementById('sig-auto-select') as HTMLSelectElement
    const indicator = document.getElementById('sig-auto-indicator')
    if (!sel) return
    const intervalSec = parseInt(sel.value, 10)
    if (!intervalSec || intervalSec <= 0) {
      if (indicator) indicator.style.display = 'none'
      return
    }
    if (indicator) indicator.style.display = 'inline'
    this.signalTimer = setInterval(() => this.generateSignal(), intervalSec * 1000)
  }

  private async generateSignal() {
    if (this.isWorking) return
    const strategyName = (document.getElementById('sig-strategy-select') as HTMLSelectElement)?.value || 'trend_following'
    this.isWorking = true
    const btn = document.getElementById('sig-generate-btn') as HTMLButtonElement
    if (btn) { btn.disabled = true; btn.textContent = '...' }

    // 定时模式 → 自动执行；手动点击 → 不自动执行
    const autoInterval = parseInt((document.getElementById('sig-auto-select') as HTMLSelectElement)?.value || '0', 10)
    const autoExecute = autoInterval > 0

    try {
      const resp = await api.generateTradingSignal(strategyName, 'AU0', autoExecute)
      if (resp.success && resp.data) {
        this.displaySignal(resp.data, autoExecute)
        if (autoExecute && resp.data.execution?.executed) {
          toast.success('信号已自动执行')
          // 执行后刷新 CTP 数据
          this.loadCtpData()
        } else if (autoExecute) {
          toast.info('信号已生成（自动执行未触发）')
        } else {
          toast.success('信号已生成')
        }
      } else {
        toast.error('信号生成失败')
      }
    } catch (e) {
      toast.error('信号生成出错')
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '生成信号' }
    }
  }

  private displaySignal(data: any, autoExecute?: boolean) {
    const container = document.getElementById('sig-result')
    if (!container) return

    if (!data.signal && !data.direction) {
      container.innerHTML = '<div class="empty-text">当前无交易信号</div>'
      return
    }

    const dir = data.direction || ''
    const isBull = dir === 'long'
    const isBear = dir === 'short'
    const riskOK = data.risk_check?.passed !== false
    const exec = data.execution
    const executed = exec?.executed

    container.innerHTML = `
      <div class="signal-card-compact ${isBull ? 'bullish' : isBear ? 'bearish' : ''}">
        <div class="scc-row">
          <span class="signal-dir-badge ${isBull ? 'long' : isBear ? 'short' : ''}">${DIR_LABEL[dir] || dir}</span>
          <span class="scc-price">¥${formatPrice(data.price)}</span>
          <span class="scc-item">${data.volume ?? 1}手</span>
          <span class="scc-item">止损 ${data.stop_loss ? '¥' + formatPrice(data.stop_loss) : '--'}</span>
          <span class="scc-item">${data.confidence != null ? (data.confidence * 100).toFixed(0) + '%' : '--'}</span>
          <span class="scc-item">${riskOK ? '✅' : '❌'}</span>
          <span class="scc-strategy">${STRATEGY_LABELS[data.strategy]?.icon || ''} ${STRATEGY_LABELS[data.strategy]?.name || data.strategy}</span>
        </div>
        ${data.reason ? `<div class="scc-reason">${data.reason}</div>` : ''}
        ${data.long_prob != null ? `
        <div class="scc-row" style="margin-top:4px;gap:6px;font-size:11px;color:var(--text-tertiary)">
          <span>做多 ${(data.long_prob*100).toFixed(0)}%</span>
          <span>做空 ${(data.short_prob*100).toFixed(0)}%</span>
          <span>观望 ${(data.hold_prob*100).toFixed(0)}%</span>
        </div>` : ''}
        <div class="scc-row" style="margin-top:6px;gap:8px">
          ${executed
            ? `<span class="scc-item" style="color:#10b981">✅ 已执行 ref=${exec.ctp_ref}</span>
               ${exec.sim_trade ? `<span class="scc-item">模拟成交 @¥${formatPrice(exec.sim_trade.price)}</span>` : ''}`
            : !autoExecute && riskOK
              ? `<button class="btn btn-primary btn-xs" data-sig-execute="${data.signal_id || data.signal?.signal_id || ''}" style="font-size:11px;padding:2px 8px">▶ 执行到 SimNow</button>`
              : `<span class="scc-item" style="color:#64748b">${exec?.reason || (riskOK ? '等待执行' : '风控未通过')}</span>`
          }
        </div>
      </div>
    `

    // 绑定执行按钮
    const execBtn = container.querySelector('[data-sig-execute]') as HTMLElement
    if (execBtn) {
      execBtn.addEventListener('click', () => this.executeSignal(execBtn.dataset.sigExecute || ''))
    }
  }

  private async executeSignal(signalId: string) {
    if (!signalId) return
    try {
      const resp = await api.executeTradingSignal(signalId)
      if (resp.success && resp.data.executed) {
        toast.success('已执行到 SimNow')
        this.loadCtpData()
        // 更新当前信号卡片显示执行结果，不重新生成信号
        this._updateSignalCardWithExecution(resp.data)
      } else {
        toast.error('执行失败: ' + (resp.data.reason || '未知'))
      }
    } catch (e) {
      toast.error('执行出错')
    }
  }

  private _updateSignalCardWithExecution(exec: any) {
    const container = document.getElementById('sig-result')
    if (!container) return
    const card = container.querySelector('.signal-card-compact')
    if (!card) return
    const row = card.querySelector('.scc-row:last-child')
    if (row) {
      row.innerHTML = exec.sim_trade
        ? `<span class="scc-item" style="color:#10b981">✅ 已执行 ref=${exec.ctp_ref}</span>
           <span class="scc-item">模拟成交 @¥${formatPrice(exec.sim_trade.price)}</span>`
        : `<span class="scc-item" style="color:#10b981">✅ 已执行 ref=${exec.ctp_ref}</span>`
    }
  }

  // ===== 回测 =====
  private async runBacktest() {
    if (this.isWorking) return
    const strategyName = (document.getElementById('bt-strategy-select') as HTMLSelectElement)?.value || 'trend_following'
    const startDate = (document.getElementById('bt-start-date') as HTMLInputElement)?.value || '2024-01-01'
    const endDate = (document.getElementById('bt-end-date') as HTMLInputElement)?.value || '2024-12-31'
    const capital = Number((document.getElementById('bt-capital') as HTMLInputElement)?.value || 1000000)
    const params = this.collectParams(strategyName)

    this.isWorking = true
    const btn = document.getElementById('bt-run-btn') as HTMLButtonElement
    if (btn) { btn.disabled = true; btn.textContent = '...' }

    try {
      const resp = await api.runTradingBacktest({
        strategy_name: strategyName, symbol: 'AU0', period: 'd',
        start_date: startDate, end_date: endDate, capital,
        params: Object.keys(params).length > 0 ? params : undefined,
      })
      if (resp.success && resp.data) {
        this.displayBacktest(resp.data)
        toast.success('回测完成')
      } else {
        toast.error('回测失败')
      }
    } catch (e) {
      toast.error('回测出错')
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '运行回测' }
    }
  }

  private displayBacktest(data: any) {
    const container = document.getElementById('bt-result')
    if (!container) return

    const perf = data.report?.performance || {}
    const risk = data.report?.risk || {}
    const trades = data.report?.trades || {}
    const cost = data.report?.cost || {}
    const ret = perf.total_return ?? 0
    const retClass = ret >= 0 ? 'positive' : 'negative'

    container.innerHTML = `
      <div class="bt-report ${retClass}">
        <div class="bt-metrics">
          <div class="bt-metric"><span class="bt-label">总收益率</span><span class="bt-value ${retClass}">${formatPct(ret)}</span></div>
          <div class="bt-metric"><span class="bt-label">年化收益</span><span class="bt-value">${formatPct(perf.annualized_return)}</span></div>
          <div class="bt-metric"><span class="bt-label">夏普比率</span><span class="bt-value">${perf.sharpe_ratio?.toFixed(2) ?? '--'}</span></div>
          <div class="bt-metric"><span class="bt-label">Sortino</span><span class="bt-value">${perf.sortino_ratio?.toFixed(2) ?? '--'}</span></div>
          <div class="bt-metric"><span class="bt-label">Calmar</span><span class="bt-value">${perf.calmar_ratio?.toFixed(2) ?? '--'}</span></div>
          <div class="bt-metric"><span class="bt-label">胜率</span><span class="bt-value">${formatPct(perf.win_rate)}</span></div>
          <div class="bt-metric"><span class="bt-label">盈亏比</span><span class="bt-value">${perf.profit_factor?.toFixed(2) ?? '--'}</span></div>
          <div class="bt-metric"><span class="bt-label">最大回撤</span><span class="bt-value negative">${formatPct(risk.max_drawdown)}</span></div>
          <div class="bt-metric"><span class="bt-label">VaR(95%)</span><span class="bt-value">¥${fmtNum(risk.var_95 ?? 0)}</span></div>
          <div class="bt-metric"><span class="bt-label">CVaR(95%)</span><span class="bt-value">¥${fmtNum(risk.cvar_95 ?? 0)}</span></div>
          <div class="bt-metric"><span class="bt-label">波动率</span><span class="bt-value">${formatPct(risk.volatility)}</span></div>
          <div class="bt-metric"><span class="bt-label">交易次数</span><span class="bt-value">${trades.total_count ?? 0}</span></div>
          <div class="bt-metric"><span class="bt-label">净盈亏</span><span class="bt-value">¥${fmtNum(cost.net_pnl)}</span></div>
          <div class="bt-metric"><span class="bt-label">手续费</span><span class="bt-value">¥${fmtNum(cost.total_commission)}</span></div>
          <div class="bt-metric"><span class="bt-label">滑点成本</span><span class="bt-value">¥${fmtNum(cost.total_slippage)}</span></div>
        </div>
      </div>
    `
  }

  // ===== 策略对比 =====
  private async runCompare() {
    if (this.isWorking) return
    const startDate = (document.getElementById('bt-start-date') as HTMLInputElement)?.value || '2024-01-01'
    const endDate = (document.getElementById('bt-end-date') as HTMLInputElement)?.value || '2024-12-31'
    this.isWorking = true
    const btn = document.getElementById('cmp-run-btn') as HTMLButtonElement
    if (btn) { btn.disabled = true; btn.textContent = '...' }

    try {
      const resp = await api.compareStrategies({
        strategy_names: ['trend_following', 'mean_reversion', 'ml_predictor'],
        symbol: 'AU0', period: 'd',
        start_date: startDate, end_date: endDate, capital: 1000000,
      })
      if (resp.success && resp.data) {
        this.displayCompare(resp.data)
      }
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '运行对比' }
    }
  }

  private displayCompare(data: any) {
    const container = document.getElementById('cmp-result')
    if (!container) return

    const strategies = data.strategies || {}
    const names = Object.keys(strategies)
    if (names.length === 0) {
      container.innerHTML = '<div class="empty-text">无数据</div>'
      return
    }

    const metrics = [
      { key: 'total_return', label: '总收益率', fmt: (v: any) => formatPct(v) },
      { key: 'sharpe_ratio', label: '夏普', fmt: (v: any) => v?.toFixed(2) ?? '--' },
      { key: 'max_drawdown', label: '最大回撤', fmt: (v: any) => formatPct(v) },
      { key: 'win_rate', label: '胜率', fmt: (v: any) => formatPct(v) },
      { key: 'profit_factor', label: '盈亏比', fmt: (v: any) => v?.toFixed(2) ?? '--' },
    ]

    let rows = ''
    for (const m of metrics) {
      rows += `<tr><td class="cmp-label">${m.label}</td>`
      for (const n of names) {
        const perf = strategies[n]?.performance || {}
        const risk = strategies[n]?.risk || {}
        const val = perf[m.key] ?? risk[m.key]
        rows += `<td>${m.fmt(val)}</td>`
      }
      rows += `</tr>`
    }

    const ranking = data.comparison?.sharpe_ranking as any[][] | undefined
    let rankingHtml = ''
    if (ranking) {
      rankingHtml = `<div class="cmp-ranking">🏆 夏普排名: ${ranking.map((r: any) => `${(STRATEGY_LABELS[r[0]] || { name: r[0] }).name}(${r[1]})`).join(' > ')}</div>`
    }

    container.innerHTML = `
      <table class="cmp-table">
        <thead><tr><th>指标</th>${names.map((n: string) => `<th>${(STRATEGY_LABELS[n] || { name: n }).icon} ${(STRATEGY_LABELS[n] || { name: n }).name}</th>`).join('')}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${rankingHtml}
    `
  }

  // ===== 高级分析 =====
  private async runSensitivity() {
    if (this.isWorking) return
    const strategyName = (document.getElementById('sens-strategy-select') as HTMLSelectElement)?.value || 'trend_following'
    this.isWorking = true
    const btn = document.getElementById('sens-run-btn') as HTMLButtonElement
    if (btn) { btn.disabled = true; btn.textContent = '...' }

    try {
      const resp = await api.runSensitivity({
        strategy_name: strategyName, symbol: 'AU0', period: 'd',
        start_date: '2024-01-01', end_date: '2024-12-31', capital: 1000000,
      })
      if (resp.success && resp.data) {
        this.displaySensitivity(resp.data, document.getElementById('sens-result'))
      }
    } catch (e) {
      toast.error('分析失败')
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '运行' }
    }
  }

  private displaySensitivity(data: any, container: HTMLElement | null) {
    if (!container) return
    const items = data.sensitivity_data || []
    const conclusion = data.conclusion || {}
    const byParam: Record<string, any[]> = {}
    for (const item of items) {
      byParam[item.param_name] = byParam[item.param_name] || []
      byParam[item.param_name].push(item)
    }

    let html = ''
    for (const [param, values] of Object.entries(byParam)) {
      const assessment = conclusion[param] || {}
      const badgeCls = assessment.status === '稳健' ? 'robust' : assessment.status === '中等' ? 'moderate' : 'fragile'
      html += `<div class="sens-group"><div class="sens-title">${this.getParamLabel(param)} <span class="sens-badge ${badgeCls}">${assessment.status || ''}</span></div>
        <table class="cmp-table small"><thead><tr><th>值</th><th>Sharpe</th><th>MaxDD%</th><th>Return%</th></tr></thead><tbody>
        ${values.map(v => `<tr><td>${v.param_value}</td><td>${v.sharpe ?? '--'}</td><td>${v.max_dd ?? '--'}</td><td>${v.total_return ?? '--'}</td></tr>`).join('')}
        </tbody></table></div>`
    }
    container.innerHTML = html || '<div class="empty-text">无数据</div>'
  }

  private async runValidation() {
    if (this.isWorking) return
    const strategyName = (document.getElementById('val-strategy-select') as HTMLSelectElement)?.value || 'trend_following'
    this.isWorking = true
    const btn = document.getElementById('val-run-btn') as HTMLButtonElement
    if (btn) { btn.disabled = true; btn.textContent = '...' }

    try {
      const resp = await api.runValidation({
        strategy_name: strategyName, symbol: 'AU0', period: 'd',
        start_date: '2020-01-01', end_date: '2025-12-31', capital: 1000000,
      })
      if (resp.success && resp.data) {
        this.displayValidation(resp.data, document.getElementById('val-result'))
      }
    } catch (e) {
      toast.error('验证失败')
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '运行' }
    }
  }

  private displayValidation(data: any, container: HTMLElement | null) {
    if (!container) return
    const sv = data.sample_validation || {}
    const scv = data.scenario_validation || {}
    let html = ''

    if (sv.in_sample) {
      const deg = sv.sharpe_degradation_pct ?? 0
      html += `<div class="val-row"><strong>In/Out验证</strong> | In Sharpe: ${sv.in_sample.performance?.sharpe_ratio ?? '--'} | Out Sharpe: ${sv.out_sample?.performance?.sharpe_ratio ?? '--'} | 退化: ${deg}% | 过拟合风险: ${sv.overfitting_risk ?? '--'}</div>`
    }

    const scenarios = scv.results || []
    if (scenarios.length > 0) {
      html += `<div class="val-row"><strong>场景验证:</strong> ${scenarios.map((s: any) => `${s.scenario}: ${s.status === '通过' ? '✅' : '❌'} (Sharpe=${s.report?.sharpe ?? '--'})`).join(' | ')}</div>`
    }

    container.innerHTML = html || '<div class="empty-text">无数据</div>'
  }

  // ===== 信号列表 =====
  private async loadSignals() {
    try {
      const resp = await api.getTradingSignals(undefined, 10)
      if (resp.success && resp.data) {
        this.displaySignals(resp.data)
      }
    } catch (e) {
      toast.error('获取信号失败')
    }
  }

  private displaySignals(signals: any[]) {
    const container = document.getElementById('signals-panel')
    if (!container) return
    if (!signals?.length) {
      container.innerHTML = '<div class="empty-text">暂无信号</div>'
      return
    }
    container.innerHTML = '<div class="signal-grid">' + signals.map(s => {
      const dir = s.direction || ''
      const cls = dir.includes('long') && !dir.includes('close') ? 'long' : dir.includes('short') && !dir.includes('close') ? 'short' : ''
      const t = s.created_at ? new Date(s.created_at) : null
      const timeStr = t ? t.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' + t.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--'
      return `<div class="signal-mini ${cls}">
        <div class="sm-row1">
          <span class="sig-dir ${cls}">${DIR_LABEL[dir] || dir}</span>
          <span class="sig-price">@${s.price ?? '--'}</span>
        </div>
        <div class="sm-row2">
          <span class="sig-strategy">${STRATEGY_LABELS[s.strategy_id || s.strategy_name]?.name || s.strategy_id || s.strategy_name}</span>
          <span class="sig-time">${timeStr}</span>
        </div>
      </div>`
    }).join('') + '</div>'
  }

  // ===== 风控 =====
  private async loadRisk() {
    try {
      const resp = await api.getRiskStatus()
      if (resp.success && resp.data) {
        const container = document.getElementById('risk-panel')
        if (container) {
          const checks = resp.data.checks || []
          const daily = resp.data.daily_summary || {}
          container.innerHTML = `
            <div class="risk-daily">
              <div class="risk-mini"><span>日内信号</span><span>${daily.signal_count ?? 0} 次</span></div>
              <div class="risk-mini"><span>日内盈亏</span><span class="${(daily.total_pnl||0)>=0?'positive':'negative'}">${(daily.total_pnl||0)>=0?'+':''}¥${fmtNum(daily.total_pnl)}</span></div>
              <div class="risk-mini"><span>连续亏损</span><span class="${(daily.consecutive_losses||0) >= 3 ? 'negative' : ''}">${daily.consecutive_losses ?? 0} 次</span></div>
            </div>
            <div style="margin-top:8px;border-top:1px solid var(--border-color);padding-top:8px">
              ${checks.map((c: any) =>
                `<div class="risk-mini"><span>${c.name}</span><span>${c.threshold}</span><span class="risk-active">${c.status}</span></div>`
              ).join('')}
            </div>`
        }
      }
    } catch (e) {
      // 静默
    }
  }

  // ===== 模拟交易（支持 SimNow/openctp 切换） =====

  private async loadCtpData() {
    try {
      const resp = await api.getCtpData()
      if (!resp.success || !resp.data) return
      const d = resp.data

      // 状态指示器
      const badge = document.getElementById('ctp-badge')
      if (badge) {
        if (!d.enabled) {
          badge.textContent = '⚪ 未启用'
          badge.className = 'ctp-badge ctp-off'
        } else if (d.connected) {
          badge.textContent = '🟢 已连接'
          badge.className = 'ctp-badge ctp-on'
        } else {
          badge.textContent = '🔴 已断开'
          badge.className = 'ctp-badge ctp-off'
        }
      }

      // 更新下拉框选中值
      const sel = document.getElementById('trading-mode-select') as HTMLSelectElement
      if (sel && d.mode) {
        sel.value = d.mode
      }

      // 资金账户
      const accountEl = document.getElementById('ctp-account')
      if (accountEl) {
        const acct = d.account || {}
        if (acct.balance) {
          accountEl.innerHTML = `
            <div class="ctp-account-grid">
              <div class="ctp-account-item"><span class="ctp-a-label">总资产</span><span class="ctp-a-value">¥${fmtNum(acct.balance)}</span></div>
              <div class="ctp-account-item"><span class="ctp-a-label">可用</span><span class="ctp-a-value">¥${fmtNum(acct.available)}</span></div>
              <div class="ctp-account-item"><span class="ctp-a-label">保证金</span><span class="ctp-a-value">¥${fmtNum(acct.margin)}</span></div>
              <div class="ctp-account-item"><span class="ctp-a-label">浮动盈亏</span><span class="ctp-a-value ${(acct.pnl||0)>=0?'positive':'negative'}">${(acct.pnl||0)>=0?'+':''}¥${fmtNum(acct.pnl)}</span></div>
            </div>`
        } else {
          accountEl.innerHTML = '<div class="empty-text">无资金数据</div>'
        }
      }

      // 持仓
      const posEl = document.getElementById('ctp-positions')
      if (posEl) {
        const list = d.positions || []
        if (!list.length) {
          posEl.innerHTML = '<div class="empty-text">无持仓</div>'
        } else {
          posEl.innerHTML = '<table class="ctp-table"><thead><tr><th>合约</th><th>方向</th><th>手数</th><th>均价</th><th>盈亏</th></tr></thead><tbody>' +
            list.map((p: any) => `
              <tr>
                <td>${p.symbol||'--'}</td>
                <td class="${p.direction==='long'?'positive':'negative'}">${p.direction==='long'?'多':'空'}</td>
                <td>${p.volume??0}</td>
                <td>¥${formatPrice(p.avg_price)}</td>
                <td class="${(p.pnl||0)>=0?'positive':'negative'}">${(p.pnl||0)>=0?'+':''}¥${fmtNum(p.pnl)}</td>
              </tr>`).join('') +
            '</tbody></table>'
        }
      }

      // 委托
      const ordEl = document.getElementById('ctp-orders')
      if (ordEl) {
        const list = d.orders || []
        if (!list.length) {
          ordEl.innerHTML = '<div class="empty-text">无委托记录</div>'
        } else {
          const latest = list.slice(-10).reverse()
          ordEl.innerHTML = '<table class="ctp-table small"><thead><tr><th>合约</th><th>方向</th><th>价格</th><th>数量</th><th>成交</th><th>状态</th><th>时间</th></tr></thead><tbody>' +
            latest.map((o: any) => {
              const dir = o.direction || ''
              const dirCls = dir.includes('buy') || dir === 'long' ? 'positive' : 'negative'
              const dirLabel = dir.includes('buy') || dir === 'long' ? '买' : '卖'
              return `<tr>
                <td>${o.symbol||'--'}</td>
                <td class="${dirCls}">${dirLabel}</td>
                <td>¥${formatPrice(o.price)}</td>
                <td>${o.volume??0}</td>
                <td>${o.traded??0}</td>
                <td>${o.status||'--'}</td>
                <td class="ctp-time">${o.insert_time||'--'}</td>
              </tr>`
            }).join('') +
            '</tbody></table>'
        }
      }
    } catch (_) { /* 静默 */ }
  }

  // ===== 风险监控面板 =====

  private async loadRiskOverview() {
    try {
      const resp = await api.get('/gold/trading/risk/overview')
      if (resp.success && resp.data) {
        this.renderRiskOverview(resp.data)
      }
    } catch (_) { /* 静默 */ }
  }

  private renderRiskOverview(d: any) {
    // 风控概览
    const overviewEl = document.getElementById('rm-overview')
    if (overviewEl) {
      const rs = d.risk_status || {}
      overviewEl.innerHTML = `
        <div class="rm-card-title">📊 风控概览</div>
        <div class="rm-card-body">
          <div class="rm-metrics">
            <div class="rm-metric"><span class="rm-label">日内信号</span><span class="rm-value">${rs.today_signal_count ?? 0} 次</span></div>
            <div class="rm-metric"><span class="rm-label">日内盈亏</span><span class="rm-value ${(rs.daily_pnl||0)>=0?'positive':'negative'}">¥${(rs.daily_pnl||0)>=0?'+':''}${(rs.daily_pnl||0).toLocaleString()}</span></div>
            <div class="rm-metric"><span class="rm-label">连续亏损</span><span class="rm-value ${(rs.consecutive_losses||0)>=3?'negative':''}">${rs.consecutive_losses ?? 0} 次</span></div>
            <div class="rm-metric"><span class="rm-label">回撤限</span><span class="rm-value">${(rs.max_drawdown_pct ?? 0) * 100}%</span></div>
            <div class="rm-metric"><span class="rm-label">持仓上限</span><span class="rm-value">${rs.max_position_lots ?? 0} 手</span></div>
          </div>
        </div>`
    }

    // 市场状态
    const regimeEl = document.getElementById('rm-regime')
    if (regimeEl) {
      const mr = d.market_regime || {}
      const regime = mr.regime || 'unknown'
      const stats = mr.stats || {}
      regimeEl.innerHTML = `
        <div class="rm-card-title">🌤 市场状态</div>
        <div class="rm-card-body">
          <div class="rm-regime-badge ${regime}">${regime === 'trending' ? '📈 趋势' : regime === 'volatile' ? '🌊 高波动' : '📊 震荡'}</div>
          <div class="rm-metrics" style="margin-top:6px">
            ${['trending', 'ranging', 'volatile'].map(r => {
              const s = stats[r] || {}
              return `<div class="rm-metric"><span class="rm-label">${r}</span><span class="rm-value">${s.n ?? 0}天 Sharpe=${s.sharpe ?? 0}</span></div>`
            }).join('')}
          </div>
        </div>`
    }

    // 过拟合检测
    const ofEl = document.getElementById('rm-overfitting')
    if (ofEl) {
      const of = d.overfitting || {}
      const isOverfit = of.overfitting_detected
      ofEl.innerHTML = `
        <div class="rm-card-title">⚡ 过拟合检测</div>
        <div class="rm-card-body">
          <div class="rm-verdict ${isOverfit ? 'negative' : 'positive'}">${isOverfit ? '⚠ 高风险' : '✅ 正常'}</div>
          <div class="rm-metrics" style="margin-top:6px">
            <div class="rm-metric"><span class="rm-label">DSR</span><span class="rm-value">${of.dsr != null ? of.dsr.toFixed(4) : '--'}</span></div>
            <div class="rm-metric"><span class="rm-label">Sharpe衰减</span><span class="rm-value">${of.sharpe_drop != null ? (of.sharpe_drop*100).toFixed(1) + '%' : '--'}</span></div>
            <div class="rm-metric"><span class="rm-label">IS Sharpe</span><span class="rm-value">${of.avg_is_sharpe != null ? of.avg_is_sharpe.toFixed(3) : '--'}</span></div>
            <div class="rm-metric"><span class="rm-label">OOS Sharpe</span><span class="rm-value">${of.avg_oos_sharpe != null ? of.avg_oos_sharpe.toFixed(3) : '--'}</span></div>
          </div>
        </div>`
    }

    // 策略退化
    const decayEl = document.getElementById('rm-decay')
    if (decayEl) {
      const dc = d.decay || {}
      const verdict = dc.verdict || '--'
      const isDecay = dc.decay_detected
      decayEl.innerHTML = `
        <div class="rm-card-title">📉 策略退化</div>
        <div class="rm-card-body">
          <div class="rm-verdict ${isDecay ? 'negative' : 'positive'}">${verdict}</div>
          <div class="rm-metrics" style="margin-top:6px">
            <div class="rm-metric"><span class="rm-label">收益衰减</span><span class="rm-value ${dc.return_decay?.decay_detected ? 'negative' : ''}">${dc.return_decay?.decay_detected ? '⚠ 是' : '✅ 否'}</span></div>
            <div class="rm-metric"><span class="rm-label">近期均值</span><span class="rm-value">${dc.return_decay?.recent_mean != null ? (dc.return_decay.recent_mean * 100).toFixed(3) + '%' : '--'}</span></div>
            <div class="rm-metric"><span class="rm-label">历史均值</span><span class="rm-value">${dc.return_decay?.historical_mean != null ? (dc.return_decay.historical_mean * 100).toFixed(3) + '%' : '--'}</span></div>
            <div class="rm-metric"><span class="rm-label">波动率</span><span class="rm-value">${dc.context?.recent_volatility != null ? (dc.context.recent_volatility * 100).toFixed(2) + '%' : '--'}</span></div>
          </div>
        </div>`
    }
  }

  // ===== 辅助方法 =====
  private async loadStrategies() {
    try {
      const resp = await api.getTradingStrategies()
      if (resp.success && resp.data) {
        this.strategies = resp.data
        this.renderParams('trend_following')
      }
    } catch (e) { /* ignore */ }
  }

  /** 切换交易模式 */
  private async switchTradingMode() {
    const sel = document.getElementById('trading-mode-select') as HTMLSelectElement
    if (!sel) return
    const mode = sel.value
    try {
      const resp = await api.setTradingMode(mode)
      if (resp.success) {
        toast.success(`已切换至 ${mode}`)
        this.loadCtpData()
      } else {
        toast.error('切换失败')
      }
    } catch (e) {
      toast.error('切换失败')
    }
  }

  // ===== RL 强化学习 =====

  private async loadRLStatus() {
    try {
      const resp = await api.get('/gold/trading/rl/status')
      if (resp.success) {
        const d = resp.data
        const statusEl = document.getElementById('rl-status')
        if (statusEl) {
          if (d.ready && d.model_count > 0) {
            statusEl.textContent = `🟢 就绪 (${d.model_count}个模型)`
          } else if (d.model_count > 0) {
            statusEl.textContent = `🟡 ${d.model_count}个模型 (未加载)`
          } else {
            statusEl.textContent = '⚪ 未训练'
          }
        }
        // 显示模型列表
        const models = d.latest_model ? [d.latest_model] : []
        if (models.length > 0) {
          const m = models[0]
          this.displayRLMetrics({
            model_file: m.filename,
            size: `${(m.size_bytes / 1024).toFixed(1)}KB`,
            device: d.device || 'N/A',
            modified: m.modified || '',
          })
        }
      }
    } catch (_) { /* ignore */ }
  }

  private async runRLTraining() {
    if (this.isWorking) return
    const iterations = parseInt((document.getElementById('rl-iterations') as HTMLInputElement)?.value || '50', 10)
    const steps = parseInt((document.getElementById('rl-steps') as HTMLInputElement)?.value || '1024', 10)
    this.isWorking = true

    const btn = document.getElementById('rl-train-btn') as HTMLButtonElement
    const progress = document.getElementById('rl-progress')
    if (btn) { btn.disabled = true; btn.textContent = '训练中...' }
    if (progress) progress.textContent = '⏳ 开始训练...'

    try {
      const resp = await api.post('/gold/trading/rl/train', {
        n_iterations: iterations,
        n_steps: steps,
      }, 600000) // 10分钟超时
      if (resp.success && resp.data) {
        const iterData = resp.data.iterations || []
        const last = iterData[iterData.length - 1] || {}
        toast.success(`RL训练完成! return=${last.avg_return_pct ?? '?'}%`)
        this.displayRLMetrics(last)
        if (progress) progress.textContent = `✅ 完成: ${last.avg_return_pct ?? 0}%`
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '未知错误'
      toast.error(`RL训练失败: ${msg}`)
      if (progress) progress.textContent = `❌ 训练失败 (${msg})`
    } finally {
      this.isWorking = false
      if (btn) { btn.disabled = false; btn.textContent = '训练模型' }
      this.loadRLStatus()
    }
  }

  private displayRLMetrics(data: any) {
    const container = document.getElementById('rl-card-metrics')
    if (!container) return
    const body = container.querySelector('.rl-card-body') || container
    body.innerHTML = `
      <table class="rl-table">
        <tr><td>总收益率</td><td class="${(data.avg_return_pct||0) >= 0 ? 'positive' : 'negative'}">${data.avg_return_pct != null ? data.avg_return_pct.toFixed(2) + '%' : '--'}</td></tr>
        <tr><td>Sharpe</td><td>${data.sharpe != null ? data.sharpe.toFixed(3) : '--'}</td></tr>
        <tr><td>胜率</td><td>${data.win_rate != null ? data.win_rate.toFixed(1) + '%' : '--'}</td></tr>
        <tr><td>最大回撤</td><td class="negative">${data.max_dd != null ? data.max_dd.toFixed(2) + '%' : '--'}</td></tr>
        <tr><td>模型文件</td><td style="font-size:11px;color:var(--text-tertiary)">${data.model_file || data.model_file || '--'}</td></tr>
        <tr><td>设备</td><td>${data.device || data.device || '--'}</td></tr>
      </table>
    `
  }

  private renderParams(strategyName: string) {
    const strategy = this.strategies.find(s => s.strategy_id === strategyName)
    const container = document.getElementById('bt-params-container')
    const row = document.getElementById('bt-params-row')
    if (!container || !row) return

    const ranges = strategy?.param_ranges || {}
    const defaults = strategy?.default_params || {}
    const keys = Object.keys(ranges)
    if (keys.length === 0) { container.classList.add('hidden'); return }
    container.classList.remove('hidden')

    row.innerHTML = keys.map(key => {
      const options = ranges[key] as number[]
      const def = defaults[key]
      return `<label>${this.getParamLabel(key)}:</label><select id="bt-param-${key}">
        ${options.map(v => `<option value="${v}" ${v === def || String(v) === String(def) ? 'selected' : ''}>${v}</option>`).join('')}
      </select>`
    }).join('')
  }

  private collectParams(strategyName: string): Record<string, any> {
    const strategy = this.strategies.find(s => s.strategy_id === strategyName)
    if (!strategy) return {}
    const ranges = strategy.param_ranges || {}
    const params: Record<string, any> = {}
    const defaults = strategy.default_params || {}

    for (const key of Object.keys(ranges)) {
      const el = document.getElementById(`bt-param-${key}`) as HTMLSelectElement
      if (el) {
        const val = el.value
        params[key] = key === 'ma_periods' ? val.split(',').map(Number) : Number(val)
      }
    }
    const diff: Record<string, any> = {}
    for (const [k, v] of Object.entries(params)) {
      if (JSON.stringify(v) !== JSON.stringify(defaults[k])) diff[k] = v
    }
    return diff
  }

  private getParamLabel(key: string): string {
    const labels: Record<string, string> = {
      ma_periods: 'MA周期', atr_period: 'ATR周期', atr_stop_multiplier: 'ATR止损倍数',
      donchian_entry: 'Donchian入场', donchian_exit: 'Donchian出场', position_size: '仓位',
      boll_period: 'BB周期', boll_dev: 'BB标准差', rsi_period: 'RSI周期',
      rsi_overbought: 'RSI超买', rsi_oversold: 'RSI超卖', window_size: '窗口大小',
      predict_interval: '预测间隔', change_threshold: '涨跌阈值', max_holding_bars: '最大持仓',
    }
    return labels[key] || key
  }
}

export const goldTradingUI = new GoldTradingUI()
