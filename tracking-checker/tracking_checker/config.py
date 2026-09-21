"""Settings (non-secret, JSON in %APPDATA%) and secrets (Windows Credential Manager via keyring).

Every secret can also come from an environment variable, which wins over the stored value:
    TC_UPS_CLIENT_ID / TC_UPS_CLIENT_SECRET
    TC_FEDEX_CLIENT_ID / TC_FEDEX_CLIENT_SECRET
    TC_USPS_CLIENT_ID / TC_USPS_CLIENT_SECRET
    ANTHROPIC_API_KEY
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

KEYRING_SERVICE = "TrackingChecker"

SECRET_NAMES = {
    "ups_client_id": "TC_UPS_CLIENT_ID",
    "ups_client_secret": "TC_UPS_CLIENT_SECRET",
    "fedex_client_id": "TC_FEDEX_CLIENT_ID",
    "fedex_client_secret": "TC_FEDEX_CLIENT_SECRET",
    "usps_client_id": "TC_USPS_CLIENT_ID",
    "usps_client_secret": "TC_USPS_CLIENT_SECRET",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}


def config_dir() -> Path:
    base = os.environ.get("TC_CONFIG_DIR") or os.path.join(os.environ.get("APPDATA", str(Path.home())), "TrackingChecker")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace_dir() -> Path:
    base = os.environ.get("TC_WORKSPACE") or str(Path.home() / "TrackingChecker")
    p = Path(base)
    for sub in ("logs", "cache", "backups", "pod"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def browser_profile_dir() -> Path:
    """Edge profile used for website lookups - keeps the user's UPS/FedEx sign-ins between runs."""
    base = os.environ.get("TC_BROWSER_PROFILE") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home())), "TrackingChecker", "browser-profile")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


AUTO, API, WEBSITE = "auto", "api", "website"


@dataclass
class CarrierSettings:
    method: str = AUTO                   # "auto" (API when keys are set, else website) | "api" | "website"
    environment: str = "production"      # "production" | "sandbox"
    account_number: str = ""             # UPS shipper # / FedEx account # - needed for signature POD
    requests_per_minute: float = 60.0
    enabled: bool = True


@dataclass
class Settings:
    ups: CarrierSettings = field(default_factory=lambda: CarrierSettings(requests_per_minute=120))
    fedex: CarrierSettings = field(default_factory=lambda: CarrierSettings(requests_per_minute=60))
    # USPS gives new apps 60 calls/hour by default; raise this after USPS approves a quota increase.
    usps: CarrierSettings = field(default_factory=lambda: CarrierSettings(requests_per_minute=1))
    claude_model: str = "claude-opus-5"
    claude_enabled: bool = True
    google_auth_mode: str = "oauth"      # "oauth" | "service_account"
    google_credentials_file: str = ""    # OAuth client JSON or service-account key JSON
    google_upload_pod_to_drive: bool = True
    google_drive_folder_id: str = ""
    web_show_browser: bool = True        # visible Edge window: far less likely to be blocked, lets you clear checks
    web_seconds_between: float = 4.0     # pause between website lookups
    web_timeout_seconds: int = 45
    status_tab_name: str = "Tracking Status"
    download_pod: bool = True
    reuse_delivered: bool = True
    prn_tab_name: str = "PRN Status"
    prn_write_back: bool = True          # add PRN Status / Pieces / Tracking # columns to the original tab
    last_file: str = ""
    last_gsheet_url: str = ""

    # ------------------------------------------------------------ persistence
    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        p = cls.path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return s
            for k, v in data.items():
                if k in ("ups", "fedex", "usps") and isinstance(v, dict):
                    cs = getattr(s, k)
                    for kk, vv in v.items():
                        if hasattr(cs, kk):
                            setattr(cs, kk, vv)
                elif hasattr(s, k):
                    setattr(s, k, v)
        return s

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def carrier(self, name: str) -> CarrierSettings:
        return getattr(self, name.lower())


# ---------------------------------------------------------------- secrets
def _keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def get_secret(name: str) -> str:
    env = SECRET_NAMES.get(name)
    if env and os.environ.get(env):
        return os.environ[env].strip()
    kr = _keyring()
    if kr is None:
        return ""
    try:
        return (kr.get_password(KEYRING_SERVICE, name) or "").strip()
    except Exception:
        return ""


def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    if kr is None:
        raise RuntimeError("The 'keyring' package is not installed; cannot store secrets.")
    value = (value or "").strip()
    if value:
        kr.set_password(KEYRING_SERVICE, name, value)
    else:
        try:
            kr.delete_password(KEYRING_SERVICE, name)
        except Exception:
            pass


def carrier_credentials(carrier: str) -> tuple[str, str]:
    c = carrier.lower()
    return get_secret(f"{c}_client_id"), get_secret(f"{c}_client_secret")


def carrier_configured(carrier: str) -> bool:
    cid, sec = carrier_credentials(carrier)
    return bool(cid and sec)


def lookup_method(carrier: str, settings: "Settings") -> str:
    """Resolve 'auto' to API or WEBSITE for one carrier."""
    m = settings.carrier(carrier).method
    if m == AUTO:
        return API if carrier_configured(carrier) else WEBSITE
    return m
