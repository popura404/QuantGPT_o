"""Tests for iteration module — compute_factor_score."""


from quantgpt.iteration import compute_factor_score, is_duplicate_expression
from quantgpt.search_ledger import (
    apply_selection_penalty,
    attempt_payload,
    compute_search_penalty,
    count_local_attempts,
    family_key,
)


class TestComputeFactorScore:
    def _base_summary(self, **overrides):
        d = {
            "ic_mean": 0.03,
            "rank_ic_mean": 0.03,
            "ic_ir": 0.5,
            "ic_win_rate": 0.6,
            "long_short_sharpe": 1.0,
            "monotonicity_score": 0.7,
            "spread": 0.05,
            "wq_fitness": 0.0,
            "wq_brain": {
                "wq_sharpe": 0.5,
                "wq_fitness": 0.3,
                "wq_turnover": 0.15,
                "wq_is_tests": {},
            },
        }
        d.update(overrides)
        return d

    def _base_report(self, **overrides):
        d = {"cagr": 0.10, "sharpe": 1.0}
        d.update(overrides)
        return d

    def test_returns_required_keys(self):
        result = compute_factor_score(self._base_summary(), self._base_report())
        assert "score" in result
        assert "grade" in result
        assert "component_scores" in result
        assert set(result["component_scores"].keys()) == {
            "ic_mean", "ic_ir", "stability", "anti_overfit", "group_backtest",
            "wq_alignment",
        }

    def test_score_in_range(self):
        result = compute_factor_score(self._base_summary(), self._base_report())
        assert 0 <= result["score"] <= 100

    def test_perfect_factor_gets_high_score(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
            wq_brain={"wq_sharpe": 1.8, "wq_fitness": 1.5, "wq_turnover": 0.3, "wq_is_tests": {}},
        )
        result = compute_factor_score(summary, self._base_report(), anti_overfit_score=90)
        assert result["score"] >= 80
        assert result["grade"] == "A"

    def test_weak_factor_gets_low_score(self):
        summary = self._base_summary(
            ic_mean=0.005, ic_ir=0.1, ic_win_rate=0.50,
            long_short_sharpe=0.1, monotonicity_score=0.2, spread=-0.02,
        )
        result = compute_factor_score(summary, self._base_report(cagr=-0.05, sharpe=-0.3))
        assert result["score"] < 40
        assert result["grade"] in ("C", "D")

    def test_negative_cagr_caps_grade(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
        )
        result = compute_factor_score(summary, self._base_report(cagr=-0.01))
        assert result["grade"] == "C"
        assert result["capped"] is True
        assert result["score"] <= 59.9

    def test_negative_sharpe_caps_grade(self):
        summary = self._base_summary(
            ic_mean=0.08, ic_ir=1.5, ic_win_rate=0.75,
            long_short_sharpe=2.0, monotonicity_score=1.0, spread=0.10,
        )
        result = compute_factor_score(summary, self._base_report(sharpe=-0.5))
        assert result["capped"] is True
        assert result["cap_reason"] == "negative_sharpe"

    def test_anti_overfit_none_defaults_to_50(self):
        result = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=None)
        assert result["component_scores"]["anti_overfit"] == 50.0

    def test_anti_overfit_clamped(self):
        result = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=150)
        assert result["component_scores"]["anti_overfit"] == 100.0
        result2 = compute_factor_score(self._base_summary(), self._base_report(), anti_overfit_score=-20)
        assert result2["component_scores"]["anti_overfit"] == 0.0

    def test_zero_inputs(self):
        summary = self._base_summary(
            ic_mean=0, ic_ir=0, ic_win_rate=0.5,
            long_short_sharpe=0, monotonicity_score=0, spread=0,
        )
        result = compute_factor_score(summary, self._base_report(cagr=0, sharpe=0))
        assert result["score"] >= 0


class TestIsDuplicateExpression:
    def test_exact_duplicate(self):
        assert is_duplicate_expression("rank(close)", ["rank(close)", "rank(volume)"])

    def test_whitespace_difference_is_duplicate(self):
        assert is_duplicate_expression("rank( close )", ["rank(close)"])

    def test_not_duplicate(self):
        assert not is_duplicate_expression("rank(volume)", ["rank(close)"])

    def test_empty_existing(self):
        assert not is_duplicate_expression("rank(close)", [])


class TestSearchLedger:
    def test_family_key_ignores_standalone_numeric_parameters(self):
        assert family_key("Rank(ts_mean(close, 20))") == family_key("rank(ts_mean(close, 30))")
        assert family_key("rank(volume / adv20)") != family_key("rank(volume / adv30)")

    def test_penalty_is_zero_for_first_attempt_and_grows(self):
        assert compute_search_penalty(0, 0) == 0.0
        exact_penalty = compute_search_penalty(2, 0)
        family_penalty = compute_search_penalty(0, 2)
        assert exact_penalty > family_penalty > 0

    def test_selection_score_never_goes_below_zero(self):
        assert apply_selection_penalty(5, 10) == 0.0
        assert apply_selection_penalty(80, 2.5) == 77.5

    def test_local_counts_respect_scope(self):
        params = {"universe": "hs300", "start_date": "2023-01-01", "end_date": "2023-12-31", "holding_period": 5, "n_groups": 5}
        other_scope = {**params, "universe": "csi500"}
        attempts = [
            attempt_payload(
                user_id="00000000-0000-0000-0000-000000000001",
                task_id="task",
                parent_task_id="parent",
                generation_index=0,
                expression="rank(ts_mean(close, 20))",
                params=params,
                source_strategy="exploit",
                from_mutation=True,
                from_crossover=False,
            ),
            attempt_payload(
                user_id="00000000-0000-0000-0000-000000000001",
                task_id="task",
                parent_task_id="parent",
                generation_index=1,
                expression="rank(ts_mean(close, 30))",
                params=params,
                source_strategy="exploit",
                from_mutation=True,
                from_crossover=False,
            ),
            attempt_payload(
                user_id="00000000-0000-0000-0000-000000000001",
                task_id="task",
                parent_task_id="parent",
                generation_index=2,
                expression="rank(ts_mean(close, 20))",
                params=other_scope,
                source_strategy="exploit",
                from_mutation=True,
                from_crossover=False,
            ),
        ]

        counts = count_local_attempts(attempts, "rank(ts_mean(close, 20))", params)

        assert counts.expression_attempts == 1
        assert counts.family_attempts == 2
