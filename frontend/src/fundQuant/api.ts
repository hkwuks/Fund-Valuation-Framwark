// frontend/src/fundQuant/api.ts
/**
 * FundQuant API 封装
 *
 * 独立于全局 api.ts，专注 fund-quant 域，返回类型化的数据。
 */

const BASE = '/api/fund-quant'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { 'Content-Type': 'application/json' } })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`)
  return res.json()
}

async function post<T>(path: string, body?: any): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${path}`)
  return res.json()
}

export interface NavPoint {
  date: string
  nav: number
  adjusted_nav?: number
}

export interface FundRanking {
  fund_code: string
  fund_name: string
  total_score: number
  factors: Record<string, number>
}

export interface BacktestResult {
  backtest_id: string
  status: string
  result?: {
    total_return: number
    annual_return: number
    max_drawdown: number
    sharpe_ratio: number
    win_rate: number
    total_trades: number
    equity_curve: { date: string; total_value: number }[]
    period_returns: Record<string, number>
  }
}

export interface AuroraBacktestResult {
  total_return: number
  annual_return: number
  sharpe_ratio: number
  max_drawdown: number
  volatility: number
  n_trading_days: number
  n_trades: number
  funds: string[]
  strategy: string
  mode?: string
}

export interface StrategyAllocationSignal {
  strategy: string
  direction: 'buy' | 'hold' | 'sell'
  weights: Record<string, number>
  confidence: number
  reason: string
  mode?: string
  capital?: number
  buy_amounts: Record<string, number>
  top_holdings: { fund_code: string; weight: number; score?: number; fund_name?: string }[]
  momentum_rank?: { fund_code: string; score: number; rank: number }[]
  asset_allocation?: Record<string, number>
}

export interface StrategyAllocationResult {
  strategies: StrategyAllocationSignal[]
  fund_codes: string[]
}

export interface SignalRecord {
  fund_code: string
  fund_name?: string
  direction: string
  confidence: number
  strategy_name: string
  created_at: string
  reason?: string
}

export interface PortfolioStatus {
  total_value: number
  cash: number
  return_pct: number
  position_count: number
  positions: Record<string, { shares: number; nav: number; value: number }>
  history_count: number
  // 扩展字段（需要后端配合）
  annual_return?: number
  max_drawdown?: number
  sharpe_ratio?: number
  volatility?: number
  benchmark_return?: number
  signal_count?: { buy: number; sell: number; hold: number }
}

export interface AttributionPeriod {
  date: string
  allocation: number
  selection: number
  interaction: number
  total: number
}

export interface AttributionResult {
  periods: AttributionPeriod[]
  cumulative: {
    allocation: number
    selection: number
    interaction: number
    excess: number
    benchmark: number
    total: number
  }
}

export interface MonthlyReturn {
  year: number
  month: number
  return: number
}

export interface MonthlyReturnResult {
  matrix: MonthlyReturn[]
  stats: {
    positive_months: number
    total_months: number
    avg_positive: number
    avg_negative: number
    max_positive: number
    max_negative: number
  }
}

export interface StrategyInfo {
  name: string
  display_name: string
  type: string
  description: string
  applicable_fund_types: string[]
  param_ranges?: StrategyParam[]
}

export interface StrategyParam {
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

export interface FactorExposureData {
  fund_code: string
  fund_name: string
  factors: Record<string, { value: number; weight: number; rank_pct: number }>
  total_score: number
  n_funds_in_category: number
}

export interface FactorExposureResult {
  success: boolean
  data: FactorExposureData
}

// ── 回测分析 ──
export interface AnalysisResult {
  has_analysis: boolean
  overfitting?: {
    deflated_sharpe: number
    min_btl_years: number
    actual_years: number
    min_btl_warning: string
    shuffle_p_value: number
    is_significant: boolean
    total_trials: number
  }
  significance?: {
    sharpe: number
    p_value: number
    ci_lower: number
    ci_upper: number
    is_significant: boolean
  }
  monte_carlo?: {
    n_simulations: number
    return_pct: Record<string, number>
    sharpe_ratio: Record<string, number>
    max_drawdown_pct: Record<string, number>
    ulcer_index?: Record<string, number>
    probability_of_loss: number
  }
  regime?: {
    n_regimes: number
    warning: string
    regimes: { label: string; duration_days: number; ann_return: number; ann_vol: number; sharpe: number }[]
  }
  factor_attribution?: {
    alpha: number
    alpha_tstat: number
    alpha_pvalue: number
    alpha_significant: boolean
    betas: Record<string, number>
    r_squared: number
    adj_r_squared: number
  }
}

// ── 参数扫描 ──
export interface ParamScanResult {
  mode: string
  param_names: string[]
  results: Record<string, any>[]
  n_iterations: number
  sensitivity_score?: Record<string, number>
  stability_region?: [number, number][]
}

// ── 模拟交易 ──
export interface PaperTradeSession {
  paper_trade_id: string
  strategy_name: string
  fund_codes: string[]
  initial_capital: number
  cash: number
  positions: Record<string, number>
  pending_orders: Record<string, any>[]
  equity_curve: { date: string; total_value: number; cash: number }[]
  status: string
  last_run_date: string | null
  created_at: string
}

export interface PaperTradeSummary {
  paper_trade_id: string
  strategy_name: string
  status: string
  days_run: number
  total_return: number
  current_value: number
  sharpe: number
  last_run: string | null
}

export const fundQuantApi = {
  // Portfolio KPI
  getPortfolioKPI: () => get<{ success: boolean; data: PortfolioStatus }>('/portfolio/status'),

  // Nav
  getNav: (code: string) => get<{ success: boolean; data: { nav_history: NavPoint[] } }>(`/nav/${code}`),
  collectNavData: (fundCodes: string[], years = 5) =>
    post<{ success: boolean; data: { fund_code: string; status: string; count: number }[] }>('/data/collect', { fund_codes: fundCodes, years }),

  // Signals
  getSignals: (fund_code?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (fund_code) params.set('fund_code', fund_code)
    return get<{ success: boolean; data: SignalRecord[] }>(`/signal/history?${params}`)
  },
  getLatestSignals: () => get<{ success: boolean; data: SignalRecord[] }>('/signal/latest'),

  // Selection
  screenFunds: (fund_type: string, top_n = 10, strategy = 'multi_factor') =>
    post<{ success: boolean; data: { rankings: FundRanking[] } }>('/selection/screen', { fund_type, top_n, strategy }),

  // Backtest
  runBacktest: (req: any) => post<{ success: boolean; data: { backtest_id: string; status: string } }>('/backtest/run', req),
  getBacktest: (id: string) => get<{ success: boolean; data: BacktestResult }>(`/backtest/result/${id}`),
  getBacktestList: (strategy_name?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (strategy_name) params.set('strategy_name', strategy_name)
    return get<{ success: boolean; data: BacktestResult[]; total: number }>(`/backtest/list?${params}`)
  },
  // — AuroraCore 引擎回测（etf_rotation / all_weather） —
  runAuroraBacktest: (req: {
    fund_codes: string[]; start_date: string; end_date: string;
    initial_capital?: number; strategy_name?: string; params?: Record<string, any>;
  }) => post<{ success: boolean; data: AuroraBacktestResult }>('/backtest/aurora-run', req),

// — 策略资产配置信号（以策略为中心） —
  getStrategyAllocation: (() => {
    const cache = new Map<string, { exp: number; data: StrategyAllocationResult }>()
    const inflight = new Map<string, Promise<{ success: boolean; data: StrategyAllocationResult }>>()
    const TTL = 8000
    return (fund_codes: string[], capital?: number, params?: Record<string, any>) => {
      const key = JSON.stringify({ c: [...fund_codes].sort(), p: params || {}, cap: capital || 100000 })
      const hit = cache.get(key)
      if (hit && hit.exp > Date.now()) return Promise.resolve({ success: true, data: hit.data })
      const ex = inflight.get(key)
      if (ex) return ex
      const p = post<{ success: boolean; data: StrategyAllocationResult }>(
        '/strategy/allocation/current', { fund_codes, capital: capital || 100000, params: params || {} },
      ).then(res => {
        if (res.success) {
          cache.set(key, { exp: Date.now() + TTL, data: res.data })
          if (cache.size > 64) cache.delete(cache.keys().next().value as string)
        }
        inflight.delete(key)
        return res
      }).catch(e => { inflight.delete(key); throw e })
      inflight.set(key, p)
      return p
    }
  })(),

  // — 新增接口 —
  getAttribution: (fund_codes: string[], start: string, end: string, method = 'brinson') =>
    get<{ success: boolean; data: AttributionResult }>(
      `/attribution/${method}?fund_codes=${fund_codes.join(',')}&start=${start}&end=${end}`,
    ),

  getMonthlyReturns: (fund_code: string) =>
    get<{ success: boolean; data: MonthlyReturnResult }>(`/portfolio/monthly-returns?fund_code=${fund_code}`),

  getStrategyList: () =>
    get<{ success: boolean; data: StrategyInfo[] }>('/strategy/list'),

  // — 新增：策略暴露接口 —
  getFactorExposure: (fund_code: string) =>
    get<FactorExposureResult>(`/factors/exposure/${fund_code}`),

  // — 新增：回测分析 —
  runAnalysis: (backtestId: string, nSimulations = 1000) =>
    post<{ success: boolean; data: { backtest_id: string; analysis: AnalysisResult } }>(
      '/backtest/analysis', { backtest_id: backtestId, n_simulations: nSimulations }
    ),

  // — 新增：参数扫描 —
  runParamScan: (req: {
    strategy_name: string; fund_codes: string[];
    start_date: string; end_date: string; initial_capital?: number;
    mode: string; param_name?: string; param_values?: any[];
    param_grid?: Record<string, any>; param_dist?: Record<string, any>;
    fixed_params?: Record<string, any>; n_iter?: number;
  }) => post<{ success: boolean; data: ParamScanResult }>('/backtest/param-scan', req),

  // — 新增：模拟交易 —
  paperTradeStart: (req: { strategy_name: string; fund_codes: string[]; initial_capital?: number }) =>
    post<{ success: boolean; data: { paper_trade_id: string } }>('/paper-trade/start', req),
  paperTradeRun: (paperTradeId: string) =>
    post<{ success: boolean; data: any }>('/paper-trade/run', { paper_trade_id: paperTradeId }),
  paperTradeStop: (paperTradeId: string) =>
    post<{ success: boolean; data: any }>('/paper-trade/stop', { paper_trade_id: paperTradeId }),
  paperTradeList: () =>
    get<{ success: boolean; data: PaperTradeSummary[] }>('/paper-trade/list'),
  paperTradeStatus: (id: string) =>
    get<{ success: boolean; data: PaperTradeSession }>(`/paper-trade/status/${id}`),
}