"""
入口脚本：用真实数据跑一次 Walk-Forward 评估，打印结果
用法：python -m backend.gold.ml.run_xgb_baseline
"""
import asyncio
import numpy as np
import pandas as pd
from loguru import logger

from backend.gold.data.gateway import GoldDataGateway
from backend.gold.ml.xgb_direction_predictor import XGBDirectionPredictor


async def main():
    logger.info("=== XGBoost 方向预测基线评估 ===")

    # 1. 获取数据
    logger.info("获取黄金日线数据...")
    gateway = GoldDataGateway()
    bars = await gateway.get_bars(
        symbol="AU0", period="d",
        start="2018-01-01", end=None,
        refresh=False,
    )
    if not bars or len(bars) < 300:
        logger.warning(f"数据不足 ({len(bars) if bars else 0})，尝试强制刷新...")
        bars = await gateway.get_bars(
            symbol="AU0", period="d",
            start="2018-01-01", end=None,
            refresh=True,
        )

    if not bars or len(bars) < 300:
        logger.error("无法获取足够的黄金日线数据，退出")
        return

    logger.info(f"获取到 {len(bars)} 条日线数据 ({bars[0].datetime.date()} ~ {bars[-1].datetime.date()})")

    # 2. 转为 DataFrame
    rows = []
    for b in bars:
        rows.append({
            "datetime": b.datetime,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        })
    df = pd.DataFrame(rows)

    # 3. 尝试获取宏观数据并合并
    try:
        macro = await gateway.get_macro_data(start="2018-01-01")
        if not macro.empty:
            macro["date"] = pd.to_datetime(macro["date"])
            df["date"] = pd.to_datetime(df["datetime"]).dt.date
            macro["date_dt"] = macro["date"].dt.date
            df = df.merge(
                macro[["date_dt", "DXY_value", "VIX_value", "US10Y_value"]],
                left_on="date", right_on="date_dt", how="left"
            )
            df.drop(columns=["date", "date_dt"], inplace=True)
            # 向前填充
            for col in ["DXY_value", "VIX_value", "US10Y_value"]:
                df[col] = df[col].ffill().bfill().fillna(0)
            logger.info(f"合并宏观数据: {macro.shape[0]} 条记录")
    except Exception as e:
        logger.warning(f"宏观数据合并失败: {e}")

    logger.info(f"数据形状: {df.shape}, 列: {list(df.columns)}")

    # 4. Walk-Forward 评估
    logger.info("运行 Walk-Forward 验证 (5折, train=252天, test=63天)...")
    predictor = XGBDirectionPredictor()
    result = predictor.train_walk_forward(
        df, n_splits=5, train_window=252, test_window=63
    )

    # 5. 打印结果
    print("\n" + "=" * 60)
    print("XGBoost 方向预测 — Walk-Forward 评估结果")
    print("=" * 60)
    print(f"总样本: {result.total_train} (训练) + {result.total_test} (测试)")
    print(f"折数: {len(result.fold_results)}")
    print(f"平均准确率: {result.mean_accuracy:.4f}")
    print(f"标准差: {result.std_accuracy:.4f}")
    print(f"每折准确率: {[f'{a:.4f}' for a in result.accuracies]}")
    print()

    print("-" * 60)
    print("每折详情:")
    print("-" * 60)
    for fold in result.fold_results:
        print(f"  Fold {fold.fold}: acc={fold.accuracy:.4f}  "
              f"train={fold.n_train}  test={fold.n_test}  "
              f"[{fold.train_start} ~ {fold.train_end}] → "
              f"[{fold.test_start} ~ {fold.test_end}]")
        cm = fold.conf_matrix
        tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        print(f"    TN={tn} FP={fp} FN={fn} TP={tp}  "
              f"precision={precision:.3f} recall={recall:.3f}")

    print()

    # 6. 特征重要性
    print("-" * 60)
    print("Top-15 特征重要性:")
    print("-" * 60)
    for i, (feat, imp) in enumerate(result.feature_importance.items()):
        if i >= 15:
            break
        print(f"  {i+1:2d}. {feat:30s} {imp:.6f}")

    # 7. 结论
    print()
    print("=" * 60)
    threshold = 0.53
    if result.mean_accuracy > threshold:
        print(f"结论: 平均准确率 {result.mean_accuracy:.4f} > {threshold} ✓")
        print("特征预测力验证通过，可进入RL阶段。")
    else:
        print(f"结论: 平均准确率 {result.mean_accuracy:.4f} <= {threshold} ✗")
        print("特征预测力不足，建议优化特征或调整参数后再进入RL。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())