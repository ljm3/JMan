"""Google Sheets adapter (gspread + Sheets API batchUpdate).

Auth modes (Settings -> Google):
  oauth            - an OAuth "Desktop app" client JSON; a browser opens once to sign in and the
                     token is cached in %APPDATA%\\TrackingChecker\\google_token.json
  service_account  - a service-account key JSON; share the spreadsheet with the service
                     account's e-mail address (Editor)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from ..config import config_dir, workspace_dir
from ..report import OutCell

log = logging.getLogger("tracking_checker")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]
_URL_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def spreadsheet_id(url_or_id: str) -> str:
    m = _URL_RE.search(url_or_id or "")
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]{25,}", (url_or_id or "").strip()):
        return url_or_id.strip()
    raise ValueError("That doesn't look like a Google Sheets link (expected .../spreadsheets/d/<id>/...).")


def credentials(settings):
    path = settings.google_credentials_file
    if not path or not Path(path).exists():
        raise RuntimeError("Google credentials file not set - choose it in Settings -> Google Sheets.")
    if settings.google_auth_mode == "service_account":
        from google.oauth2.service_account import Credentials
        return Credentials.from_service_account_file(path, scopes=SCOPES)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token = config_dir() / "google_token.json"
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:  # noqa: BLE001 - fall through to a fresh sign-in
            creds = None
    if not creds or not creds.valid:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


class GoogleWorkbook:
    kind = "gsheet"

    def __init__(self, url: str, settings):
        import gspread

        self.settings = settings
        self.key = spreadsheet_id(url)
        self.creds = credentials(settings)
        self.gc = gspread.authorize(self.creds)
        self.sh = self.gc.open_by_key(self.key)
        self.display_name = self.sh.title
        self.url = f"https://docs.google.com/spreadsheets/d/{self.key}/edit"
        self._drive_folder: str | None = None
        self._drive_failed = ""
        self._pending: list[dict] = []

    def tab_names(self) -> list[str]:
        return [ws.title for ws in self.sh.worksheets()]

    def read_tab(self, name: str) -> list[list]:
        return self.sh.worksheet(name).get_all_values()

    def begin_write(self) -> list[str]:
        return []

    # ------------------------------------------------------------ POD files
    def pod_folder(self) -> Path:
        safe = re.sub(r"[^A-Za-z0-9 _.-]", "_", self.display_name)[:60]
        return workspace_dir() / "pod" / f"{safe} ({self.key[:8]})"

    def pod_link(self, path: Path) -> str:
        if not self.settings.google_upload_pod_to_drive or self._drive_failed:
            return ""
        try:
            return self._upload(Path(path))
        except Exception as e:  # noqa: BLE001
            self._drive_failed = str(e)
            log.warning("Drive upload failed (%s). POD files stay local in %s; the sheet shows the file path instead.",
                        e, self.pod_folder())
            return ""

    def _session(self):
        from google.auth.transport.requests import AuthorizedSession
        return AuthorizedSession(self.creds)

    def _folder(self, s) -> str:
        if self._drive_folder:
            return self._drive_folder
        if self.settings.google_drive_folder_id:
            self._drive_folder = self.settings.google_drive_folder_id
            return self._drive_folder
        name = f"Tracking Check POD - {self.display_name}"
        q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        r = s.get("https://www.googleapis.com/drive/v3/files", params={"q": q, "fields": "files(id)",
                                                                       "supportsAllDrives": "true",
                                                                       "includeItemsFromAllDrives": "true"})
        r.raise_for_status()
        files = r.json().get("files") or []
        if files:
            self._drive_folder = files[0]["id"]
        else:
            r = s.post("https://www.googleapis.com/drive/v3/files", params={"supportsAllDrives": "true"},
                       json={"name": name, "mimeType": "application/vnd.google-apps.folder"})
            r.raise_for_status()
            self._drive_folder = r.json()["id"]
        return self._drive_folder

    def _upload(self, path: Path) -> str:
        import mimetypes
        s = self._session()
        folder = self._folder(s)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        q = f"name = '{path.name}' and '{folder}' in parents and trashed = false"
        r = s.get("https://www.googleapis.com/drive/v3/files", params={"q": q, "fields": "files(id,webViewLink)",
                                                                       "supportsAllDrives": "true",
                                                                       "includeItemsFromAllDrives": "true"})
        r.raise_for_status()
        existing = r.json().get("files") or []
        boundary = "tc-boundary-7d1f"
        meta = {"name": path.name} if existing else {"name": path.name, "parents": [folder]}
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--".encode()
        url = "https://www.googleapis.com/upload/drive/v3/files"
        params = {"uploadType": "multipart", "fields": "id,webViewLink", "supportsAllDrives": "true"}
        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        if existing:
            r = s.patch(f"{url}/{existing[0]['id']}", params=params, data=body, headers=headers)
        else:
            r = s.post(url, params=params, data=body, headers=headers)
        r.raise_for_status()
        return r.json().get("webViewLink") or f"https://drive.google.com/file/d/{r.json()['id']}/view"

    # ------------------------------------------------------------ writing
    def _gid(self, title: str) -> int:
        return self.sh.worksheet(title).id

    def write_status_tab(self, name: str, headers: list[str], rows: list[list[OutCell]], widths: dict[str, int],
                         header_notes: dict[str, str] | None = None) -> None:
        import gspread

        nrows, ncols = len(rows) + 1, len(headers)
        try:
            ws = self.sh.worksheet(name)
            ws.clear()
            ws.resize(rows=max(nrows, 2), cols=ncols)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=name, rows=max(nrows, 2), cols=ncols)
        grid = [headers] + [[_gs(c.value) for c in row] for row in rows]
        ws.update(values=grid, range_name="A1", value_input_option="RAW")
        gid = ws.id
        req = [
            {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": _rgb("FFFFFF")},
                                                           "backgroundColor": _rgb("1F4E78"), "wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat(textFormat,backgroundColor,wrapStrategy)"}},
            {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1,
                                                                                          "frozenColumnCount": 1}},
                                       "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": nrows,
                                                     "startColumnIndex": 0, "endColumnIndex": ncols}}}},
        ]
        for c, h in enumerate(headers):
            if header_notes and h in header_notes:
                req.append({"repeatCell": {"range": _cell_range(gid, 0, c), "cell": {"note": header_notes[h][:2000]},
                                           "fields": "note"}})
            req.append({"updateDimensionProperties": {
                "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
                "properties": {"pixelSize": int(widths.get(h, 16) * 7.5)}, "fields": "pixelSize"}})
        tab_gids = {}
        for r, row in enumerate(rows, 1):
            for c, oc in enumerate(row):
                uri = oc.url
                if not uri and oc.goto:
                    tab, gr, gc = oc.goto
                    if tab not in tab_gids:
                        tab_gids[tab] = self._gid(tab)
                    uri = f"#gid={tab_gids[tab]}&range={_a1(gr, gc)}"
                fmt, fields = {}, []
                if uri and uri.startswith(("http", "#")):
                    fmt["textFormat"] = {"link": {"uri": uri}, "underline": True, "foregroundColor": _rgb("0563C1")}
                    fields.append("userEnteredFormat.textFormat")
                if oc.fill:
                    fmt["backgroundColor"] = _rgb(oc.fill)
                    fields.append("userEnteredFormat.backgroundColor")
                if fmt:
                    req.append({"repeatCell": {"range": _cell_range(gid, r, c), "cell": {"userEnteredFormat": fmt},
                                               "fields": ",".join(fields)}})
        self._pending = req
        self._status_gid = gid

    def link_source_cells(self, src_tab: str, links: list[tuple[int, int, int]], status_tab: str) -> int:
        src_gid = self._gid(src_tab)
        for row, col, target_row in links:
            self._pending.append({"repeatCell": {
                "range": _cell_range(src_gid, row - 1, col - 1),
                "cell": {"userEnteredFormat": {"textFormat": {
                    "link": {"uri": f"#gid={self._status_gid}&range=A{target_row}"},
                    "underline": True, "foregroundColor": _rgb("0563C1")}}},
                "fields": "userEnteredFormat.textFormat.link,userEnteredFormat.textFormat.underline,"
                          "userEnteredFormat.textFormat.foregroundColor"}})
        return len(links)

    def save(self) -> str:
        for i in range(0, len(self._pending), 500):
            self.sh.batch_update({"requests": self._pending[i:i + 500]})
        self._pending = []
        return self.url


def _cell_range(gid: int, r0: int, c0: int) -> dict:
    return {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r0 + 1, "startColumnIndex": c0, "endColumnIndex": c0 + 1}


def _rgb(hex_: str) -> dict:
    return {"red": int(hex_[0:2], 16) / 255, "green": int(hex_[2:4], 16) / 255, "blue": int(hex_[4:6], 16) / 255}


def _a1(row: int, col: int) -> str:
    s, n = "", col
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return f"{s}{row}"


def _gs(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str) and len(v) > 49000:
        return v[:49000]
    return v
