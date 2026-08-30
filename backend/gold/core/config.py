from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 (backend/gold/core/config.py → 4层到项目根)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class GoldSettings(BaseSettings):
    """黄金量化系统配置"""
    model_config = SettingsConfigDict(
        env_prefix="GOLD_", env_file=".env", extra="ignore",
    )

    # 数据目录
    gold_data_dir: str = str(_PROJECT_ROOT / "data" / "backend" / "gold")
    gold_db_path: str = str(_PROJECT_ROOT / "data" / "backend" / "gold" / "gold.db")

    # SHFE AU合约参数
    au_multiplier: int = 1000       # 合约乘数：1000克/手
    au_margin_rate: float = 0.08    # 保证金率：8%
    au_price_tick: float = 0.02     # 最小变动价位：0.02元/克
    au_limit_pct: float = 0.05      # 涨跌停板：±5%

    # 回测默认参数（统一为元/手）
    backtest_capital: float = 1_000_000
    backtest_commission_per_lot: float = 10.0        # 开仓手续费：10元/手
    backtest_close_commission_per_lot: float = 0.0   # 平今手续费：0元/手（SHFE AU平今免费）
    backtest_slippage_per_lot: float = 20.0          # 滑点：1跳=0.02元/克×1000=20元/手
    risk_free_rate: float = 0.025

    # 模拟交易模式切换: "simnow" / "openctp"
    trading_mode: str = "simnow"

    # ---- computed paths (用于非Pydantic字段的相对路径) ----
    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    # 风控参数
    max_drawdown_pct: float = 0.10
    max_daily_loss_pct: float = 0.03
    max_daily_signals: int = 20
    max_position_lots: int = 10        # 单品种最大持仓手数
    max_margin_ratio: float = 0.30     # 保证金占比上限
    max_consecutive_losses: int = 3    # 连续亏损熔断次数

    # AkShare数据
    akshare_symbol: str = "AU0"

    # CTP 通用配置
    ctp_enabled: bool = False
    ctp_symbols: str = ""                                 # 订阅合约列表（逗号分隔），留空自动按季度生成后续合约
    ctp_main_symbol: str = "AU0"                          # 前端显示用的主连代码（仅展示）

    # ---- SimNow 连接 ----
    simnow_md_address: str = "tcp://180.168.146.187:10131"
    simnow_td_address: str = "tcp://180.168.146.187:10130"
    simnow_user_id: str = ""
    simnow_password: str = ""

    # ---- openctp TTS 连接 ----
    openctp_md_address: str = "tcp://121.37.80.177:20004"
    openctp_td_address: str = "tcp://121.37.80.177:20002"
    openctp_user_id: str = ""
    openctp_password: str = ""


gold_settings = GoldSettings()
