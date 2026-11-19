// Minimal DOM shim to run every dashboard view once and surface runtime errors.
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const data = html.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1];
const code = html.split("\"use strict\";")[1].split("</script>")[0];
class N {
  constructor(tag){ this.tag=tag; this.children=[]; this.attrs={}; this.style={}; this.dataset={}; this.textContent=""; this.hidden=false; this.value=""; this.listeners={};
    this.classList={toggle(){},add(){},remove(){}}; }
  append(...k){ this.children.push(...k); } prepend(...k){ this.children.unshift(...k); }
  replaceChildren(...k){ this.children=k; }
  setAttribute(k,v){ this.attrs[k]=String(v); if(k.startsWith("data-")) this.dataset[k.slice(5)]=String(v); }
  removeAttribute(k){ delete this.attrs[k]; } toggleAttribute(){}
  addEventListener(t,f){ (this.listeners[t] ||= []).push(f); }
  querySelectorAll(){ const out=[]; const walk=n=>{ (n.children||[]).forEach(c=>{ if(c&&c.tag==="a") out.push(c); if(c&&c.children) walk(c); }); }; walk(this); return out; }
  getBoundingClientRect(){ return {left:0,top:0,right:100,width:100,height:20}; }
  get clientWidth(){ return 900; } set innerHTML(v){} set className(v){ this.attrs.class=v; } get className(){ return this.attrs.class; }
}
const els={}; const byId=id=>els[id] ||= new N("div");
global.document={ createElement:t=>new N(t), createElementNS:(_,t)=>new N(t), createTextNode:t=>({text:t}),
  getElementById:id=> id==="data"? {textContent:data} : byId(id) };
global.location={hash:"", get h(){return this.hash}}; global.innerWidth=1200; global.innerHeight=800;
global.addEventListener=()=>{};
let errors=0;
try { eval(code + `
  for (const [id] of TABS) { try { S.tab=id; render(); console.log("ok", id); } catch(e){ errors++; console.log("FAIL", id, e.stack.split("\\n").slice(0,3).join(" | ")); } }
  S.from=4; S.to=4; S.types=new Set(["Installs"]);
  for (const [id] of TABS) { try { S.tab=id; render(); } catch(e){ errors++; console.log("FAIL (May, Installs)", id, e.message); } }
  // fire every input listener once (sliders, search)
  const fire=n=>{ (n.children||[]).forEach(c=>{ if(c&&c.listeners){ ["input","click"].forEach(t=>(c.listeners[t]||[]).forEach(f=>{ try{ f({target:c}); }catch(e){ errors++; console.log("listener FAIL", t, e.message);} })); fire(c);} }); };
  S.tab="scenarios"; S.from=0; S.to=9; S.types=new Set(BILL); render(); fire(MAIN); console.log("listeners fired");
  const F=finance(); console.log("rev", Math.round(F.tot.rev), "loaded", Math.round(F.tot.loaded), "direct", Math.round(F.tot.direct));
`); } catch(e){ errors++; console.log("TOP FAIL", e.stack); }
console.log("errors", errors);
