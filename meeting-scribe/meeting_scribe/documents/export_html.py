"""Self-contained interactive transcript.

A single HTML file (inline CSS + JS, no network) with an <audio> element that
references the recording by relative path. As the audio plays the current word
and speaker line are highlighted; clicking any word seeks the audio there.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

from .model import Project

_PALETTE = ["#2f6fed", "#e8590c", "#2b8a3e", "#9c36b5", "#c92a2a",
            "#0b7285", "#5f3dc4", "#a9761b", "#495057", "#1864ab"]


def build_interactive_html(project: Project, out_path: Path, audio_rel: str,
                           named: bool = True) -> Path:
    t = project.transcript
    spk_index = {s.id: i for i, s in enumerate(t.speakers)}

    def spk_name(sid: str | None) -> str:
        s = t.speaker_by_id(sid)
        if not s:
            return "Speaker"
        return s.display if named else s.label

    segments: List[Dict[str, Any]] = []
    for seg in t.segments:
        words = []
        for w in (seg.words or []):
            words.append({"s": round(w.start, 3), "e": round(w.end, 3),
                          "t": w.text})
        if not words and seg.text:
            # even split fallback so highlighting still works
            toks = seg.text.split()
            if toks:
                dur = max(seg.end - seg.start, 0.001)
                step = dur / len(toks)
                for i, tok in enumerate(toks):
                    words.append({"s": round(seg.start + i * step, 3),
                                  "e": round(seg.start + (i + 1) * step, 3),
                                  "t": tok})
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "speaker": spk_name(seg.speaker),
            "color": _PALETTE[spk_index.get(seg.speaker, 0) % len(_PALETTE)],
            "words": words,
        })

    title = (project.minutes.title if project.minutes else "Meeting") + " - Interactive Transcript"
    legend = [{"name": (s.display if named else s.label),
               "color": _PALETTE[i % len(_PALETTE)]}
              for i, s in enumerate(t.speakers)]

    data = json.dumps({"segments": segments, "legend": legend,
                       "audio": audio_rel, "title": title},
                      ensure_ascii=False)
    # keep a literal "</script>" in transcript text from ending the script block
    data = data.replace("</", "<\\/")

    doc = _TEMPLATE.replace("/*DATA*/", data).replace(
        "{{TITLE}}", html.escape(title)).replace(
        "{{AUDIO}}", html.escape(audio_rel))
    out_path = Path(out_path)
    out_path.write_text(doc, encoding="utf-8")
    return out_path


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background: #fbfbfd; color: #1c1c1e; }
  @media (prefers-color-scheme: dark) { body { background:#1c1c1e; color:#f2f2f7; } }
  header { position: sticky; top: 0; z-index: 5; backdrop-filter: blur(8px);
           background: rgba(251,251,253,.85); border-bottom: 1px solid #d8d8dd; padding: 12px 20px; }
  @media (prefers-color-scheme: dark) { header { background: rgba(28,28,30,.85); border-color:#3a3a3c; } }
  h1 { font-size: 17px; margin: 0 0 8px; }
  .controls { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  audio { height: 34px; }
  .legend { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 8px; font-size: 13px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .dot { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
  main { max-width: 820px; margin: 24px auto 120px; padding: 0 20px; }
  .line { display: flex; gap: 12px; padding: 8px 10px; border-radius: 8px; margin: 2px 0; }
  .line.active { background: rgba(47,111,237,.08); }
  .who { flex: 0 0 130px; font-weight: 600; font-size: 13px; padding-top: 2px; }
  .said { flex: 1; }
  .w { cursor: pointer; border-radius: 3px; padding: 0 1px; transition: background .1s; }
  .w:hover { background: rgba(120,120,128,.25); }
  .w.spoken { background: rgba(47,111,237,.18); }
  .w.now { background: #2f6fed; color: #fff; }
  .speed { font-size: 13px; }
  button.jump { font: inherit; font-size: 12px; padding: 2px 8px; border-radius: 6px;
                border: 1px solid #c7c7cc; background: transparent; color: inherit; cursor: pointer; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; padding: 8px 20px;
           background: rgba(251,251,253,.9); border-top: 1px solid #d8d8dd; font-size: 12px;
           display: flex; justify-content: space-between; }
  @media (prefers-color-scheme: dark){ footer{ background: rgba(28,28,30,.9); border-color:#3a3a3c; } }
</style>
</head>
<body>
<header>
  <h1 id="ttl"></h1>
  <div class="controls">
    <audio id="au" controls preload="metadata" src="{{AUDIO}}"></audio>
    <label class="speed">Speed
      <select id="rate">
        <option value="0.75">0.75x</option>
        <option value="1" selected>1x</option>
        <option value="1.25">1.25x</option>
        <option value="1.5">1.5x</option>
        <option value="2">2x</option>
      </select>
    </label>
    <label class="speed"><input type="checkbox" id="follow" checked> Auto-scroll</label>
  </div>
  <div class="legend" id="legend"></div>
</header>
<main id="body"></main>
<footer>
  <span id="clock">00:00</span>
  <span>Generated by Meeting Scribe &mdash; open this file in any browser. Keep the audio file alongside it.</span>
</footer>
<script>
const DATA = /*DATA*/;
const au = document.getElementById('au');
const body = document.getElementById('body');
document.getElementById('ttl').textContent = DATA.title;
document.title = DATA.title;

const leg = document.getElementById('legend');
DATA.legend.forEach(l => {
  const s = document.createElement('span');
  s.innerHTML = '<span class="dot" style="background:'+l.color+'"></span>'+l.name;
  leg.appendChild(s);
});

const wordEls = [];
DATA.segments.forEach((seg, si) => {
  const line = document.createElement('div');
  line.className = 'line'; line.dataset.si = si;
  line.dataset.start = seg.start; line.dataset.end = seg.end;
  const who = document.createElement('div');
  who.className = 'who'; who.style.color = seg.color; who.textContent = seg.speaker;
  const said = document.createElement('div'); said.className = 'said';
  seg.words.forEach(w => {
    const span = document.createElement('span');
    span.className = 'w'; span.textContent = w.t + ' ';
    span.dataset.s = w.s; span.dataset.e = w.e;
    span.addEventListener('click', () => { au.currentTime = w.s; au.play(); });
    said.appendChild(span);
    wordEls.push(span);
  });
  line.appendChild(who); line.appendChild(said);
  line.addEventListener('click', ev => { if (ev.target === line || ev.target === who)
    { au.currentTime = seg.start; au.play(); } });
  body.appendChild(line);
});

document.getElementById('rate').addEventListener('change', e => au.playbackRate = parseFloat(e.target.value));
const follow = document.getElementById('follow');
const clock = document.getElementById('clock');
function fmt(t){ t=Math.max(0,Math.floor(t)); const m=Math.floor(t/60), s=t%60;
  return (m<10?'0':'')+m+':'+(s<10?'0':'')+s; }

let lastNow = -1, lastLine = null, ticking = false;
function paint(){
  ticking = false;
  const ct = au.currentTime;
  clock.textContent = fmt(ct) + ' / ' + fmt(au.duration || 0);
  // binary search current word
  let lo = 0, hi = wordEls.length - 1, idx = -1;
  while (lo <= hi){ const mid = (lo+hi)>>1; const el = wordEls[mid];
    const s = +el.dataset.s, e = +el.dataset.e;
    if (ct < s) hi = mid - 1; else if (ct > e) lo = mid + 1; else { idx = mid; break; } }
  if (idx === -1) idx = Math.min(lo, wordEls.length - 1);
  if (idx !== lastNow){
    if (lastNow >= 0 && wordEls[lastNow]) wordEls[lastNow].classList.remove('now');
    for (let i = 0; i < wordEls.length; i++)
      wordEls[i].classList.toggle('spoken', i < idx);
    const cur = wordEls[idx];
    if (cur){ cur.classList.add('now');
      const line = cur.closest('.line');
      if (line !== lastLine){
        if (lastLine) lastLine.classList.remove('active');
        line.classList.add('active');
        if (follow.checked) line.scrollIntoView({block:'center', behavior:'smooth'});
        lastLine = line;
      }
    }
    lastNow = idx;
  }
}
au.addEventListener('timeupdate', () => { if (!ticking){ ticking = true; requestAnimationFrame(paint); } });
au.addEventListener('loadedmetadata', paint);
au.addEventListener('error', () => {
  const w = document.createElement('div');
  w.style.cssText = 'background:#c92a2a;color:#fff;padding:10px;border-radius:8px;margin:12px 0';
  w.textContent = 'Could not load the audio file "' + DATA.audio + '". Keep it in the same folder as this page.';
  body.prepend(w);
});
</script>
</body>
</html>
"""
