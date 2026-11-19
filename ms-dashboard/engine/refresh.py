"""Rebuild the Managed Services dashboard from whatever is in the 'Drop files here' folder.

  refresh.py            rebuild once and open the result in the default browser
  refresh.py --watch    keep running; rebuild whenever a file in the drop folder changes
  refresh.py --no-open  rebuild without opening the browser
"""
import sys, os, json, time, glob, shutil, zipfile, tempfile, datetime, webbrowser
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
from build import build

APP = os.path.dirname(ENGINE)
DROP = os.path.join(APP, "Drop files here")
OUT = os.path.join(APP, "Managed Services Dashboard.html")
HIST = os.path.join(APP, "history")
LOG = os.path.join(APP, "last refresh log.txt")
KEEP_HISTORY = 30


def refresh(open_browser=True):
    tmp = tempfile.mkdtemp(prefix="msdash-")
    lines = []
    def say(s): print(s); lines.append(s)
    try:
        roots = [DROP]
        for z in glob.glob(os.path.join(DROP, "**", "*.zip"), recursive=True):
            dest = os.path.join(tmp, os.path.splitext(os.path.basename(z))[0])
            with zipfile.ZipFile(z) as zf:
                for m in zf.namelist():   # refuse paths that would escape the temp folder
                    if os.path.isabs(m) or ".." in m.replace("\\", "/").split("/"): raise ValueError(f"unsafe path in {z}: {m}")
                zf.extractall(dest)
                for info in zf.infolist():   # keep each file's own date so "newest workbook wins" compares real edit dates
                    p = os.path.join(dest, info.filename)
                    if os.path.isfile(p):
                        t = time.mktime(info.date_time + (0, 0, -1)); os.utime(p, (t, t))
            roots.append(dest); say(f"Unzipped {os.path.basename(z)}")
        data = build(roots)
        m = data["meta"]
        tpl = open(os.path.join(ENGINE, "template.html"), encoding="utf-8").read()
        blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
        html = tpl.replace("/*DATA*/", blob)
        with open(OUT, "w", encoding="utf-8") as f: f.write(html)
        os.makedirs(HIST, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
        shutil.copyfile(OUT, os.path.join(HIST, f"Managed Services Dashboard {stamp}.html"))
        for old in sorted(glob.glob(os.path.join(HIST, "*.html")))[:-KEEP_HISTORY]: os.remove(old)
        rev = sum(x["rev"] for x in data["finMonthly"]); pl = sum(x["pl"] for x in data["finMonthly"])
        say(f"Built {m['built']} from {m['workbook']}")
        say(f"  Months: {m['months'][0]}–{m['months'][-1]} {m['year']}   Revenue ${rev:,.0f}   P&L ${pl:,.0f}")
        say(f"  Banks: {len(data['cust'])}   Ticket exports: {m.get('tkMonth','none')} ({len(data['tickets'])} tickets)   Time studies: {m['studyCount']}")
        for w in m["warnings"]: say(f"  WARNING: {w}")
        say(f"Saved: {OUT}")
        if open_browser: webbrowser.open("file:///" + OUT.replace("\\", "/"))
        return True
    except SystemExit as e:
        say(f"ERROR: {e}"); return False
    except Exception as e:
        import traceback; say("ERROR: refresh failed\n" + traceback.format_exc()); return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        with open(LOG, "w", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")


def signature():
    sig = []
    for f in glob.glob(os.path.join(DROP, "**", "*"), recursive=True):
        if os.path.isfile(f) and not os.path.basename(f).startswith("~$"):
            st = os.stat(f); sig.append((f, st.st_mtime, st.st_size))
    return sorted(sig)


if __name__ == "__main__":
    os.makedirs(DROP, exist_ok=True)
    if "--watch" in sys.argv:
        print(f"Watching {DROP}\nThe dashboard rebuilds a few seconds after files change. Close this window to stop.\n")
        refresh(open_browser=True); last = signature(); pending = None
        while True:
            time.sleep(5); now = signature()
            if now != last:
                pending, last = now, now          # wait for copying to finish
            elif pending is not None:
                print(f"\nChange detected {datetime.datetime.now():%H:%M:%S}, rebuilding…")
                refresh(open_browser=False); pending = None
                print("Reload the dashboard tab in your browser to see the update.")
    else:
        ok = refresh(open_browser="--no-open" not in sys.argv)
        sys.exit(0 if ok else 1)
