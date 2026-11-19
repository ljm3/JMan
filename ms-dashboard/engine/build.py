"""Reads the workbooks found in one or more folders and returns the dashboard data as a dict.

Nothing here is tied to a month count or a file name: the main workbook is found by its
"Managed Services Details" sheet, time studies by their "Time Study" sheet, and the synopsis
by its "Metrics-Overall" sheet. Each section is optional; a missing or changed sheet adds a
warning instead of stopping the refresh.

Privacy: requester names, end-customer company names, ticket subjects, serial numbers and
time-study free-text notes are never read into the output.
"""
import os, re, glob, collections, datetime, traceback
import pandas as pd, numpy as np, openpyxl

FULLM = ["January","February","March","April","May","June","July","August","September","October","November","December"]
ABBR = [m[:3] for m in FULLM]
ALIAS = {"wilbert nazario":"Wil Nazario","wnazario":"Wil Nazario","thaerol villanueva":"Tek Villanueva",
 "joe yarrow":"Joseph Yarrow","randall riviere":"Randy Riviere","uknown":"Unknown","ddomingo":"Dallas Domingo",
 "jlandino":"Josue Landino","j. landino":"Josue Landino","jouse landino":"Josue Landino","jrhodes":"Jarius Rhodes",
 "j. rhodes":"Jarius Rhodes","hicks":"Martin Hicks","mhicks":"Martin Hicks","m. hicks":"Martin Hicks",
 "tj grunfelder":"Thomas Grunfelder"}
# file-name fragments used to tell whose time study a workbook is
WHO = [("domingo","Dallas Domingo"),("landino","Josue Landino"),("rhodes","Jarius Rhodes"),("hicks","Martin Hicks"),
       ("nazario","Wil Nazario"),("tjg","Thomas Grunfelder"),("grunfelder","Thomas Grunfelder"),("tek v","Tek Villanueva"),
       ("villanueva","Tek Villanueva"),("kyle","Kyle Jennings"),("jennings","Kyle Jennings"),("yarrow","Joseph Yarrow")]
PROD = {"Support Inbound","Support Outbound","Project Inbound","Project Outbound","Consult"}
ROUNDW = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6}
PTO = ["VACATION","SICK","FLOAT","BEREAV"]

alias_hits = set()
def nm(x):
    if x is None or (isinstance(x, float) and np.isnan(x)): return "Unassigned"
    s = re.sub(r"\s+", " ", str(x)).strip()
    if not s: return "Unassigned"
    if s.lower() in ALIAS:
        alias_hits.add(f"{s} → {ALIAS[s.lower()]}")
        return ALIAS[s.lower()]
    return s

def r(x, n=2):
    try:
        if x is None or np.isnan(x): return 0
    except TypeError:
        pass
    try: return round(float(x), n)
    except (TypeError, ValueError): return 0

def clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return ""
    s = str(v).strip()
    return "" if s in ("—", "-", "–", "nan", "NaT") else s

def col(df, *names):
    """First column whose name matches one of the candidates (case/space-insensitive)."""
    norm = {re.sub(r"\s+", " ", str(c)).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in norm: return df[norm[n.lower()]]
    raise KeyError(f"none of the columns {names} found")

def sheetnames(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
        s = wb.sheetnames; wb.close(); return s
    except Exception:
        return []

def discover(roots):
    files = []
    for root in roots:
        files += [f for f in glob.glob(os.path.join(root, "**", "*.xlsx"), recursive=True)
                  if not os.path.basename(f).startswith("~$")]
    main, studies, syn = [], {}, []
    for f in files:
        sn = sheetnames(f)
        if "Managed Services Details" in sn: main.append(f)
        elif "Metrics-Overall" in sn: syn.append(f)
        elif "Time Study" in sn:
            b = os.path.basename(f)   # the same study dropped twice (zip + folder) counts once
            if b not in studies or os.path.getmtime(f) > os.path.getmtime(studies[b]): studies[b] = f
    newest = lambda L: max(L, key=os.path.getmtime) if L else None
    return newest(main), sorted(studies.values()), newest(syn), main


def build(roots):
    alias_hits.clear()
    warn = []
    out = {"meta": {}}
    def section(name, fn):
        try: fn()
        except Exception as e:
            warn.append(f"{name}: skipped ({type(e).__name__}: {e})")

    F, study_files, syn_file, all_main = discover(roots)
    if not F:
        raise SystemExit("No workbook with a 'Managed Services Details' sheet was found in the drop folder.")
    if len(all_main) > 1:
        warn.append(f"{len(all_main)} main workbooks found; used the newest: {os.path.basename(F)}")
    X = pd.ExcelFile(F)
    meta = out["meta"]

    # ---------- finance detail (required) ----------
    d = pd.read_excel(X, "Managed Services Details")
    d.columns = [str(c).strip() for c in d.columns]
    raw_type = col(d, "Support Type").astype(str)
    hw_space = int((raw_type.str.match(r"^\S.*\s+$") & raw_type.str.strip().eq("Hardware")).sum())
    d["Support Type"] = col(d, "Support Type").fillna("Unclassified").astype(str).str.strip()
    d["Assignee"] = col(d, "Assignee").map(nm)
    d["Organization"] = col(d, "Organization").fillna("").astype(str).str.strip()
    month = pd.to_datetime(col(d, "Month"), errors="coerce")
    d = d[month.notna()].copy(); month = month[month.notna()]
    year = int(month.dt.year.mode()[0])
    if (month.dt.year != year).any():
        warn.append(f"Detail rows from other years were left out (kept {year}).")
        d = d[month.dt.year == year].copy(); month = month[month.dt.year == year]
    d["mi"] = month.dt.month - 1
    cost_cols = {"emp":("Actual Employee Expenses (non-tech)",),"con":("Contractor Support",),
                 "tech":("Tecnician Support","Technician Support"),"oth":("Other Expenses",),"com":("Commissions",)}
    for k, c in cost_cols.items(): d[k] = col(d, *c).fillna(0)
    if len(d) and col(d, "Profit and Loss").isna().mean() > 0.9:
        warn.append("Profit and Loss is blank in most detail rows. The workbook was probably saved by a program that doesn't calculate formulas; open it in Excel, save, and refresh again.")
    d["rev"] = col(d, "Revenue").fillna(0); d["pl"] = col(d, "Profit and Loss").fillna(0)
    d["hrs"] = col(d, "Time Spent").fillna(0); d["lab"] = col(d, "Labor Costs").fillna(0)
    pooled = d.loc[d["Support Type"]=="Unallocated", ["mi","hrs"]].groupby("mi")["hrs"].sum()
    d.loc[d["Support Type"]=="Unallocated", "hrs"] = 0  # pooled worked hours, not ticket time
    agg = ["rev","emp","con","tech","oth","com","pl","hrs","lab"]
    g = d.groupby(["mi","Support Type"])[agg].sum().reset_index()
    out["finMonthly"] = [{"m":int(x.mi),"t":x["Support Type"],**{k:r(x[k]) for k in agg}} for _,x in g.iterrows()]
    out["pooledHours"] = {int(k): r(v) for k, v in pooled.items() if v}
    last = int(d["mi"].max())
    meta.update(year=year, months=ABBR[:last+1], monthsFull=FULLM[:last+1])

    c = d[d.Organization!=""].groupby(["Organization","mi","Support Type"]).agg(
        rev=("rev","sum"), pl=("pl","sum"), hrs=("hrs","sum"), dc=("tech","sum"), con=("con","sum"), n=("hrs","size")).reset_index()
    cust = collections.defaultdict(list)
    for _,x in c.iterrows():
        cust[x.Organization].append([int(x.mi), x["Support Type"], r(x.rev), r(x.pl), r(x.hrs), r(x.dc + x.con), int(x.n)])
    out["cust"] = cust
    t = d[d["Support Type"]!="Unallocated"].groupby(["Assignee","mi","Support Type"]).agg(
        rev=("rev","sum"), pl=("pl","sum"), hrs=("hrs","sum"), lab=("lab","sum"), n=("hrs","size")).reset_index()
    out["tech"] = [[x.Assignee,int(x.mi),x["Support Type"],r(x.rev),r(x.pl),r(x.hrs),r(x.lab),int(x.n)] for _,x in t.iterrows()]

    # defaults so the page renders even when an optional section is missing
    out.update(projects=[], ts=[], absence=[], people={}, resHours=[], resHdr=[], rates=[], tickets=[],
               sepProjects=[], studies=[], actions=[], synopsis={})

    def projects():
        ps = pd.read_excel(X, "Summary By customer YTD", header=None)
        hdr_row = next(i for i in range(min(10, len(ps))) if "Project" in [str(v).strip() for v in ps.iloc[i].tolist()])
        pc = [str(v).strip() for v in ps.iloc[hdr_row].tolist()].index("Project")
        for _, rw in ps.iloc[hdr_row+1:].iterrows():
            p, cd, yn = rw.iloc[pc], rw.iloc[pc+1], rw.iloc[pc+2]
            if isinstance(p, str) and p.strip():
                out["projects"].append({"p":p.strip(),"d":cd.strftime("%Y-%m-%d") if hasattr(cd,"strftime") else None,
                                        "done":str(yn).strip() if isinstance(yn,str) else ""})
    section("Project list (Summary By customer YTD)", projects)

    def timesheet():
        e = pd.read_excel(X, "Employee Hours by Day")
        e.columns = [re.sub(r"\s+"," ",str(c)).strip() for c in e.columns]
        e["Employee"] = e["Employee"].map(nm)
        mcol = e["Month"]
        e["mi"] = mcol.map(lambda m: m.month-1 if hasattr(m,"month") else FULLM.index(str(m).strip().title()) if str(m).strip().title() in FULLM else None)
        e = e[e["mi"].notna()].copy(); e["mi"] = e["mi"].astype(int)
        e["pt"] = e["Pay Type"].astype(str).str.split("-").str[0].str.strip().str.upper()
        pos = e.groupby("Employee")["Position"].agg(lambda s: s.mode()[0] if len(s.mode()) else "").to_dict()
        grp = e.groupby("Employee")["Group"].agg(lambda s: s.mode()[0] if len(s.mode()) else "").to_dict()
        tg = e.groupby(["Employee","mi","pt"]).agg(h=("Total Daily Decimal Hours","sum"), p=("Total Daily Productive Hours","sum"),
            bm=("Benchmark Total Hours","sum"), av=("Avivatech Total Hours","sum"), dc=("Digital Check Total Hours","sum"),
            days=("Date Day","nunique")).reset_index()
        out["ts"] = [[x.Employee,int(x.mi),x.pt,r(x.h),r(x.p),r(x.bm),r(x.av),r(x.dc),int(x.days)] for _,x in tg.iterrows()]
        ab = e[e.pt.isin(PTO)].groupby(["Employee","Day","pt"])["Total Daily Decimal Hours"].sum().reset_index()
        out["absence"] = [[x.Employee,x.Day,x.pt,r(x["Total Daily Decimal Hours"])] for _,x in ab.iterrows()]
        out["people"] = {k:{"pos":pos[k],"grp":grp[k]} for k in pos}
        meta["tsLast"] = int(e["mi"].max())
    section("Timesheet (Employee Hours by Day)", timesheet)

    def resource():
        ws = openpyxl.load_workbook(F, read_only=True, data_only=True)["Resource hours"]
        rows = list(ws.iter_rows(values_only=True))
        hdr = None; res = []; seen = set(); relabeled = []
        for i, rw in enumerate(rows):
            if len(rw) > 2 and rw[1] == "Time Period":
                hdr = [nm(v) if v else None for v in rw]; continue
            if hdr and len(rw) > 2 and isinstance(rw[1], str) and re.fullmatch(r"\d{4}-\d{4}", rw[1]) and rw[2] == "AVT" and i+1 < len(rows):
                nxt = rows[i+1]; lab = rw[1]; mi = int(lab[5:7]) - 1
                if lab in seen:            # a week label used twice belongs to the following month
                    mi += 1; relabeled.append(lab)
                seen.add(lab)
                for j in range(3, min(len(hdr), len(rw), len(nxt))):
                    if hdr[j] and hdr[j] not in ("Total","Totals","Percentages") and isinstance(rw[j],(int,float)) and isinstance(nxt[j],(int,float)) and (rw[j] or nxt[j]):
                        res.append([hdr[j], mi, r(rw[j]), r(nxt[j])])
        out["resHours"] = res
        out["resHdr"] = [h for h in hdr if h] if hdr else []
        meta["resLast"] = max((x[1] for x in res), default=None)
        meta["resRelabeled"] = len(relabeled)
    section("Resource hours", resource)

    def rates():
        rt = pd.read_excel(X, "rates").dropna(how="all")
        rt["Support Type"] = rt["Support Type"].astype(str).str.strip(); rt["Organization"] = rt["Organization"].astype(str).str.strip()
        out["rates"] = [[x.Organization, x["Support Type"], r(x.Rate)] for _,x in rt.iterrows() if not pd.isna(x.Rate)]
    section("Billing rates (rates)", rates)

    # ---------- monthly ticket exports: the latest "<Month> Installs / Help Desk / Projects" set ----------
    def tickets():
        pat = re.compile(r"^(%s)\s+(Installs|Help Desk|Projects)$" % "|".join(FULLM), re.I)
        found = collections.defaultdict(dict)
        for s in X.sheet_names:
            m = pat.match(s.strip())
            if m: found[FULLM.index(m.group(1).title())][m.group(2).title()] = s
        if not found:
            warn.append("Ticket exports: no sheets named like 'September Installs' were found."); return
        tm = max(found); sh = found[tm]; meta["tkMonth"] = FULLM[tm]; meta["tkSheets"] = sorted(sh.values())
        def days(a, b):
            try:
                v = (pd.to_datetime(b) - pd.to_datetime(a)).days
                return None if pd.isna(v) else int(v)
            except Exception: return None
        hrs = lambda v: 0 if v is None or pd.isna(v) else r(v/3600, 3)
        tk = []
        if "Installs" in sh:
            ins = pd.read_excel(X, sh["Installs"])
            for _, x in ins.iterrows():
                if pd.isna(x.get("Ticket ID")): continue
                tk.append(["Installs",clean(x.get("Organization")),nm(x.get("Assignee")),clean(x.get("Problem Description")),clean(x.get("Problem Resolution")),
                           clean(x.get("TYP")),clean(x.get("OS")),clean(x.get("Browser")),clean(x.get("Software")),hrs(x.get("Time Spent(sec)")),
                           days(x.get("Date Created"),x.get("Solved Date")),str(x.get("Date Created"))[:10],str(x.get("Solved Date"))[:10]])
        if "Help Desk" in sh:
            hd = pd.read_excel(X, sh["Help Desk"])
            pd_col = "Problem Desciption" if "Problem Desciption" in hd.columns else "Problem Description"
            for _, x in hd.iterrows():
                if pd.isna(x.get("Case ID")): continue
                tk.append(["Help Desk",clean(x.get("Organization")),nm(x.get("Assignee")),clean(x.get(pd_col)),clean(x.get("RDC Problem Resolution")),
                           clean(x.get("Check Scanner Type")),clean(x.get("Operating System")),clean(x.get("Browser")),clean(x.get("RDC Software Core")),
                           hrs(x.get("Total time spent (sec)")),days(x.get("Ticket Created - Date"),x.get("Ticket Solved - Date")),
                           str(x.get("Ticket Created - Date"))[:10],str(x.get("Ticket Solved - Date"))[:10]])
        out["tickets"] = tk
        if "Projects" in sh:
            pj = pd.read_excel(X, sh["Projects"]); pjl = []
            for _, x in pj.iterrows():
                if pd.isna(x.get("Ticket ID")): continue
                dt = pd.to_datetime(x.get("Scheduled Date"), errors="coerce"); ok = not pd.isna(dt)
                pjl.append([clean(x.get("Organization")), nm(x.get("Technician")), r(x.get("Converted Time"),3),
                            dt.strftime("%Y-%m-%d") if ok else "", int(dt.hour) if ok else None, int(dt.dayofweek) if ok else None])
            out["sepProjects"] = pjl
        missing = {"Installs","Help Desk","Projects"} - set(sh)
        if missing: warn.append(f"Ticket exports for {FULLM[tm]}: no {', '.join(sorted(missing))} sheet.")
    section("Ticket exports", tickets)

    # ---------- time studies (free-text notes excluded) ----------
    def studies():
        for f in study_files:
            b = os.path.basename(f)
            try:
                ws = openpyxl.load_workbook(f, data_only=True, read_only=True)["Time Study"]
                rows = list(ws.iter_rows(min_row=1, max_row=800, values_only=True))
                h = [i for i, rw in enumerate(rows) if rw and rw[0] == "Line #"][0]
                meta_row = rows[3] if len(rows) > 3 else (); date = meta_row[1] if len(meta_row) > 1 else None
                m = re.search(r"Round\s*(\d+)", b, re.I) or re.search(r"Round(%s)" % "|".join(ROUNDW), b, re.I)
                rd = (int(m.group(1)) if m.group(1).isdigit() else ROUNDW[m.group(1).lower()]) if m else 1
                who = next((v for k, v in WHO if k in b.lower()), None)
                if not who:
                    who = os.path.splitext(b)[0]
                    warn.append(f"Time study '{b}': person not recognized from the file name; shown under the file name.")
                metatxt = " ".join(str(v) for v in meta_row if v)
                lines = []
                for rw in rows[h+1:]:
                    if len(rw) < 12 or not rw[1]: continue
                    st, en, mins = rw[5], rw[7], rw[9]
                    if not isinstance(mins, (int, float)):
                        mins = (en-st).total_seconds()/60 if hasattr(st,"hour") and hasattr(en,"hour") and hasattr(en - st, "total_seconds") else 0
                    mins = max(mins or 0, 0)
                    typ = str(rw[2] or "").strip()
                    lines.append([str(rw[1]), typ, "P" if typ in PROD else "N" if "Break" in typ else "U", r(mins),
                                  r(st.hour + st.minute/60, 3) if hasattr(st,"hour") else None, str(rw[11] or "")])
                out["studies"].append({"who":who,"round":rd,"date":date.strftime("%Y-%m-%d") if hasattr(date,"strftime") else "",
                                "half":"half day" in b.lower(),"file":b,"lines":lines,
                                "monitor":"Joseph Yarrow" if ("Yarrow" in metatxt and who != "Joseph Yarrow") else "James Lennon"})
            except Exception as e:
                warn.append(f"Time study '{b}': skipped ({type(e).__name__}: {e})")
        dates = sorted(s["date"] for s in out["studies"] if s["date"])
        if dates: meta["studyRange"] = [dates[0], dates[-1]]
    section("Time studies", studies)

    def synopsis():
        if not syn_file:
            warn.append("Time-study synopsis: no workbook with a 'Metrics-Overall' sheet; coaching items are empty."); return
        wb = openpyxl.load_workbook(syn_file, data_only=True, read_only=True)
        if "Actions" in wb.sheetnames:
            for rw in list(wb["Actions"].iter_rows(values_only=True))[1:]:
                if rw and rw[0]:
                    out["actions"].append([nm(rw[0]), int(rw[1] or 0), rw[3], rw[4], rw[5],
                                           r(rw[6]) if len(rw) > 6 and rw[6] not in (None,"None","") else None])
        mo = list(wb["Metrics-Overall"].iter_rows(values_only=True)); hd0 = mo[0]
        for rw in mo:
            if rw[0] in ("Productive min","Non-Productive min","Productive %","Total logged min","Activity lines"):
                for j in range(1, len(hd0)):
                    if rw[j] is None: continue
                    m = re.match(r"(.+) (R\d|Total)$", str(hd0[j]))
                    who = nm(m.group(1)) if m else "All"; k = m.group(2) if m else "Total"
                    out["synopsis"].setdefault(who, {}).setdefault(k, {})[rw[0]] = float(rw[j])
        meta["synopsisFile"] = os.path.basename(syn_file)
    section("Time-study synopsis", synopsis)

    meta.update(workbook=os.path.basename(F), studyCount=len(out["studies"]),
                built=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), warnings=warn,
                hwTrailing=hw_space, aliases=sorted(alias_hits),
                pooledMonths=sorted(out["pooledHours"]))
    return out
