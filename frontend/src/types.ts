export type AssetType = 'stock' | 'fund' | 'index' | 'bond';

export type MarketType = 'on_exchange' | 'off_exchange' | 'unknown';
export type TradeMode = 't0' | 't1' | 't2';

export type ValuationType = 'real_time_price' | 'index_based' | 'holdings_based' | 'hybrid_bond' | 'hybrid_qdii' | 'benchmark_only' | 'not_supported';

// 估值方法显示名称
export const ValuationMethodNames: Record<string, string> = {
  'real_time_price': '实时价格估值',
  'index_based': '指数估值',
  'holdings_based': '持仓估值',
  'hybrid_bond': '混合估值（债券 + 股票）',
  'hybrid_qdii': '混合估值（持仓 + 指数）',
  'benchmark_only': '业绩基准参考',
  'not_supported': '暂不支持',
};

export interface Holding {
  asset_code: string;
  asset_name: string;
  asset_type: AssetType;
  quantity: number;
  price?: number;
  market_value?: number;
  weight?: number;
}

export interface Fund {
  fund_code: string;
  fund_name: string;
  fund_type: string;
  total_shares: number;
  nav?: number;
  nav_date?: string;  // 最新净值日期
  previous_nav?: number;
  holdings: Holding[];
  market_type?: MarketType;
  trade_mode?: TradeMode;
  subscription_confirm_days?: number;
  redemption_confirm_days?: number;
  cash_arrival_days?: number;
  purchase_channel?: 'tiantian' | 'ant' | 'broker' | 'bank' | 'other';
  trading_profile_source?: string;
  trading_profile_confidence?: number;
  trading_profile_needs_confirmation?: boolean;
  estimated_nav?: number;
  estimated_change_percent?: number;
  last_update?: string;
  confidence_note?: string;  // 估值说明
  valuation_method?: string;  // 估值方法
  // 指数估值分位
  index_code?: string;
  index_name?: string;
  pe_value?: number;
  pe_percentile?: number;
  pb_value?: number;
  pb_percentile?: number;
  // 定投信号
  valuation_signal?: string;  // 深度低估/低估/合理/偏高/高估
  signal_action?: string;  // 加倍定投/正常定投/持有/止盈/卖出
  signal_source?: string;  // 信号数据来源
  // 场内折溢价
  iopv?: number;  // 场内ETF IOPV 实时估值
  premium_percent?: number;  // 场内ETF折溢价率(%，正=溢价)
}

export interface FundData {
  fund_code: string;
  fund_name: string;
  fund_type: string;
  nav?: number;
  nav_date?: string;
  previous_nav?: number;
  establish_date?: string;
  market_type: MarketType;
  benchmark?: string;
  tracking_index?: string;
  price?: number;
  change?: number;
  change_percent?: number;
  volume?: number;
  timestamp?: string;
}

export interface ValuationResult {
  fund_code: string;
  fund_name: string;
  valuation_type: ValuationType;
  valuation_method?: string;  // 估值方法说明
  estimated_nav?: number;
  estimated_change_percent?: number;
  previous_nav?: number;
  latest_nav?: number;
  nav_date?: string;
  total_value: number;
  holdings_value: Record<string, { weight: number; change_percent: number; contribution: number }>;
  benchmark_info?: Record<string, any>;
  confidence: number;
  confidence_note?: string;
  timestamp: string;
  // 指数估值分位
  index_code?: string;
  index_name?: string;
  pe_value?: number;
  pe_percentile?: number;
  pb_value?: number;
  pb_percentile?: number;
  // 定投信号
  valuation_signal?: string;  // 深度低估/低估/合理/偏高/高估
  signal_action?: string;  // 加倍定投/正常定投/持有/止盈/卖出
  signal_source?: string;  // 信号数据来源
  // 场内折溢价
  iopv?: number;  // 场内ETF IOPV 实时估值
  premium_percent?: number;  // 场内ETF折溢价率(%，正=溢价)
}

export interface MarketData {
  code: string;
  name: string;
  price: number;
  change?: number;
  change_percent?: number;
  volume?: number;
  timestamp: string;
}

export interface FundListResponse {
  funds: Fund[];
  total: number;
}

export interface ValuationRequest {
  fund_codes: string[];
}
