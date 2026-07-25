/**
 * 基金量化前端全局状态
 *
 * EventEmitter 模式：面板通过 subscribe 监听状态变化，通过 emit 发布事件。
 * 避免面板间直接耦合。
 */

export interface FundInfo {
  fund_code: string
  fund_name: string
  fund_type: string
}

export interface SignalSummary {
  direction: 'buy' | 'sell' | 'hold'
  confidence: number
  strategy_name: string
  timestamp: string
  fund_code: string
  fund_name: string
}

export interface PortfolioKPI {
  total_return: number
  annual_return: number
  max_drawdown: number
  sharpe_ratio: number
  volatility: number
  benchmark_return: number
  signal_count: { buy: number; sell: number; hold: number }
}

export interface PortfolioAllocation {
  total_value: number
  cash: number
  return_pct: number
  position_count: number
  positions: Record<string, { shares: number; nav: number; value: number; weight: number }>
}

export interface PanelLayout {
  visible: boolean
  order: number
  grid_pos?: { x: number; y: number; w: number; h: number }
}

export interface LayoutConfig {
  name: string
  panels: Record<string, PanelLayout>
}

export interface LoadingState {
  isRefreshing: boolean
  lastRefreshTime: number | null
  errors: Record<string, string | null>
}

export interface ResearchState {
  visible: boolean
  activeTab: 'timing' | 'exposure'
  fundCode: string | null
  signal?: SignalSummary | null
}

export interface GlobalState {
  fundPool: FundInfo[]
  selectedFund: string | null
  showSignalPopup: boolean
  signals: SignalSummary[]
  portfolio: PortfolioKPI | null
  allocation: PortfolioAllocation | null
  layout: LayoutConfig
  settings: Record<string, any>
  loading: LoadingState
  researchPanel: ResearchState
  customParams: Record<string, Record<string, any>>
}

type StateKey = keyof GlobalState
type Listener = (state: GlobalState) => void

export class EventEmitter {
  private listeners: Map<StateKey, Set<Listener>> = new Map()
  private allListeners: Set<Listener> = new Set()
  private state: GlobalState

  constructor(initial: GlobalState) {
    this.state = initial
  }

  getState(): GlobalState {
    return this.state
  }

  get<K extends StateKey>(key: K): GlobalState[K] {
    return this.state[key]
  }

  set<K extends StateKey>(key: K, value: GlobalState[K]): void {
    this.state = { ...this.state, [key]: value }
    this.listeners.get(key)?.forEach(fn => fn(this.state))
    this.allListeners.forEach(fn => fn(this.state))
  }

  on(key: StateKey | '*', fn: Listener): () => void {
    if (key === '*') {
      this.allListeners.add(fn)
      return () => this.allListeners.delete(fn)
    }
    if (!this.listeners.has(key)) this.listeners.set(key, new Set())
    this.listeners.get(key)!.add(fn)
    return () => this.listeners.get(key)?.delete(fn)
  }
}

// 默认布局
const DEFAULT_LAYOUT: LayoutConfig = {
  name: '默认布局',
  panels: {
    kpi: { visible: true, order: 0 },
    nav_chart: { visible: true, order: 1, grid_pos: { x: 0, y: 1, w: 2, h: 1 } },
    signal_list: { visible: true, order: 2, grid_pos: { x: 0, y: 2, w: 1, h: 1 } },
    allocation: { visible: true, order: 3, grid_pos: { x: 1, y: 2, w: 1, h: 1 } },
    fund_ranking: { visible: false, order: 4, grid_pos: { x: 1, y: 0, w: 1, h: 1 } },
    attribution: { visible: false, order: 5, grid_pos: { x: 0, y: 3, w: 1, h: 1 } },
    monthly_returns: { visible: false, order: 6, grid_pos: { x: 1, y: 3, w: 1, h: 1 } },
  },
}

// 单例
export const state = new EventEmitter({
  fundPool: [],
  selectedFund: null,
  showSignalPopup: false,
  signals: [],
  portfolio: null,
  allocation: null,
  layout: DEFAULT_LAYOUT,
  settings: {},
  loading: { isRefreshing: false, lastRefreshTime: null, errors: {} },
  researchPanel: { visible: false, activeTab: 'timing', fundCode: null, signal: null },
  customParams: {},
})
