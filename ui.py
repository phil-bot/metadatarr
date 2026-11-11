"""tba-tickets – Gemeinsame Oberfläche: Stil, Seitenrahmen, Hilfsfunktionen.

Aufbau: mobil zuerst (ein Spalte, große Touch-Ziele). Ab 900 px wird die Folgenliste als Tabellenzeile
dargestellt (gleiches Markup, nur CSS-Grid).
"""
import html
import re
import threading
from datetime import datetime

from config import CSRF_TOKEN, SHOW_POSTERS


# ---------------------------------------------------------------------------
# Stil
# ---------------------------------------------------------------------------
LIGHT = ("--bg:#f4f5f7;--sf:#fff;--fg:#16181d;--mut:#5b6270;--line:#dfe2e8;--acc:#1d4ed8;--acc-fg:#fff;--acc-soft:#e6eefc;"
         "--ok:#15713d;--ok-bg:#e3f4ea;--warn:#8a5300;--warn-bg:#fdf1dc;--busy:#1d4ed8;--busy-bg:#e6eefc;--bad:#b42318;"
         "--open-fg:#7a5a00;--open-bg:#ffe9a6;--side:#fbfbfc;--hov:#eef0f4")
DARK = ("--bg:#0e1014;--sf:#171a21;--fg:#e8eaee;--mut:#9aa3b2;--line:#2a2f3b;--acc:#7aa7ff;--acc-fg:#0b1220;--acc-soft:#1b2a4a;"
        "--ok:#5fd28a;--ok-bg:#15301f;--warn:#f0b35a;--warn-bg:#3a2a10;--busy:#8fb4ff;--busy-bg:#18284a;--bad:#ff8a80;"
        "--open-fg:#ffe08a;--open-bg:#4a3a0c;--side:#12151b;--hov:#1f2430")
# Farbschema: System (Voreinstellung), Hell oder Dunkel. Die Wahl steht am <html>-Element (data-theme).
THEME_CSS = (
    ":root{--r:12px;color-scheme:light;" + LIGHT + "}\n"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;" + DARK + "}}\n"
    ":root[data-theme=dark]{color-scheme:dark;" + DARK + "}\n"
    ":root[data-theme=light]{color-scheme:light;" + LIGHT + "}"
)

CSS = """
@@ROOT@@
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.35rem;line-height:1.25;margin:.2rem 0 .3rem;overflow-wrap:anywhere}
h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:1.6rem 0 .5rem;font-weight:700}
p{margin:.4rem 0}
.mut{color:var(--mut)}.small{font-size:.87rem}
main{max-width:46rem;margin:0 auto;padding:.9rem 1rem 7.5rem}
@media (min-width:900px){main{max-width:74rem;padding:1.4rem 2rem 4rem}}
.crumb{font-size:.9rem;margin:0 0 .2rem}
.meta{color:var(--mut);font-size:.92rem;margin:0 0 .7rem;overflow-wrap:anywhere}
.note,.err{border-radius:var(--r);padding:.6rem .85rem;margin:.6rem 0;font-size:.93rem;background:var(--sf)}
.note{border:1px solid var(--acc)}
.err{border:1px solid var(--bad)}
.hint{color:var(--warn);font-size:.88rem;margin:.3rem 0}
.row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.chip{display:inline-flex;align-items:center;gap:.25rem;border:1px solid var(--line);border-radius:999px;padding:.1rem .6rem;font-size:.8rem;line-height:1.5;background:var(--sf);color:var(--mut);white-space:nowrap;text-decoration:none}
a.chip:hover{text-decoration:none;border-color:var(--acc)}
.chip.ok{color:var(--ok);background:var(--ok-bg);border-color:transparent}
.chip.busy{color:var(--busy);background:var(--busy-bg);border-color:transparent}
.chip.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.chip.fg{color:var(--fg)}
.chip.open{color:var(--open-fg);background:var(--open-bg);border-color:transparent}
.btn.icon{font-size:1.25rem;line-height:1;min-width:44px;padding:.3rem .6rem}
.btn{font:inherit;min-height:44px;padding:.45rem .95rem;border-radius:10px;border:1px solid var(--line);background:var(--sf);color:var(--fg);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:.35rem;text-decoration:none;white-space:nowrap}
.btn:hover{border-color:var(--acc);text-decoration:none;background:var(--hov)}
.btn.pri:hover{background:var(--acc);filter:brightness(1.08)}
.btn.pri{background:var(--acc);border-color:var(--acc);color:var(--acc-fg);font-weight:600}
.btn.quiet{border-color:transparent;background:transparent;color:var(--acc)}
.btn.sm{min-height:38px;padding:.3rem .7rem;font-size:.9rem}
.btn.block{width:100%}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn:disabled:hover{border-color:var(--line)}
input[type=text],select{font:inherit;min-height:44px;padding:.4rem .6rem;border-radius:10px;border:1px solid var(--line);background:var(--sf);color:var(--fg)}
input[type=text]{width:100%}
select{padding-right:2rem}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.9rem 0}
.stat{background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.7rem .9rem}
.stat b{display:block;font-size:1.5rem;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{font-size:.85rem;color:var(--mut)}
@media (min-width:900px){.stats{grid-template-columns:repeat(4,1fr)}}
.list{display:grid;gap:.5rem}
.item{display:flex;gap:.7rem;align-items:center;justify-content:space-between;background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.7rem .9rem;color:var(--fg);min-height:52px}
a.item:hover{border-color:var(--acc);text-decoration:none;background:var(--hov)}
.item .t{font-weight:600;overflow-wrap:anywhere}
.item .s{font-size:.86rem;color:var(--mut);overflow-wrap:anywhere}
.item .end{display:flex;flex-direction:column;align-items:flex-end;gap:.3rem;flex-shrink:0}
.filter{display:flex;flex-wrap:wrap;gap:.5rem;margin:.2rem 0 .6rem}
.filter a.chip{min-height:36px;padding:.25rem .8rem;font-size:.9rem}
.filter a.chip[aria-current=true]{background:var(--acc);color:var(--acc-fg);border-color:transparent}
details{margin:.3rem 0}
summary{cursor:pointer;color:var(--acc);min-height:40px;display:flex;align-items:center;list-style:revert}
details.info{background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.2rem .85rem .6rem;margin:.6rem 0}
details.info ul{margin:.3rem 0 .2rem 1.1rem;padding:0;font-size:.92rem}
.seasonbar{display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;margin:.9rem 0 .3rem}
.seasonbar select{font-size:1.05rem;font-weight:600}
.eps{display:grid;gap:.6rem;margin-top:.5rem}
.ep{background:var(--sf);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--r);padding:.7rem .8rem;display:grid;gap:.4rem;align-content:start}
.ep[data-state=markiert]{border-left-color:var(--ok)}
.ep[data-state="im Ticket"]{border-left-color:var(--busy)}
.ep[data-state=verworfen]{opacity:.62}
.ep-head{display:flex;flex-wrap:wrap;gap:.2rem .7rem;align-items:baseline}
.code{font-weight:700;font-variant-numeric:tabular-nums;font-size:1.02rem}
.date{font-size:.9rem;color:var(--mut);font-variant-numeric:tabular-nums}
.d-sent{color:var(--ok)}.d-ann{color:var(--warn)}
.ep-title{font-weight:600;overflow-wrap:anywhere}
.ep-sub{font-size:.87rem;color:var(--mut);overflow-wrap:anywhere}
.ep-chips{display:flex;flex-wrap:wrap;gap:.3rem;margin:.25rem 0}
.ep-act{display:flex;flex-wrap:wrap;gap:.4rem .8rem;align-items:center}
.ep-act .var{display:flex;flex-wrap:wrap;gap:.4rem .8rem;align-items:center}
.check{display:inline-flex;align-items:center;gap:.5rem;min-height:44px;cursor:pointer;font-weight:600}
.check input{width:1.4rem;height:1.4rem;accent-color:var(--ok);margin:0}
.ep[data-state="im Ticket"] .check input{accent-color:var(--busy)}
.ep-nav{display:flex;align-items:center;gap:.2rem}
.ep-nav .btn{min-width:44px;padding:0}
.pos{font-variant-numeric:tabular-nums;color:var(--mut);font-size:.9rem;min-width:3rem;text-align:center}
details.more{border-top:1px dashed var(--line);padding-top:.3rem;font-size:.92rem}
details.more .var{display:block}
.sugtext{white-space:pre-line;overflow-wrap:anywhere;margin:.3rem 0}
.actbar{position:sticky;bottom:0;z-index:4;background:var(--bg);padding:.6rem 0 .5rem;margin-top:.8rem;border-top:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
.actbar form{margin:0;display:contents}
.actbar .btn{width:100%}
.actbar .wide{grid-column:1/-1}
@media (min-width:900px){.actbar{grid-template-columns:auto auto auto auto;justify-content:end}.actbar .wide{grid-column:auto}.actbar .btn{width:auto}}
.box{background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.8rem .95rem;margin:.5rem 0}
.copy{display:grid;gap:.5rem;margin-top:.4rem}
.body-preview{background:#fff;color:#111;border:1px solid #ccc;border-radius:10px;padding:.8rem 1rem;max-height:60vh;overflow:auto;font-size:.95rem}
.body-preview pre{white-space:pre-wrap;background:#f1f2f4;padding:.5rem .6rem;border-radius:6px;font:.88rem ui-monospace,Menlo,monospace;margin:.4rem 0}
.field{position:relative}
.field input{width:100%;padding-right:3rem}
.body-wrap{position:relative}
.body-wrap .body-preview{padding-right:3rem}
.inbtn{position:absolute;right:.3rem;top:50%;transform:translateY(-50%);min-width:40px;min-height:40px;padding:0;border:0;border-radius:8px;background:transparent;color:var(--acc);font-size:1.25rem;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center}
.body-wrap .inbtn{top:.3rem;transform:none;color:#1f5fa8}
.inbtn:hover,.inbtn:focus-visible{background:var(--line)}
.ico{border:0;background:none;padding:0 .15rem;font:inherit;font-size:1.15rem;line-height:1;color:var(--acc);cursor:pointer;text-decoration:none}
.ico:hover{opacity:.75}
.ico-form{display:inline}
.ticketlist li{margin:.15rem 0;overflow-wrap:anywhere}
.lade{position:fixed;left:50%;top:4.1rem;transform:translateX(-50%);display:flex;gap:.6rem;align-items:center;background:var(--sf);border:1px solid var(--acc);border-radius:999px;padding:.45rem 1rem;box-shadow:0 4px 16px rgba(0,0,0,.2);z-index:9}
.toast{position:fixed;left:50%;bottom:8.5rem;transform:translateX(-50%);background:var(--sf);border:1px solid var(--acc);border-radius:10px;padding:.5rem 1rem;box-shadow:0 4px 16px rgba(0,0,0,.2);max-width:92vw;z-index:10}
@media (min-width:900px){.toast{bottom:1.5rem}}
.spin{width:1rem;height:1rem;border:2px solid var(--line);border-top-color:var(--acc);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spin{animation-duration:3s}}
/* Folgenkarte: ab 900 px als Tabellenzeile */
@media (min-width:900px){
.eps{gap:.35rem}
.ep{grid-template-columns:6.5rem minmax(0,1fr) 9.5rem 16rem;column-gap:.9rem;row-gap:.2rem;align-items:start;padding:.55rem .8rem}
.ep-head{display:contents}
.ep .code{grid-column:1;grid-row:1;padding-top:.45rem}
.ep-main{grid-column:2;grid-row:1}
.ep .date{grid-column:3;grid-row:1;padding-top:.5rem}
.ep-act{grid-column:4;grid-row:1;justify-content:flex-end;align-self:start}
details.more{grid-column:1/-1}
.ep-chips{margin:.15rem 0 0}
}

/* Rahmen: Seitenleiste (ab 900 px fest, darunter ausklappbar) */
.shell{min-height:100vh}
.side{background:var(--side);border-right:1px solid var(--line);padding:1rem .75rem;display:flex;flex-direction:column;gap:.2rem;overflow-y:auto}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:700;font-size:1.05rem;color:var(--fg);padding:.2rem .5rem .9rem}
.brand:hover{text-decoration:none}
.logo{width:2rem;height:2rem;border-radius:9px;background:var(--acc);color:var(--acc-fg);display:grid;place-items:center;font-size:.9rem;font-weight:800}
.nav{display:flex;align-items:center;gap:.7rem;min-height:44px;padding:.4rem .7rem;border-radius:10px;color:var(--mut);font-weight:500}
.nav svg{width:1.15rem;height:1.15rem;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.nav:hover{background:var(--hov);color:var(--fg);text-decoration:none}
.nav[aria-current=page]{background:var(--acc-soft);color:var(--acc);font-weight:600}
.side-foot{margin-top:auto;padding:1rem .3rem 0;display:grid;gap:.6rem}
.theme{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;background:var(--line);border-radius:10px;padding:2px}
.theme button{font:inherit;font-size:.82rem;min-height:36px;border:0;border-radius:8px;background:var(--side);color:var(--mut);cursor:pointer}
.theme button:hover{color:var(--fg)}
.theme button[aria-pressed=true]{background:var(--sf);color:var(--fg);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.15)}
.mbar{display:none}
.scrim{display:none}
html.js .mbar{display:flex;position:sticky;top:0;z-index:15;align-items:center;gap:.7rem;padding:.4rem .8rem;background:var(--sf);border-bottom:1px solid var(--line)}
.mbar .btn{min-width:44px;padding:0}
.mbar .brand{padding:0}
html.js .side{position:fixed;inset:0 auto 0 0;width:17rem;max-width:85vw;transform:translateX(-102%);transition:transform .2s ease;z-index:30;box-shadow:none}
html.js .side.open{transform:none;box-shadow:0 0 40px rgba(0,0,0,.35)}
html.js .scrim.open{display:block;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:25}
@media (prefers-reduced-motion:reduce){html.js .side{transition:none}}
@media (min-width:900px){
.shell{display:grid;grid-template-columns:15.5rem minmax(0,1fr)}
html.js .mbar,html.js .scrim.open{display:none}
html.js .side,.side{position:sticky;top:0;height:100vh;inset:auto;transform:none;width:auto;max-width:none;box-shadow:none;z-index:1}
}
.content{min-width:0}

/* Seitenkopf, Karten, Kacheln */
.pagehead{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:flex-end;justify-content:space-between;margin:.2rem 0 .4rem}
.pagehead h1{margin:0}
.shead{display:flex;gap:1rem;align-items:flex-start;margin:.1rem 0 .4rem}
.shead h1{margin-top:0}
.stats .stat{display:flex;gap:.75rem;align-items:center}
.stat .si{width:2.5rem;height:2.5rem;border-radius:10px;display:grid;place-items:center;background:var(--acc-soft);color:var(--acc);flex-shrink:0}
.stat .si svg{width:1.2rem;height:1.2rem;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.stat .si.w{background:var(--warn-bg);color:var(--warn)}.stat .si.g{background:var(--ok-bg);color:var(--ok)}
.card{background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.8rem .95rem}
.card h2:first-child{margin-top:.1rem}
.cols{display:grid;gap:1rem}
@media (min-width:1100px){.cols{grid-template-columns:minmax(0,3fr) minmax(0,2fr);align-items:start}}
.feed{list-style:none;margin:0;padding:0;display:grid;gap:.1rem}
.feed li{display:flex;gap:.7rem;align-items:flex-start;padding:.5rem .2rem;border-bottom:1px solid var(--line)}
.feed li:last-child{border-bottom:0}
.feed .dot{width:.6rem;height:.6rem;border-radius:50%;background:var(--acc);margin-top:.5rem;flex-shrink:0}
.feed .dot.s{background:var(--ok)}
.feed .when{margin-left:auto;white-space:nowrap;font-size:.82rem;color:var(--mut);font-variant-numeric:tabular-nums}
.searchbox{margin:.2rem 0 .6rem;max-width:26rem}

/* Poster */
.poster{position:relative;display:block;aspect-ratio:2/3;border-radius:8px;overflow:hidden;background:var(--hov);border:1px solid var(--line);flex-shrink:0;width:100%}
.poster .ph{position:absolute;inset:0;display:grid;place-items:center;font-weight:700;font-size:1.5rem;color:var(--mut);letter-spacing:.04em}
.poster img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.poster.sm{width:2.6rem;border-radius:6px}.poster.sm .ph{font-size:.9rem}
.poster.lg{width:5.5rem}
@media (min-width:900px){.poster.lg{width:7rem}}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}
@media (min-width:600px){.grid{grid-template-columns:repeat(auto-fill,minmax(10.5rem,1fr))}}
.scard{display:flex;flex-direction:column;gap:.5rem;background:var(--sf);border:1px solid var(--line);border-radius:var(--r);padding:.55rem;color:var(--fg)}
a.scard:hover{border-color:var(--acc);background:var(--hov);text-decoration:none}
.scard .t{font-weight:600;line-height:1.3;overflow-wrap:anywhere}
.scard .s{font-size:.82rem;color:var(--mut);overflow-wrap:anywhere}
.strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(5.2rem,1fr));gap:.6rem}
.strip a{display:grid;gap:.3rem;color:var(--fg);font-size:.8rem;line-height:1.25}
.strip a:hover{text-decoration:none}.strip a:hover .poster{border-color:var(--acc)}

/* Einstellungen und Diagnose */
.cfg{width:100%;border-collapse:collapse;font-size:.92rem}
.cfg th{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);text-align:left;padding:.4rem .6rem;border-bottom:1px solid var(--line)}
.cfg td{padding:.55rem .6rem;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
.cfg tr:last-child td{border-bottom:0}
.cfg code{font:.85rem ui-monospace,Menlo,monospace;color:var(--fg)}
.cfg .val{font-variant-numeric:tabular-nums}
@media (max-width:759px){
.cfg thead{display:none}
.cfg,.cfg tbody,.cfg tr,.cfg td{display:block}
.cfg tr{padding:.5rem 0;border-bottom:1px solid var(--line)}
.cfg td{border:0;padding:.1rem .2rem}
}
.diag{display:grid;gap:.6rem}
@media (min-width:760px){.diag{grid-template-columns:repeat(2,minmax(0,1fr))}}
.kv{display:grid;grid-template-columns:auto 1fr;gap:.25rem 1rem;margin:.3rem 0 0;font-size:.92rem}
.kv dt{color:var(--mut)}.kv dd{margin:0;overflow-wrap:anywhere;text-align:right}
"""
CSS = CSS.replace("@@ROOT@@", THEME_CSS)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def esc(value):
    return html.escape("" if value is None else str(value))


def de_date(iso):
    if not iso:
        return "–"
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return esc(iso)


# ---------------------------------------------------------------------------
# Skripte
# ---------------------------------------------------------------------------
LADE = """<div id='lade' class='lade' role='status' aria-live='polite' hidden><span class='spin' aria-hidden='true'></span> Bitte warten …</div>
<script>
document.addEventListener('submit',function(e){var f=e.target;
if(!f.classList||!f.classList.contains('ladeform'))return;
document.getElementById('lade').hidden=false;
f.querySelectorAll('button').forEach(function(b){b.disabled=true;});});
window.addEventListener('pageshow',function(){document.getElementById('lade').hidden=true;});
document.addEventListener('submit',function(e){if(e.target.classList&&e.target.classList.contains('ajax'))return;
try{sessionStorage.setItem('tbaScroll',JSON.stringify({p:location.pathname,y:window.scrollY}));}catch(x){}});
window.addEventListener('load',function(){try{var s=JSON.parse(sessionStorage.getItem('tbaScroll')||'null');
sessionStorage.removeItem('tbaScroll');if(s&&s.p===location.pathname)window.scrollTo(0,s.y);}catch(x){}});
</script>"""

# Haken, Verwerfen und Wieder öffnen laufen ohne Neuladen: Die Antwort ersetzt nur den Inhalt,
# die Scroll-Position bleibt, die Meldung erscheint kurz unten.
AJAX = """<div id='toast' class='toast' role='status' aria-live='polite' hidden></div>
<script>
(function(){
var toastEl=document.getElementById('toast'),timer=null;
function toast(msg){toastEl.textContent=msg;toastEl.hidden=false;clearTimeout(timer);
timer=setTimeout(function(){toastEl.hidden=true;},6000);}
function send(f){
var y=window.scrollY,id=f.id;
var body=new URLSearchParams(new FormData(f)).toString();
var fields=f.querySelectorAll('input,button,textarea,select');
fields.forEach(function(x){x.disabled=true;});
fetch(f.action,{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'text/html'},body:body})
.then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.text();})
.then(function(t){
var doc=new DOMParser().parseFromString(t,'text/html');
var nm=doc.querySelector('main'),cur=document.querySelector('main');
if(!nm||!cur)throw new Error('Seite ohne Inhalt');
cur.innerHTML=nm.innerHTML;
var fm=cur.querySelector('.flashmsg');
if(fm){toast(fm.textContent.trim());fm.parentNode.removeChild(fm);}
window.scrollTo(0,y);
var again=id?document.getElementById(id):null;
var c=again&&again.querySelector('input[type=checkbox],button');if(c)c.focus();
})
.catch(function(){
fields.forEach(function(x){x.disabled=false;});
var cb=f.querySelector('input[type=checkbox]');if(cb)cb.checked=!cb.checked;
toast('Das konnte nicht gespeichert werden. Bitte erneut versuchen.');
});}
document.addEventListener('submit',function(e){var f=e.target;
if(!f.classList||!f.classList.contains('ajax'))return;
e.preventDefault();send(f);});
})();
</script>"""

# Wechsel der Vorschläge per ◀ ▶ in der Folgenkarte. Keine Bearbeitung von Hand.
INLINE = """<script>
(function(){
function showVar(ep,i){var n=+ep.getAttribute('data-n');if(!n)return;i=(i+n)%n;
ep.querySelectorAll('[data-var]').forEach(function(el){
if(el.closest('.ep')===ep)el.hidden=(+el.getAttribute('data-var')!==i);});
ep.setAttribute('data-cur',i);
var cur=ep.querySelector('.ep-main [data-var="'+i+'"]');
if(cur)ep.setAttribute('data-state',cur.getAttribute('data-state'));
var p=ep.querySelector('.pos');if(p)p.textContent=(i+1)+' / '+n;}
document.addEventListener('click',function(e){
var nb=e.target.closest&&e.target.closest('.var-nav');if(!nb)return;
var ep=nb.closest('.ep');var d=nb.getAttribute('data-dir')==='next'?1:-1;
showVar(ep,(+ep.getAttribute('data-cur')||0)+d);});
})();
</script>"""


def csrf_fields(body):
    """Fügt in jedes POST-Formular das verborgene CSRF-Feld ein."""
    field = f"<input type='hidden' name='csrf' value='{CSRF_TOKEN}'>"
    return re.sub(r"<form method='post'[^>]*>", lambda m: m.group(0) + field, body)


_CTX = threading.local()


def set_path(path):
    """Merkt den Pfad der laufenden Anfrage (je Thread), damit die Seitenleiste den aktiven Eintrag markiert."""
    _CTX.path = path or "/"


ICONS = {
    "home": "<path d='M3 11l9-8 9 8'/><path d='M5 10v10h14V10'/>",
    "tv": "<rect x='3' y='5' width='18' height='13' rx='2'/><path d='M8 21h8'/>",
    "ticket": "<path d='M3 8a2 2 0 0 0 0 4v0a2 2 0 0 0 0 4v2h18v-2a2 2 0 0 1 0-4v0a2 2 0 0 1 0-4V6H3z'/><path d='M14 6v12' stroke-dasharray='2 3'/>",
    "gear": "<circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1'/>",
    "pulse": "<path d='M3 12h4l3-8 4 16 3-8h4'/>",
    "menu": "<path d='M4 6h16M4 12h16M4 18h16'/>",
    "list": "<path d='M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'/>",
    "clock": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
    "check": "<path d='M20 6L9 17l-5-5'/>",
}


def icon(name):
    return f"<svg viewBox='0 0 24 24' aria-hidden='true'>{ICONS[name]}</svg>"


NAV = (
    ("uebersicht", "/", "Übersicht", "home"),
    ("serien", "/serien", "Serien", "tv"),
    ("tickets", "/tickets", "Tickets", "ticket"),
    ("einstellungen", "/einstellungen", "Einstellungen", "gear"),
    ("diagnose", "/diagnose", "Diagnose", "pulse"),
)


def active_nav(path):
    path = path or "/"
    if path.startswith(("/series", "/serien")):
        return "serien"
    if path.startswith(("/ticket", "/tickets")):
        return "tickets"
    if path.startswith("/einstellungen"):
        return "einstellungen"
    if path.startswith("/diagnose"):
        return "diagnose"
    return "uebersicht"


def initials(title):
    words = [w for w in re.split(r"[\s\-–:]+", title or "") if w[:1].isalnum()]
    return esc("".join(w[0] for w in words[:2]).upper() or "?")


def poster(s, size=""):
    """Serienposter aus Sonarr (über /poster/<id>); ohne Bild erscheinen die Initialen. Abschaltbar über SHOW_POSTERS."""
    cls = f"poster {size}".strip()
    img = ""
    if SHOW_POSTERS and s.get("poster"):
        img = f"<img src='/poster/{int(s['id'])}' loading='lazy' decoding='async' alt='' onerror='this.remove()'>"
    return f"<span class='{cls}'><span class='ph' aria-hidden='true'>{initials(s.get('title'))}</span>{img}</span>"


# Farbschema-Umschalter und Menü der Seitenleiste (Wahl wird nur im Browser gespeichert)
HEAD_JS = ("<script>(function(){var d=document.documentElement;d.className='js';"
           "try{var t=localStorage.getItem('tbaTheme');if(t==='light'||t==='dark')d.setAttribute('data-theme',t);}catch(e){}})();</script>")
SHELL_JS = """<script>
(function(){
var d=document.documentElement,side=document.getElementById('side'),scrim=document.getElementById('scrim'),mb=document.getElementById('menubtn');
function menu(open){side.classList.toggle('open',open);scrim.classList.toggle('open',open);mb.setAttribute('aria-expanded',open?'true':'false');}
mb.addEventListener('click',function(){menu(!side.classList.contains('open'));});
scrim.addEventListener('click',function(){menu(false);});
document.addEventListener('keydown',function(e){if(e.key==='Escape')menu(false);});
function mark(){var cur=d.getAttribute('data-theme')||'system';
document.querySelectorAll('.theme button').forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-t')===cur?'true':'false');});}
document.querySelectorAll('.theme button').forEach(function(b){b.addEventListener('click',function(){
var v=b.getAttribute('data-t');
if(v==='system'){d.removeAttribute('data-theme');try{localStorage.removeItem('tbaTheme');}catch(e){}}
else{d.setAttribute('data-theme',v);try{localStorage.setItem('tbaTheme',v);}catch(e){}}
mark();});});
mark();
var q=document.getElementById('serienfilter');
if(q){q.addEventListener('input',function(){var v=q.value.trim().toLowerCase(),n=0;
document.querySelectorAll('[data-name]').forEach(function(el){var ok=!v||el.getAttribute('data-name').indexOf(v)>=0;el.hidden=!ok;if(ok)n++;});
var e=document.getElementById('serienleer');if(e)e.hidden=n>0;});}
})();
</script>"""


def sidebar(active):
    links = []
    for key, href, label, ic in NAV:
        aria_current = ' aria-current="page"' if key == active else ""
        links.append(
            f"<a class='nav' href='{href}'{aria_current}>"
            f"{icon(ic)}<span>{label}</span></a>"
        )
    links = "".join(links)
    return (
        "<aside class='side' id='side' aria-label='Hauptmenü'>"
        "<a class='brand' href='/'><span class='logo' aria-hidden='true'>T</span>TBA-Tickets</a>"
        f"<nav aria-label='Navigation' style='display:grid;gap:.15rem'>{links}</nav>"
        "<div class='side-foot'>"
        "<div class='theme' role='group' aria-label='Farbschema'>"
        "<button type='button' data-t='system' aria-pressed='true'>System</button>"
        "<button type='button' data-t='light' aria-pressed='false'>Hell</button>"
        "<button type='button' data-t='dark' aria-pressed='false'>Dunkel</button></div>"
        "<span class='small mut'>Liest nur aus Sonarr, dort wird nichts geändert.</span>"
        "</div></aside>"
    )


def page(title, body, active=None):
    body = csrf_fields(body)
    active = active or active_nav(getattr(_CTX, "path", "/"))
    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        f"{HEAD_JS}<title>{esc(title)} – TBA-Tickets</title><style>{CSS}</style></head>"
        "<body><div class='shell'>"
        f"{sidebar(active)}<div class='scrim' id='scrim'></div>"
        "<div class='content'>"
        "<div class='mbar'><button type='button' class='btn icon' id='menubtn' aria-label='Menü' aria-expanded='false' "
        f"aria-controls='side'>{icon('menu').replace('<svg ', '<svg width=22 height=22 fill=none stroke=currentColor stroke-width=2 stroke-linecap=round ')}</button>"
        "<a class='brand' href='/'><span class='logo' aria-hidden='true'>T</span>TBA-Tickets</a></div>"
        f"<main>{body}</main></div></div>{LADE}{AJAX}{INLINE}{SHELL_JS}</body></html>"
    )


def badge(state):
    cls = "chip warn" if state == "angekündigt" else "chip ok" if state == "gesendet" else "chip"
    return f"<span class='{cls}'>{esc(state)}</span>"


def render_error(message):
    return page(
        "Fehler",
        "<h1>Sonarr nicht erreichbar</h1>"
        f"<div class='err'>{esc(message)}</div>"
        "<p>Prüfe SONARR_URL und SONARR_API_KEY in der Compose-Datei.</p>"
        "<form method='post' action='/refresh'><button class='btn'>Erneut versuchen</button></form>",
    )
