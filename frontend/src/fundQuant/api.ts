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

export interface TimingSignal {
  direction: string
  confidence: number
  strategy_name: string
  timestamp: string
}

export interface TimingResult {
  signals: TimingSignal[]
  fusion_signal?: any
}

export interface FundRanking {
  fund_code: string
  fund_name: string
  total_score: number
  factors: Record<string, number>
}

export interface AllocationResult {
  weights: Record<string, number>
  expected_return: number
  portfolio_volatility: number
  sharpe_ratio: number
  method: string
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

export interface SignalRecord {
  fund_code: string
  fund_name?: string
  direction: string
  confidence: number
  strategy_name: string
  created_at: string
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

export interface SignalExplain {
  strategy_name: string
  strategy_display_name: string
  formula_description: string
  verdict: string
  key_values: Record<string, { value: number; label: string; format?: string; highlight?: string }>
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

export const fundQuantApi = {
  // Portfolio KPI
  getPortfolioKPI: () => get<{ success: boolean; data: PortfolioStatus }>('/portfolio/status'),

  // Nav
  getNav: (code: string) => get<{ success: boolean; data: { nav_history: NavPoint[] } }>(`/nav/${code}`),
  collectNavData: (fundCodes: string[], years = 5) =>
    post<{ success: boolean; data: { fund_code: string; status: string; count: number }[] }>('/data/collect', { fund_codes: fundCodes, years }),

  // Timing
  evaluateTiming: (fund_code: string, params?: any, strategy_name?: string) =>
    post<{ success: boolean; data: TimingResult }>('/timing/evaluate', { fund_code, strategy_name: strategy_name || '', params: params || {} }),

  // Signals
  getSignals: (fund_code?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (fund_code) params.set('fund_code', fund_code)
    return get<{ success: boolean; data: SignalRecord[] }>(`/signal/history?${params}`)
  },
  getLatestSignals: () => get<{ success: boolean; data: SignalRecord[] }>('/signal/latest'),

  // Allocation
  optimizeAllocation: (fund_codes: string[], params?: any) =>
    post<{ success: boolean; data: AllocationResult }>('/allocation/optimize', { fund_codes, params: params || {} }),

  // Selection
  screenFunds: (fund_type: string, top_n = 10) =>
    post<{ success: boolean; data: { rankings: FundRanking[] } }>('/selection/screen', { fund_type, top_n }),

  // Backtest
  runBacktest: (req: any) => post<{ success: boolean; data: { backtest_id: string } }>('/backtest/run', req),
  getBacktest: (id: string) => get<{ success: boolean; data: BacktestResult }>(`/backtest/result/${id}`),

  // — 新增接口 —
  getAttribution: (fund_codes: string[], start: string, end: string, method = 'brinson') =>
    get<{ success: boolean; data: AttributionResult }>(
      `/attribution/${method}?fund_codes=${fund_codes.join(',')}&start=${start}&end=${end}`,
    ),

  getMonthlyReturns: (fund_code: string) =>
    get<{ success: boolean; data: MonthlyReturnResult }>(`/portfolio/monthly-returns?code=${fund_code}`),

  // — 新增：择时研究接口 —
  explainTiming: (fund_code: string, strategy_name: string, params?: any) =>
    post<{ success: boolean; data: SignalExplain }>('/timing/explain', { fund_code, strategy_name, params: params || {} }),

  getStrategyList: () =>
    get<{ success: boolean; data: StrategyInfo[] }>('/strategy/list'),

  // — 新增：因子暴露接口 —
  getFactorExposure: (fund_code: string) =>
    get<FactorExposureResult>(`/factors/exposure/${fund_code}`),
}