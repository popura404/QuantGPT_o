from quantgpt.daily_summary import _SYSTEM_PROMPT, _build_llm_prompt
from quantgpt.factor_signals import FactorSignal


def _factor_signal(**overrides) -> FactorSignal:
    data = {
        "factor_id": "momentum",
        "factor_name": "20日动量",
        "category": "trend",
        "signal_description": "动量样本信号",
        "direction": "转强",
        "dispersion": "高分化",
        "top_stocks": [("sh.600519", 1.2345, "贵州茅台")],
        "bottom_stocks": [("sz.000001", -0.4321, "平安银行")],
        "today_mean": 0.2,
        "yesterday_mean": 0.1,
        "pct_above_median": 51.0,
        "top10_pct_change": 0.12,
        "percentile_20d": 95.0,
        "zscore_20d": 2.1,
        "signal_strength": 2,
    }
    data.update(overrides)
    return FactorSignal(**data)


def test_daily_summary_system_prompt_allows_explicitly_provided_stocks():
    assert "严禁出现任何个股代码或个股名称" not in _SYSTEM_PROMPT
    assert "允许引用数据中明确提供的个股代码或个股名称" in _SYSTEM_PROMPT
    assert "禁止编造未提供的个股信息" in _SYSTEM_PROMPT


def test_build_llm_prompt_includes_provided_stock_samples_only():
    prompt = _build_llm_prompt(
        "2026-05-21",
        {"hs300_change": 0.1, "sz50_change": -0.2, "zz500_change": 0.3, "csi1000_change": 0.4},
        [_factor_signal()],
    )

    assert "## 个股样本信号" in prompt
    assert "贵州茅台(sh.600519)" in prompt
    assert "平安银行(sz.000001)" in prompt
    assert "不得补充未提供的个股信息" in prompt
    assert "不构成买卖建议" in prompt
