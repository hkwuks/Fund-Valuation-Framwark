"""FundQuant 配置 - 基金量化投资系统"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List

# 项目根目录 (backend/fund_quant/core/config.py → 4层到项目根)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class FundQuantSettings(BaseSettings):
    """基金量化系统配置"""
    model_config = SettingsConfigDict(
        env_prefix="FUND_QUANT_", env_file=".env", extra="ignore",
    )

    # SQLite
    FUND_QUANT_DB_PATH: str = str(_PROJECT_ROOT / "data" / "backend" / "fund_quant" / "fund_quant.db")

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 1

    # APScheduler
    SCHEDULER_JOBSTORE_URL: str = f"sqlite:///{_PROJECT_ROOT / 'data' / 'backend' / 'fund_quant' / 'scheduler_jobs.db'}"
    SCHEDULER_MISFIRE_GRACE: int = 3600
    SCHEDULER_COALESCE: bool = True
    SCHEDULER_MAX_INSTANCES: int = 1

    # 数据采集
    COLLECTION_BATCH_SIZE: int = 100
    COLLECTION_MAX_RETRIES: int = 3
    COLLECTION_RATE_LIMIT: float = 0.1  # 每秒请求数上限

    # 策略默认参数
    TIMING_WEIGHT: float = 0.5
    SELECTION_WEIGHT: float = 0.2
    ALLOCATION_WEIGHT: float = 0.3

    # 风控默认参数
    DEFAULT_MIN_CONFIDENCE: float = 0.6
    DEFAULT_COOLDOWN_DAYS: int = 5
    DEFAULT_MIN_HOLDING_DAYS: int = 7
    DEFAULT_MAX_POSITION_PCT: float = 0.3
    DEFAULT_MAX_DRAWDOWN_PCT: float = 0.15
    DEFAULT_MIN_CASH_PCT: float = 0.05

    # 回测
    BACKTEST_DEFAULT_CAPITAL: float = 100000.0
    BACKTEST_DEFAULT_REBALANCE: str = "monthly"


fund_quant_settings = FundQuantSettings()
