from __future__ import annotations

import importlib
import os
import platform
import sys

from . import __version__, checks, config
from .models import SUPPORTED_CARRIERS


def doctor() -> int:
    print(f"Tracking Check {__version__}")
    print(f"Python      {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform    {platform.platform()}")
    print(f"Settings    {config.Settings.path()}")
    print(f"Workspace   {config.workspace_dir()}  (logs, backups, cache)")
    print()
    print("Libraries")
    bad = 0
    for mod, need in (("openpyxl", True), ("requests", True), ("keyring", True), ("dateutil", True),
                      ("PIL", False), ("gspread", False), ("google.auth", False), ("google_auth_oauthlib", False),
                      ("anthropic", False), ("playwright", False), ("tkinter", False)):
        try:
            m = importlib.import_module(mod)
            print(f"  ok       {mod:22} {getattr(m, '__version__', getattr(m, 'TkVersion', ''))}")
        except ImportError as e:
            bad += need
            print(f"  {'MISSING' if need else 'missing'}  {mod:22} {e}")
    try:
        import keyring
        print(f"  keyring backend: {keyring.get_keyring().__class__.__name__}")
    except Exception:  # noqa: BLE001
        pass
    s = config.Settings.load()
    print()
    print("Carriers")
    for c in SUPPORTED_CARRIERS:
        cs = s.carrier(c)
        state = "keys set" if config.carrier_configured(c) else "no keys"
        print(f"  {c:6} lookup={config.lookup_method(c, s):8} ({cs.method}) {state:9} env={cs.environment:10} "
              f"account={'set' if cs.account_number else '-':4} rate={cs.requests_per_minute}/min enabled={cs.enabled}")
    edge = [p for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")) if os.path.exists(p)]
    print(f"  Website lookups: browser={'Edge' if edge else 'Edge NOT found'} visible={s.web_show_browser} "
          f"pace={s.web_seconds_between}s profile={config.browser_profile_dir()}")
    print()
    print(f"Extra checks  {checks.engine_label(s)}")
    gf = s.google_credentials_file
    print(f"Google        mode={s.google_auth_mode} credentials={'set' if gf else 'not set'}"
          f"{' (file missing!)' if gf and not os.path.exists(gf) else ''}")
    return 1 if bad else 0
