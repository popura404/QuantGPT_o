import exchange_calendars as xcals
import pandas as pd
import pytest

from quantgpt.data_snapshots import freeze_market_frame
from quantgpt.research.contracts import content_hash
from scripts.verify_multisecurity_round import SELECTION_RULE, parse_args, study_config


@pytest.fixture(scope="module")
def sessions():
    calendar = xcals.get_calendar("XNYS")
    return [day.date().isoformat() for day in calendar.sessions_in_range("2021-01-01", "2025-12-31")]


def arguments(*options, demo=False):
    mode = ["--demo"] if demo else ["--manifest-id", "fixture", "--import-root", "."]
    return parse_args([*mode, "--work-dir", "unused", *options])


def snapshot(days):
    return {"manifest_id": "fixture", "start_session": days[0], "end_session": days[-1],
            "data_input": {"source": "fixture", "feed": "daily", "adjustment": "raw", "asof": "2026-01-01T00:00:00Z"}}


def registered_snapshot(root, declared, observed, query=None):
    frame = pd.DataFrame({"trade_date": pd.to_datetime(observed), "stock_code": "fixture", "close": 10.0})
    frame.attrs["calendar_sessions"] = declared
    manifest = freeze_market_frame(frame, root, vendor="test", query_params=query)
    registered = snapshot(observed)
    registered["manifest_id"] = manifest["snapshot_id"]
    registered["data_input"].update(manifest_id=manifest["snapshot_id"], version_status="verified",
                                    content_sha256=manifest["content_hash"].removeprefix("sha256:"))
    return registered


def test_real_default_uses_full_trading_windows_with_warmup_and_purge(sessions):
    args = arguments()
    config, dates, scored_days, plan = study_config(args, snapshot(sessions), sessions)
    assert plan["profile"] == "daily_default"
    assert (plan["selection_sessions"], plan["holdout_sessions"], plan["warmup_sessions"]) == (756, 252, 30)
    assert len(scored_days) == 756
    selection_start, selection_end, holdout_start, holdout_end = map(sessions.index, dates)
    assert holdout_end == len(sessions) - 1 and holdout_end - holdout_start + 1 == 252
    assert holdout_start - selection_end - 1 == max(5, config["window"]["label_horizon_sessions"])
    assert selection_start - sessions.index(config["simulation_config"]["rebalance_anchor_session"]) == 30


def test_default_reports_insufficient_history_without_shortening_windows(sessions):
    sample = sessions[-752:]
    with pytest.raises(ValueError, match=r"1043 declared sessions, found 752"):
        study_config(arguments(), snapshot(sample), sessions)


@pytest.mark.parametrize(("demo", "options", "profile"), [
    (True, [], "synthetic_demo"),
    (False, ["--selection-sessions", "75", "--holdout-sessions", "65"], "legacy_short"),
])
def test_demo_and_explicit_historical_replay_preserve_dates_anchor_and_split_hash(sessions, demo, options, profile):
    sample = sessions[-180:]
    config, dates, scored_days, plan = study_config(arguments(*options, demo=demo), snapshot(sample), sample)
    expected = [sample[-145], sample[-71], sample[-65], sample[-1]]
    assert dates == expected and dates[2:] == ["2025-09-30", "2025-12-31"]
    assert plan["profile"] == profile and len(scored_days) == 75
    assert config["simulation_config"]["rebalance_anchor_session"] == sample[-170]
    assert config["window"]["warmup_observations"] == 25
    assert config["window"]["split_hash"] == content_hash(
        "quantgpt.multisecurity_study/v2", ["fixture", expected, SELECTION_RULE])


@pytest.mark.parametrize(("options", "sizes"), [
    (["--selection-sessions", "75"], (75, 252)),
    (["--holdout-sessions", "65"], (756, 65)),
])
def test_single_real_override_keeps_other_daily_default(sessions, options, sizes):
    config, _, _, plan = study_config(arguments(*options), snapshot(sessions), sessions)
    assert (plan["selection_sessions"], plan["holdout_sessions"]) == sizes
    assert plan["profile"] == "explicit_sessions" and config["window"]["warmup_observations"] == 30


@pytest.mark.parametrize(("demo", "gap"), [(True, -150), (False, -66)])
def test_registered_snapshot_requires_prices_during_warmup_and_purge(sessions, tmp_path, demo, gap):
    if demo:
        args = arguments(demo=True)
        days = pd.bdate_range("2025-01-02", periods=180).strftime("%Y-%m-%d").tolist()
    else:
        args = arguments("--selection-sessions", "75", "--holdout-sessions", "65")
        days = sessions[-180:]
    missing = days[gap]
    registered = registered_snapshot(tmp_path, days, [day for day in days if day != missing])
    with pytest.raises(ValueError, match=f"SNAPSHOT_SESSION_GAP: {missing}"):
        study_config(args, registered, days, snapshot_root=tmp_path)


def test_missing_final_published_session_cannot_move_holdout_back(sessions, tmp_path):
    days = sessions[-180:]
    registered = registered_snapshot(tmp_path, days, days[:-1])
    args = arguments("--selection-sessions", "75", "--holdout-sessions", "65")
    with pytest.raises(ValueError, match=f"SNAPSHOT_SESSION_GAP: {days[-1]}"):
        study_config(args, registered, days, snapshot_root=tmp_path)


def test_frozen_query_bounds_clip_longer_published_calendar(sessions, tmp_path):
    declared = sessions[-200:]
    covered = declared[10:-10]
    registered = registered_snapshot(tmp_path, declared, covered, query={"from": covered[0], "to": covered[-1]})
    args = arguments("--selection-sessions", "75", "--holdout-sessions", "65")
    _, dates, scored, plan = study_config(args, registered, declared, snapshot_root=tmp_path)
    assert plan["status"] == "ready" and plan["available_sessions"] == len(covered)
    assert plan["warmup_start"] == covered[-170] and dates[-1] == covered[-1]
    assert scored == covered[-145:-70]
