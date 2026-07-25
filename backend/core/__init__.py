"""AuroraCore — 通用量化框架内核"""
from .event import EventBus, Event, EventType
from .signal import Signal, Direction, Order, OrderStatus, Fill, Position, SignalLifecycle
from .data import Bar, FundNavPoint, DataFeed
from .strategy import Strategy, StrategyContext, StrategyRegistry
from .backtest import (
    BacktestEngine, BacktestReport,
    ExecutionEngine, SimExecutionEngine, T1ExecutionEngine, FuturesExecutionEngine,
    SlippageModel, NoSlippage, PercentageSlippage,
    CostModel, NoCost,
)
from .risk import (
    RiskCheck, RiskContext, RiskVerdict, RiskLevel, RiskPipeline,
    MaxDrawdownCheck, DailyLossCheck, SignalFrequencyCheck,
    ConsecutiveLossCheck, VarCheck, PositionLimitCheck,
)
from .evaluation import Metrics, MetricsCalculator, ComparisonReport
from .validation import (
    WalkForwardValidator, WalkForwardReport,
    CPCVValidator, CPCVReport,
    ParamOptimizer, OptimizationReport, calculate_pbo,
)
from .adapter import DomainAdapter
from .config import BacktestConfig
from .exceptions import (
    AuroraCoreError, ConfigError, DataError,
    SignalError, RiskError, StrategyError, ExecutionError, ValidationError,
)
from .style_drift import StyleDriftDetector, DriftResult
from .monte_carlo import MonteCarloSimulator, MonteCarloResult, SensitivityAnalyzer
from . import factor
