import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep tests away from the real %APPDATA% settings, workspace and stored API keys."""
    monkeypatch.setenv("TC_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TC_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("TC_BROWSER_PROFILE", str(tmp_path / "browser"))
    for k in ("TC_UPS_CLIENT_ID", "TC_UPS_CLIENT_SECRET", "TC_FEDEX_CLIENT_ID", "TC_FEDEX_CLIENT_SECRET",
              "TC_USPS_CLIENT_ID", "TC_USPS_CLIENT_SECRET", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from tracking_checker import config
    monkeypatch.setattr(config, "_keyring", lambda: None)
