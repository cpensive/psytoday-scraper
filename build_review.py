"""Build a self-contained HTML review app from the scored therapists.

Reads data/evaluated.json and writes data/review.html with the therapist data
embedded directly (no server, no fetch, no dependencies). Open it in a browser.
Re-run after a re-score to refresh; saved decisions (status + notes) persist
because they are keyed by profile_id.

UI philosophy: calm by default. The list is just name + index + a single muted
signal summary. All filters live behind one "Filters" toggle. Every signal is
still sortable/filterable - just not shouting at you.

Usage:  uv run python build_review.py
Output: data/review.html
"""

from __future__ import annotations

import datetime as _dt
import json
import sys

import common
import config

logger = common.logger


def _trim(record: dict) -> dict:
    ev = record.get("evaluation") or {}
    s = ev.get("signals") or {}
    return {
        "id": str(record.get("profile_id", "")),
        "name": ev.get("name") or record.get("name", ""),
        "index": ev.get("composite_score"),
        "tier": ev.get("verdict", "SKIP"),
        "one_line": ev.get("one_line", ""),
        "centrality": s.get("couples_centrality", "secondary"),
        "style": s.get("style_llm") or s.get("approach", "Unclear"),
        "couples": s.get("couples", "No"),
        "method": s.get("method", "None"),
        "method_depth": s.get("method_depth", "none"),
        "experience_years": s.get("experience_years"),
        "approach": s.get("approach", "Unclear"),
        "cultural": s.get("cultural", "none"),
        "license": s.get("license", "unknown"),
        "adhd": bool(s.get("adhd")),
        "generalist": bool(s.get("generalist")),
        "off_target": bool(s.get("off_target")),
        "in_person": bool(s.get("nyc_in_person")),
        "online": bool(s.get("online")),
        "url": record.get("url", ""),
        "website": record.get("website_url", ""),
        "location": record.get("location") or "",
        "bio": record.get("bio_narrative") or "",
        "treatment": record.get("treatment_approach_text") or "",
    }


def build() -> int:
    common.setup_logging()
    records = common.load_json(config.EVALUATED_PATH, [])
    if not records:
        logger.error("No evaluated records at %s. Run score_heuristic.py first.", config.EVALUATED_PATH)
        return 1
    # Drop hard-excluded profiles entirely; group primary above secondary, then by index.
    crank = {"primary": 0, "secondary": 1, "individual_primary": 2}
    data = [_trim(r) for r in records if (r.get("evaluation") or {}).get("verdict") != "EXCLUDED"]
    data.sort(key=lambda t: (crank.get(t["centrality"], 3), -(t["index"] or 0)))
    excluded = sum(1 for r in records if (r.get("evaluation") or {}).get("verdict") == "EXCLUDED")
    counts: dict[str, int] = {"EXCLUDED": excluded}
    for t in data:
        counts[t["tier"]] = counts.get(t["tier"], 0) + 1
    meta = {"generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "total": len(data), "counts": counts}

    html = _TEMPLATE.replace("__REVIEW_DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__REVIEW_META__", json.dumps(meta, ensure_ascii=False))
    out = config.DATA_DIR / "review.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Wrote review app -> %s  (%d therapists: %s)", out, len(data),
                ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    logger.info("Open it with:  open %s", out)
    return 0


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Therapist Review</title>
<style>
  :root{ --bg:#0e0f12; --panel:#16181d; --line:#262a31; --txt:#e7e9ee; --muted:#8b93a1;
         --accent:#7aa2f7; --call:#34d399; --read:#e8b84b; --skip:#5b626e; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--txt);
        font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  header{ position:sticky; top:0; z-index:10; background:rgba(14,15,18,.96); backdrop-filter:blur(8px);
          border-bottom:1px solid var(--line); padding:14px 20px; }
  .top{ display:flex; align-items:center; gap:12px; max-width:860px; margin:0 auto; }
  h1{ font-size:15px; font-weight:600; margin:0; white-space:nowrap; }
  h1 .sub{ color:var(--muted); font-weight:400; }
  .grow{ flex:1; }
  input[type=text],select{ background:transparent; border:1px solid var(--line); color:var(--txt);
        padding:7px 11px; border-radius:9px; font:inherit; font-size:13px; }
  input[type=text]{ width:100%; }
  .ghost{ background:transparent; border:1px solid var(--line); color:var(--muted); padding:7px 12px;
          border-radius:9px; cursor:pointer; font:inherit; font-size:13px; }
  .ghost.on{ color:var(--accent); border-color:var(--accent); }
  .panel{ max-width:860px; margin:12px auto 0; display:none; }
  .panel.open{ display:block; }
  .frow{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:7px 0; }
  .flbl{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.6px; width:84px; }
  .chip{ background:transparent; border:1px solid var(--line); color:var(--muted); padding:5px 11px;
         border-radius:999px; cursor:pointer; user-select:none; font-size:12px; }
  .chip.on{ color:var(--bg); background:var(--accent); border-color:var(--accent); }
  .panel input[type=number]{ background:transparent; border:1px solid var(--line); color:var(--txt);
        width:64px; padding:5px 8px; border-radius:8px; font:inherit; }
  .askbar{ max-width:860px; margin:10px auto 0; color:var(--muted); font-size:12.5px;
        border-left:2px solid var(--accent); padding:4px 12px; }
  .wrap{ max-width:860px; margin:0 auto; padding:14px 20px 60px; }
  .stat{ color:var(--muted); font-size:12.5px; margin:6px 0 14px; }
  .stat b{ color:var(--txt); }
  .card{ border-bottom:1px solid var(--line); padding:16px 0; }
  .card.pass{ opacity:.4; }
  .r1{ display:flex; align-items:baseline; gap:10px; }
  .dot{ width:8px; height:8px; border-radius:50%; flex:none; align-self:center; }
  .d-CALL{ background:var(--call);} .d-READ_MORE{ background:var(--read);} .d-SKIP{ background:var(--skip);}
  .name{ font-size:15px; font-weight:600; }
  .idx{ margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; font-size:13px; }
  .sig{ color:var(--muted); font-size:13px; margin:5px 0 0 18px; }
  .loc{ color:var(--muted); font-size:12px; margin:2px 0 0 18px; }
  .more{ margin:8px 0 0 18px; }
  .more a, details summary{ color:var(--accent); text-decoration:none; font-size:12.5px; cursor:pointer; }
  details{ margin:6px 0 0 18px; } details .body{ white-space:pre-wrap; color:#c8cdd6; font-size:13px;
        margin-top:6px; background:var(--panel); padding:11px; border-radius:9px; max-height:260px; overflow:auto; }
  .acts{ display:flex; gap:0; margin:10px 0 0 18px; border:1px solid var(--line); border-radius:9px;
        overflow:hidden; width:max-content; }
  .seg{ background:transparent; border:0; border-right:1px solid var(--line); color:var(--muted);
        padding:6px 13px; cursor:pointer; font:inherit; font-size:12px; }
  .seg:last-child{ border-right:0; }
  .seg.on{ background:var(--accent); color:var(--bg); }
  .notes{ width:100%; margin:9px 0 0 18px; max-width:calc(100% - 18px); background:var(--panel);
        border:1px solid var(--line); color:var(--txt); border-radius:9px; padding:8px; resize:vertical;
        min-height:0; height:34px; font:inherit; font-size:13px; }
  .empty{ text-align:center; color:var(--muted); padding:50px; }
</style>
</head>
<body>
<header>
  <div class="top">
    <h1>Therapists <span class="sub" id="metaSub"></span></h1>
    <span class="grow"><input type="text" id="search" placeholder="Search name"></span>
    <select id="sort" title="Sort">
      <option value="match">Best match</option>
      <option value="experience">Experience</option>
      <option value="method_depth">Method depth</option>
      <option value="name">Name</option>
    </select>
    <button class="ghost" id="filtersBtn">Filters</button>
  </div>
  <div class="panel" id="panel">
    <div class="frow"><span class="flbl">Couples</span>
      <span class="chip on" data-fac="centrality" data-val="primary">Primary focus</span>
      <span class="chip on" data-fac="centrality" data-val="secondary">Does couples</span></div>
    <div class="frow"><span class="flbl">Method</span>
      <span class="chip on" data-fac="method_depth" data-val="supervisor">Supervisor</span>
      <span class="chip on" data-fac="method_depth" data-val="certified">Certified</span>
      <span class="chip on" data-fac="method_depth" data-val="trained">Trained</span>
      <span class="chip on" data-fac="method_depth" data-val="listed">Listed</span>
      <span class="chip on" data-fac="method_depth" data-val="none">None</span></div>
    <div class="frow"><span class="flbl">Cultural</span>
      <span class="chip on" data-fac="cultural" data-val="asian">Asian</span>
      <span class="chip on" data-fac="cultural" data-val="intercultural">Intercultural</span>
      <span class="chip on" data-fac="cultural" data-val="ea_language">EA-language</span>
      <span class="chip on" data-fac="cultural" data-val="checkbox">Checkbox</span>
      <span class="chip on" data-fac="cultural" data-val="none">None</span></div>
    <div class="frow"><span class="flbl">Status</span>
      <span class="chip on" data-stat="shortlist">Shortlist</span>
      <span class="chip on" data-stat="intro">Intro</span>
      <span class="chip on" data-stat="session">Session</span>
      <span class="chip on" data-stat="">Unmarked</span>
      <span class="chip" data-stat="pass">Passed</span></div>
    <div class="frow"><span class="flbl">Filters</span>
      <span class="chip" data-tog="independent">Independent only</span>
      <span class="chip" data-tog="adhd">ADHD</span>
      <span class="chip" data-tog="hideGeneralist">Hide generalist</span>
      <span style="margin-left:6px;color:var(--muted);font-size:12px">Min yrs</span>
      <input type="number" id="minYears" min="0" placeholder="0">
      <span class="grow"></span>
      <button class="ghost" id="exportBtn">Export</button>
      <button class="ghost" id="importBtn">Import</button>
      <input type="file" id="importFile" accept="application/json" style="display:none">
    </div>
  </div>
  <div class="askbar">On every call, verify the two things a profile can't tell you: <b>what share of their practice is actually couples</b>, and <b>how directive vs. validating</b> they are.</div>
</header>
<div class="wrap">
  <div class="stat" id="stat"></div>
  <div id="list"></div>
  <div class="empty" id="empty" style="display:none">Nothing matches these filters.</div>
</div>
<script>
const DATA=__REVIEW_DATA__, META=__REVIEW_META__, LSKEY="psytoday_review_v2";
let decisions={}; try{ decisions=JSON.parse(localStorage.getItem(LSKEY))||{}; }catch(e){ decisions={}; }
function save(){ localStorage.setItem(LSKEY,JSON.stringify(decisions)); }
function dec(id){ return decisions[id]||(decisions[id]={status:"",notes:""}); }

const FACETS=["centrality","method_depth","cultural"];
const state={ search:"", sort:"match", minYears:0,
  filters:{ centrality:new Set(["primary","secondary"]),
            method_depth:new Set(["supervisor","certified","trained","listed","none"]),
            cultural:new Set(["asian","intercultural","ea_language","checkbox","none"]) },
  status:new Set(["shortlist","intro","session",""]),
  tog:{ independent:false, adhd:false, hideGeneralist:false } };
const ORD={ centrality:{primary:0,secondary:1,individual_primary:2}, method_depth:{supervisor:0,certified:1,trained:2,listed:3,none:4} };
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function matches(t){
  if(state.search && !(t.name||"").toLowerCase().includes(state.search)) return false;
  for(const f of FACETS){ if(!state.filters[f].has(t[f])) return false; }
  if(state.minYears && !(t.experience_years!=null && t.experience_years>=state.minYears)) return false;
  if(state.tog.independent && t.license!=="independent") return false;
  if(state.tog.adhd && !t.adhd) return false;
  if(state.tog.hideGeneralist && t.generalist) return false;
  if(!state.status.has((decisions[t.id]||{}).status||"")) return false;
  return true;
}
function sortData(a){ const s=state.sort, cr=ORD.centrality; return a.slice().sort((x,y)=>{
  if(s==="name") return (x.name||"").localeCompare(y.name||"");
  if(s==="experience") return (y.experience_years??-1)-(x.experience_years??-1)||(y.index-x.index);
  if(s==="method_depth") return (ORD.method_depth[x[s]]??9)-(ORD.method_depth[y[s]]??9)||(y.index-x.index);
  // "match": primary band above secondary band, then index within each
  return (cr[x.centrality]??3)-(cr[y.centrality]??3)||(y.index||0)-(x.index||0); }); }

function card(t){
  const d=decisions[t.id]||{status:"",notes:""};
  const fmt=[]; if(t.in_person)fmt.push("in-person"); if(t.online)fmt.push("online");
  const S=["shortlist","intro","session","pass"], L={shortlist:"Shortlist",intro:"Intro",session:"Session",pass:"Pass"};
  const segs=S.map(s=>`<button class="seg ${d.status===s?'on':''}" data-act="${s}">${L[s]}</button>`).join("");
  const links=[ t.url?`<a href="${esc(t.url)}" target="_blank" rel="noopener">Profile \u2197</a>`:"",
               t.website?`<a href="${esc(t.website)}" target="_blank" rel="noopener">Website \u2197</a>`:"" ].filter(Boolean).join("  ");
  const detail=(t.bio||t.treatment)?`<details><summary>Read bio</summary><div class="body">${esc([t.bio,t.treatment].filter(Boolean).join("\n\n"))}</div></details>`:"";
  return `<div class="card ${d.status==='pass'?'pass':''}" data-id="${esc(t.id)}">
    <div class="r1"><span class="dot d-${t.tier}"></span><span class="name">${esc(t.name)}</span><span class="idx">${t.index}</span></div>
    <div class="sig">${esc(t.one_line)}</div>
    <div class="loc">${esc(t.location)}${fmt.length?(" \u00b7 "+fmt.join(", ")):""}</div>
    ${detail}
    ${links?`<div class="more">${links}</div>`:""}
    <div class="acts">${segs}</div>
    <textarea class="notes" placeholder="Notes">${esc(d.notes)}</textarea>
  </div>`;
}
function render(){
  const f=sortData(DATA.filter(matches));
  const sl=Object.values(decisions).filter(d=>d.status==='shortlist').length;
  const it=Object.values(decisions).filter(d=>d.status==='intro').length;
  const se=Object.values(decisions).filter(d=>d.status==='session').length;
  document.getElementById("stat").innerHTML=`<b>${f.length}</b> shown \u00b7 ${META.total} scored &nbsp;&nbsp; \u2b50 ${sl} shortlisted \u00b7 \ud83d\udcde ${it}/5\u20137 intro \u00b7 \u2705 ${se}/3 sessions`;
  document.getElementById("list").innerHTML=f.map(card).join("");
  document.getElementById("empty").style.display=f.length?"none":"block";
}
document.addEventListener("click",e=>{
  const fc=e.target.closest("[data-fac]"); if(fc){ const set=state.filters[fc.dataset.fac];
    set.has(fc.dataset.val)?set.delete(fc.dataset.val):set.add(fc.dataset.val); fc.classList.toggle("on"); render(); return; }
  const tg=e.target.closest("[data-tog]"); if(tg){ state.tog[tg.dataset.tog]=!state.tog[tg.dataset.tog]; tg.classList.toggle("on"); render(); return; }
  const sf=e.target.closest("[data-stat]"); if(sf){ const v=sf.dataset.stat;
    state.status.has(v)?state.status.delete(v):state.status.add(v); sf.classList.toggle("on"); render(); return; }
  const sb=e.target.closest(".seg"); if(sb){ const id=sb.closest(".card").dataset.id,a=sb.dataset.act,d=dec(id);
    d.status=(d.status===a)?"":a; save(); render(); return; }
});
document.addEventListener("input",e=>{ if(e.target.classList.contains("notes")){ dec(e.target.closest(".card").dataset.id).notes=e.target.value; save(); } });
document.getElementById("search").addEventListener("input",e=>{state.search=e.target.value.toLowerCase();render();});
document.getElementById("sort").addEventListener("change",e=>{state.sort=e.target.value;render();});
document.getElementById("minYears").addEventListener("input",e=>{state.minYears=parseInt(e.target.value)||0;render();});
document.getElementById("filtersBtn").addEventListener("click",e=>{ document.getElementById("panel").classList.toggle("open"); e.target.classList.toggle("on"); });
document.getElementById("exportBtn").addEventListener("click",()=>{ const b=new Blob([JSON.stringify(decisions,null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(b); a.download="therapist-decisions-"+new Date().toISOString().slice(0,10)+".json"; a.click(); });
document.getElementById("importBtn").addEventListener("click",()=>document.getElementById("importFile").click());
document.getElementById("importFile").addEventListener("change",e=>{ const f=e.target.files[0]; if(!f)return; const r=new FileReader();
  r.onload=()=>{ try{ const inc=JSON.parse(r.result); let m=0; for(const id in inc){ const c=decisions[id]||{status:"",notes:""},i=inc[id]||{};
    if(i.status&&i.status!==c.status){c.status=i.status;m++;} if(i.notes&&i.notes!==c.notes){c.notes=c.notes?(c.notes+"\n---\n"+i.notes):i.notes;} decisions[id]=c; }
    save(); render(); alert("Merged. Updated "+m+" statuses."); }catch(err){ alert("Bad file: "+err); } }; r.readAsText(f); });
document.getElementById("metaSub").textContent=" \u00b7 "+(META.counts.CALL||0)+" call \u00b7 "+(META.counts.READ_MORE||0)+" review";
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(build())
