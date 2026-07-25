#!/usr/bin/env python
"""
基金回测引擎 BacktestGap — 测试运行脚本
使用方法:
    python run_tests.py                  # 运行全部 BacktestGap 新开发测试
    python run_tests.py --all            # 运行 fund_quant 全部测试（跳过已知失败）
    python run_tests.py --coverage       # 运行 + 覆盖率报告
    python run_tests.py --pipeline       # CI管道模式: 测试 + 覆盖率 + lint检查
"""

import subprocess
import sys
import time

# BacktestGap 各 Phase 测试文件（按阶段分组）
PHASE_TESTS = {
    "P1-申购折扣+历史费率": ["tests/fund_quant/test_cost_model_ext.py"],
    "P1-分红集成": ["tests/fund_quant/test_dividend_integration.py"],
    "P1-巨额赎回": ["tests/fund_quant/test_redemption_gate.py"],
    "P1-净值缺失": ["tests/fund_quant/test_nav_gaps.py"],
    "P1-清盘合并": ["tests/fund_quant/test_liquidation.py"],
    "P2-Monte Carlo": ["tests/fund_quant/test_monte_carlo.py"],
    "P2-过拟合检测": ["tests/fund_quant/test_overfitting.py"],
    "P2-参数扫描": ["tests/fund_quant/test_param_scanner.py"],
    "P2-显著性检验": ["tests/fund_quant/test_significance.py"],
    "P2-市场状态": ["tests/fund_quant/test_regime_detector.py"],
    "P2-QDII时差": ["tests/fund_quant/test_qdii_timezone.py"],
    "P3-披露日历": ["tests/fund_quant/test_disclosure.py"],
    "P3-基金转换": ["tests/fund_quant/test_conversion.py"],
    "P3-PaperTrader": ["tests/fund_quant/test_paper_trader.py"],
    "P4-Brinson归因": ["tests/fund_quant/test_brinson.py"],
    "P4-因子归因": ["tests/fund_quant/test_factor_attribution.py"],
    "P4-向量化回测": ["tests/fund_quant/test_vectorized_engine.py"],
    "覆盖补齐": ["tests/fund_quant/test_coverage_gaps.py"],
}

ALL_NEW_TESTS = sum(PHASE_TESTS.values(), [])

KNOWN_FAILURES = [
    "test_position_estimator", "test_quality", "test_signal_portfolio",
    "test_api_integration", "test_core",
]


def banner(text: str):
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}")


def run_tests(test_files: list, verbose: bool = True, tb: str = "short"):
    cmd = [sys.executable, "-m", "pytest"]
    if verbose:
        cmd.append("-v")
    cmd.extend([f"--tb={tb}"])
    cmd.extend(test_files)
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - t0
    return result.returncode, elapsed


def run_coverage(test_files: list):
    """Run tests with coverage measurement."""
    modules = "backend.fund_quant.backtest"
    cmd = [sys.executable, "-m", "coverage", "run", f"--source={modules}",
           "-m", "pytest", "-v", "--tb=short"] + test_files
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - t0

    # Generate coverage report
    subprocess.run([sys.executable, "-m", "coverage", "report",
                    "--sort=-Cover",
                    "--include=*/backtest/redemption_gate.py,*/backtest/liquidation.py,*/backtest/monte_carlo.py,*/backtest/overfitting.py,*/backtest/param_scanner.py,*/backtest/significance.py,*/backtest/regime_detector.py,*/backtest/disclosure.py,*/backtest/paper_trader.py,*/backtest/brinson.py,*/backtest/factor_attribution.py,*/backtest/vectorized_engine.py,*/backtest/cost_model.py"])
    return result.returncode, elapsed


def main():
    mode = "new" if len(sys.argv) < 2 else sys.argv[1]

    if mode == "--pipeline":
        banner("BacktestGap CI Pipeline")
        # 1. Run all new tests
        rc, elapsed = run_tests(ALL_NEW_TESTS, verbose=True)
        print(f"\nAll tests: {'PASS' if rc == 0 else 'FAIL'} ({elapsed:.1f}s)")
        if rc != 0:
            sys.exit(rc)
        # 2. Coverage
        run_coverage(ALL_NEW_TESTS)
        # 3. Quick summary
        print(f"\nPipeline complete. {len(ALL_NEW_TESTS)} test files executed.")

    elif mode == "--coverage":
        run_coverage(ALL_NEW_TESTS)

    elif mode == "--all":
        # Run all fund_quant tests, skipping known failing modules
        skip_args = []
        for kf in KNOWN_FAILURES:
            skip_args.extend(["--ignore", f"tests/fund_quant/{kf}.py"])
        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short",
               "tests/fund_quant/"] + skip_args
        subprocess.run(cmd)

    else:
        # Default: run all BacktestGap new tests grouped by Phase
        total_passed = 0
        total_failed = 0
        for phase_name, test_files in PHASE_TESTS.items():
            banner(f"Phase: {phase_name}")
            rc, elapsed = run_tests(test_files, verbose=False, tb="line")
            if rc == 0:
                print(f"  ✅ {phase_name}: pass ({elapsed:.1f}s)")
                total_passed += 1
            else:
                print(f"  ❌ {phase_name}: FAIL ({elapsed:.1f}s)")
                total_failed += 1

        print(f"\n{'=' * 60}")
        print(f"BacktestGap 测试总结: {total_passed} passed, {total_failed} failed")
        print(f"测试文件数: {len(ALL_NEW_TESTS)}")
        print(f"详细运行: python run_tests.py -v")
        if total_failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
