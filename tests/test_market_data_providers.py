"""Optional market-data provider registry tests."""

from quantgpt.market_data_providers import (
    PROVIDER_UNAVAILABLE,
    OptionalProvider,
    ProviderRegistry,
    ProviderSpec,
    get_market_data_provider,
    list_market_data_providers,
)


def test_default_registry_lists_free_and_optional_paid_providers():
    providers = list_market_data_providers()
    names = {provider["name"] for provider in providers}

    assert {"local_cache", "baostock", "rqdatac", "tushare", "wind", "choice", "jqdata"} <= names
    assert all("credential_status" in provider for provider in providers)


def test_optional_paid_provider_reports_missing_credentials(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    provider = get_market_data_provider("tushare")
    status = provider.credential_status()
    result = provider.fetch_stocks(["000001.SZ"], "2024-01-01", "2024-01-31")

    assert status["credentials_configured"] is False
    assert status["missing_env_vars"] == ["TUSHARE_TOKEN"]
    assert result["error_code"] == PROVIDER_UNAVAILABLE
    assert result["reason"] == "CREDENTIALS_MISSING"


def test_provider_does_not_require_paid_sdk_import_at_registry_time(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "token")

    provider = get_market_data_provider("tushare")
    status = provider.credential_status()
    result = provider.fetch_fundamentals(["000001.SZ"])

    assert status["credentials_configured"] is True
    assert "sdk_available" in status
    assert result["error_code"] == PROVIDER_UNAVAILABLE
    assert result["reason"] in {"SDK_MISSING", "ADAPTER_NOT_IMPLEMENTED"}


def test_custom_provider_can_be_registered():
    registry = ProviderRegistry()
    provider = OptionalProvider(ProviderSpec(name="custom", capabilities=("ohlcv",)))

    registry.register(provider)

    assert registry.get("custom") is provider
    assert registry.list_providers()[0]["source_metadata"]["vendor"] == "custom"
