from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, config
from .models import FEDEX, UPS, USPS

HELP = {
    UPS: ("developer.ups.com -> Apps -> Add App -> product 'Tracking'. Client ID + Client Secret are on the app page.\n"
          "Shipper/account number: required for signature images and the UPS POD letter on your own shipments."),
    FEDEX: ("developer.fedex.com -> My Projects -> Create API Project -> 'Track API'. Use the Production key once "
            "the project is approved.\nAccount number: required for the Signature Proof of Delivery PDF."),
    USPS: ("developers.usps.com -> Apps -> Add App (Consumer Key = Client ID, Consumer Secret = Client Secret).\n"
           "New apps get about 60 requests per HOUR, so keep 1 request/minute until USPS raises your quota."),
}


_METHOD_LABELS = {
    config.AUTO: "Automatic - API when keys are set, otherwise website",
    config.API: "API only (needs the keys above)",
    config.WEBSITE: "Website only (carrier's tracking page)",
}
_METHOD_BY_LABEL = {v: k for k, v in _METHOD_LABELS.items()}


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings: config.Settings, on_save=None):
        super().__init__(parent)
        self.title(f"{APP_NAME} - Settings")
        self.settings = settings
        self.on_save = on_save
        self.resizable(True, False)
        self.transient(parent)
        self.vars: dict[str, tk.Variable] = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        for carrier in (UPS, FEDEX, USPS):
            nb.add(self._carrier_tab(nb, carrier), text=carrier)
        nb.add(self._web_tab(nb), text="Website lookups")
        nb.add(self._claude_tab(nb), text="Claude (extra checks)")
        nb.add(self._google_tab(nb), text="Google Sheets")
        ttk.Label(self, text=f"API keys are stored in Windows Credential Manager, never in the app folder.\n"
                             f"Other settings: {config.Settings.path()}", foreground="#666", wraplength=700,
                  justify="left").pack(fill="x", padx=12)
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bar, text="Save", command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self.geometry("800x500")
        self.grab_set()

    def _entry(self, parent, row, label, key, value, secret=False, width=52):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        v = tk.StringVar(value=value)
        self.vars[key] = v
        e = ttk.Entry(parent, textvariable=v, width=width, show="•" if secret else "")
        e.grid(row=row, column=1, sticky="ew", padx=8, pady=4)
        from .gui import enable_clipboard
        enable_clipboard(e)
        return e

    def _carrier_tab(self, nb, carrier):
        f = ttk.Frame(nb)
        c = carrier.lower()
        cs = self.settings.carrier(carrier)
        cid, sec = config.carrier_credentials(carrier)
        ttk.Label(f, text="Lookup method:").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        v = tk.StringVar(value=_METHOD_LABELS[cs.method])
        self.vars[f"{c}.method"] = v
        ttk.Combobox(f, textvariable=v, values=list(_METHOD_LABELS.values()), state="readonly", width=48
                     ).grid(row=0, column=1, sticky="w", padx=8, pady=(8, 4))
        ttk.Label(f, text="API keys (optional when using the website):", foreground="#1f4e78"
                  ).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 0))
        self._entry(f, 2, "Client ID / API key:", f"{c}_client_id", cid)
        self._entry(f, 3, "Client secret:", f"{c}_client_secret", sec, secret=True)
        if carrier != USPS:
            self._entry(f, 4, "Account / shipper number:", f"{c}.account_number", cs.account_number, width=24)
        ttk.Label(f, text="Environment:").grid(row=5, column=0, sticky="w", padx=8, pady=4)
        v = tk.StringVar(value=cs.environment)
        self.vars[f"{c}.environment"] = v
        ttk.Combobox(f, textvariable=v, values=["production", "sandbox"], state="readonly", width=14
                     ).grid(row=5, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Max API requests per minute:").grid(row=6, column=0, sticky="w", padx=8, pady=4)
        v = tk.StringVar(value=str(cs.requests_per_minute))
        self.vars[f"{c}.requests_per_minute"] = v
        ttk.Entry(f, textvariable=v, width=8).grid(row=6, column=1, sticky="w", padx=8)
        v = tk.BooleanVar(value=cs.enabled)
        self.vars[f"{c}.enabled"] = v
        ttk.Checkbutton(f, text=f"Use {carrier}", variable=v).grid(row=7, column=1, sticky="w", padx=8)
        ttk.Label(f, text=HELP[carrier], foreground="#555", wraplength=620, justify="left"
                  ).grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        lbl = ttk.Label(f, text="")
        ttk.Button(f, text="Test API keys", command=lambda: self._test(carrier, lbl)
                   ).grid(row=9, column=0, sticky="w", padx=8, pady=(0, 10))
        lbl.grid(row=9, column=1, sticky="w", padx=8, pady=(0, 10))
        f.columnconfigure(1, weight=1)
        return f

    def _web_tab(self, nb):
        f = ttk.Frame(nb)
        s = self.settings
        v = tk.BooleanVar(value=s.web_show_browser)
        self.vars["web_show_browser"] = v
        ttk.Checkbutton(f, text="Show the browser window while checking (recommended)", variable=v
                        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(f, text="Sites block hidden browsers far more often, and a visible window lets you clear any "
                          "'are you human' check.", foreground="#555"
                  ).grid(row=1, column=0, columnspan=2, sticky="w", padx=28, pady=(0, 6))
        self._entry(f, 2, "Seconds between lookups:", "web_seconds_between", str(s.web_seconds_between), width=8)
        self._entry(f, 3, "Page timeout (seconds):", "web_timeout_seconds", str(s.web_timeout_seconds), width=8)
        bar = ttk.Frame(f)
        bar.grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        from .gui import sign_in
        ttk.Button(bar, text="Sign in to UPS / FedEx...", command=lambda: sign_in(self, lambda m: None)
                   ).pack(side="left")
        ttk.Button(bar, text="Forget saved sign-ins", command=self._clear_profile).pack(side="left", padx=8)
        ttk.Label(f, text=(
            "Website lookups read each carrier's public tracking page in an Edge window - no API keys needed.\n"
            "• UPS and USPS: status, full history, delivery details; POD = a PDF capture of the carrier's page. "
            "Signed in to ups.com, UPS also shows the signature and its Proof of Delivery letter.\n"
            "• FedEx currently blocks automated lookups on fedex.com, so FedEx numbers need the (free) FedEx API "
            "keys - with 'Automatic' they are used as soon as you add them.\n"
            "• USPS never shows signatures online.\n"
            "Websites change without notice; when a page can't be read the app saves a debug snapshot in the logs "
            "folder. Automated use may be restricted by the carriers' website terms."),
            foreground="#555", wraplength=700, justify="left").grid(row=5, column=0, columnspan=2, sticky="w",
                                                                    padx=8, pady=6)
        f.columnconfigure(1, weight=1)
        return f

    def _clear_profile(self):
        import shutil
        if not messagebox.askyesno(APP_NAME, "Sign out of UPS/FedEx in the app's browser and clear its saved data?",
                                   parent=self):
            return
        try:
            shutil.rmtree(config.browser_profile_dir())
            messagebox.showinfo(APP_NAME, "Saved sign-ins cleared.", parent=self)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Couldn't clear it (is the browser open?): {e}", parent=self)

    def _claude_tab(self, nb):
        f = ttk.Frame(nb)
        self._entry(f, 0, "Anthropic API key:", "anthropic_api_key", config.get_secret("anthropic_api_key"), secret=True)
        self._entry(f, 1, "Model:", "claude_model", self.settings.claude_model, width=24)
        v = tk.BooleanVar(value=self.settings.claude_enabled)
        self.vars["claude_enabled"] = v
        ttk.Checkbutton(f, text="Use Claude for the 'anything specific to check?' box when a key is set",
                        variable=v).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Get a key at console.anthropic.com -> API keys. Without a key, the built-in rules handle "
                          "common requests offline. Cost is typically a few cents per run.",
                  foreground="#555", wraplength=620, justify="left").grid(row=3, column=0, columnspan=2, sticky="w",
                                                                          padx=8, pady=8)
        f.columnconfigure(1, weight=1)
        return f

    def _google_tab(self, nb):
        f = ttk.Frame(nb)
        ttk.Label(f, text="Sign-in method:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        v = tk.StringVar(value=self.settings.google_auth_mode)
        self.vars["google_auth_mode"] = v
        ttk.Radiobutton(f, text="My Google account (OAuth desktop client JSON)", variable=v, value="oauth"
                        ).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Radiobutton(f, text="Service account (key JSON; share the sheet with its e-mail)", variable=v,
                        value="service_account").grid(row=1, column=1, sticky="w", padx=8)
        e = self._entry(f, 2, "Credentials JSON file:", "google_credentials_file", self.settings.google_credentials_file)
        ttk.Button(f, text="Browse...", command=lambda: self._pick_json("google_credentials_file")
                   ).grid(row=2, column=2, padx=(0, 8))
        v = tk.BooleanVar(value=self.settings.google_upload_pod_to_drive)
        self.vars["google_upload_pod_to_drive"] = v
        ttk.Checkbutton(f, text="Upload POD files to Google Drive so the sheet's links work for everyone",
                        variable=v).grid(row=3, column=1, sticky="w", padx=8)
        self._entry(f, 4, "Drive folder ID (optional):", "google_drive_folder_id", self.settings.google_drive_folder_id)
        ttk.Label(f, text="See docs/CARRIER_API_SETUP.md -> Google Sheets for the 5-minute setup. With a service "
                          "account, Drive uploads need a Shared Drive folder ID (service accounts have no storage of "
                          "their own); otherwise POD files stay on this PC.",
                  foreground="#555", wraplength=620, justify="left").grid(row=5, column=0, columnspan=3, sticky="w",
                                                                          padx=8, pady=8)
        f.columnconfigure(1, weight=1)
        return f

    def _pick_json(self, key):
        p = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.vars[key].set(p)

    def _apply(self):
        s = self.settings
        for carrier in (UPS, FEDEX, USPS):
            c = carrier.lower()
            cs = s.carrier(carrier)
            cs.method = _METHOD_BY_LABEL[self.vars[f"{c}.method"].get()]
            cs.environment = self.vars[f"{c}.environment"].get()
            cs.enabled = bool(self.vars[f"{c}.enabled"].get())
            if f"{c}.account_number" in self.vars:
                cs.account_number = self.vars[f"{c}.account_number"].get().strip()
            try:
                cs.requests_per_minute = max(0.1, float(self.vars[f"{c}.requests_per_minute"].get()))
            except ValueError:
                pass
            for k in ("client_id", "client_secret"):
                config.set_secret(f"{c}_{k}", self.vars[f"{c}_{k}"].get())
        s.web_show_browser = bool(self.vars["web_show_browser"].get())
        for key, lo in (("web_seconds_between", 1.0), ("web_timeout_seconds", 15)):
            try:
                setattr(s, key, type(getattr(s, key))(max(lo, float(self.vars[key].get()))))
            except ValueError:
                pass
        config.set_secret("anthropic_api_key", self.vars["anthropic_api_key"].get())
        s.claude_model = self.vars["claude_model"].get().strip() or "claude-opus-5"
        s.claude_enabled = bool(self.vars["claude_enabled"].get())
        s.google_auth_mode = self.vars["google_auth_mode"].get()
        s.google_credentials_file = self.vars["google_credentials_file"].get().strip().strip('"')
        s.google_upload_pod_to_drive = bool(self.vars["google_upload_pod_to_drive"].get())
        s.google_drive_folder_id = self.vars["google_drive_folder_id"].get().strip()
        s.save()

    def _save(self):
        try:
            self._apply()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Couldn't save settings: {e}", parent=self)
            return
        if self.on_save:
            self.on_save()
        self.destroy()

    def _test(self, carrier, lbl):
        try:
            self._apply()
        except Exception as e:  # noqa: BLE001
            lbl.configure(text=str(e), foreground="#b42318")
            return
        lbl.configure(text="Testing ...", foreground="#555")

        def work():
            from . import carriers
            try:
                msg, ok = carriers.build(carrier, self.settings).test_connection(), True
            except Exception as e:  # noqa: BLE001
                msg, ok = str(e), False
            self.after(0, lambda: lbl.configure(text=msg, foreground="#1a7f37" if ok else "#b42318"))
        threading.Thread(target=work, daemon=True).start()
