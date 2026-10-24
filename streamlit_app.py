"""
Simple Accounting - Streamlit version.  Jet Black + Cerulean Blue + Neon Rose.  Laptop AND phone.

Run locally :   streamlit run streamlit_app.py
Deploy online:  Streamlit Community Cloud + a free Neon Postgres database

Keep simple_accounting.py (your own file), storage.py and the .streamlit/config.toml file
(dark theme colours) in the right places.

Modes (picked automatically):
    * No DATABASE_URL secret  -> LOCAL mode: your JSON files on this computer, no login needed
    * DATABASE_URL secret set -> ONLINE mode: data in the database, login (APP_PASSWORD) required

Month-wise books:
    * Every screen shows ONE period: this month (default), previous month, any month, or full history.
    * Balance-sheet accounts (Asset / Liability / Equity) carry their balance forward into the next month.
    * Revenue / Expense start from zero each month; earlier months' profit moves into
      "Retained Earnings (previous months)" on the Balance Sheet.
    * Entry numbers look like 202611001 = year 2026, month 11, entry no. 001 (restarts every month).

Needs Streamlit 1.37 or newer (pip install -U streamlit).
"""
import csv
import hashlib
import html as _html
import io
import json
import os
import secrets
import tempfile
import time
from collections import Counter
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import streamlit as st
import streamlit.components.v1 as components

from simple_accounting import (ACCOUNT_TYPES, DATA_FILE, SECURITY_FILE, Ledger,
                               export_pdf, load_default_accounts, money)
from storage import ConflictError, FileStorage, PostgresStorage

st.set_page_config(page_title="Simple Accounting", page_icon="📒", layout="wide",
                   initial_sidebar_state="collapsed")

UNLOCK_SECONDS = 300        # editing stays unlocked this long, then locks itself again
MAX_PIN_FAILS = 5           # wrong PINs allowed before a short pause
BLOCK_SECONDS = 60
IDLE_LOGOUT_SECONDS = 1800  # logged out after 30 minutes without activity

Z = Decimal("0")
PL_TYPES = ("Revenue", "Expense")
TYPE_ORDER = ["Asset", "Liability", "Equity", "Revenue", "Expense"]
TYPE_RGB = {"Asset": "30,167,253", "Liability": "255,46,136", "Equity": "150,130,255",
            "Revenue": "52,214,160", "Expense": "255,176,64"}
TYPES = [t for t in TYPE_ORDER if t in ACCOUNT_TYPES] + [t for t in ACCOUNT_TYPES if t not in TYPE_ORDER]


def type_rgb(t):
    return TYPE_RGB.get(t, "139,151,171")


def debit_normal(typ):
    return ACCOUNT_TYPES.get(typ) == "debit"


# =============================================================== settings (secrets / environment)
def secret(name, default=None):
    v = os.environ.get(name)
    if v:
        return v
    try:
        v = st.secrets.get(name)
    except Exception:  # no secrets file at all (normal when running locally)
        v = None
    return v if v not in (None, "") else default


@st.cache_resource
def setup_timezone():
    """Servers usually run on UTC. Use your local time so 'today' and timestamps are right."""
    tz = secret("APP_TIMEZONE") or ("PKT-5" if secret("DATABASE_URL") else None)
    if tz and hasattr(time, "tzset"):
        os.environ["TZ"] = tz
        time.tzset()
    return True


setup_timezone()


@st.cache_resource
def get_storage():
    url = secret("DATABASE_URL")
    if url:
        return PostgresStorage(url)
    return FileStorage({"ledger": DATA_FILE, "pin": SECURITY_FILE})


STORAGE = get_storage()


# =============================================================== look & feel  (Jet Black / Cerulean / Neon Rose)
CSS = """
:root { --bg:#07080b; --s1:#0d1118; --s2:#141a25; --line:rgba(255,255,255,.09); --tx:#e9eef7; --mut:#8b97ab;
        --blue:#1ea7fd; --blue2:#0b7fd6; --rose:#ff2e88; --rose2:#ff5fa8; }
.stApp { color: var(--tx);
  background: radial-gradient(1100px 520px at 88% -8%, rgba(30,167,253,.14), transparent 60%),
              radial-gradient(900px 480px at -8% 0%, rgba(255,46,136,.10), transparent 55%), var(--bg); }
[data-testid="stMainBlockContainer"], .block-container { max-width: 1600px; padding-top: 1.6rem; padding-bottom: 5rem; }
header[data-testid="stHeader"] { background: transparent; }
h1,h2,h3 { letter-spacing: -.01em; }
hr { border-color: var(--line) !important; }
::-webkit-scrollbar { width: 8px; height: 8px; } ::-webkit-scrollbar-thumb { background: rgba(255,255,255,.14); border-radius: 8px; }

/* ---- brand bar ---- */
.brand { display:flex; align-items:center; gap:.8rem; }
.brand .logo { width:42px; height:42px; border-radius:13px; display:grid; place-items:center; font-size:1.3rem; color:#fff; flex:0 0 auto;
  background: linear-gradient(135deg,var(--blue),var(--rose)); box-shadow: 0 0 22px rgba(30,167,253,.45), 0 0 30px rgba(255,46,136,.25); }
.brand .bt { font-size:1.28rem; font-weight:800; line-height:1.15; }
.brand .bt b { background: linear-gradient(90deg,var(--blue),var(--rose2)); -webkit-background-clip:text; background-clip:text; color:transparent; }
.brand .bs { font-size:.78rem; color:var(--mut); }
[class*="st-key-topbar"] [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; align-items:center; gap:.4rem !important; }
[class*="st-key-topbar"] [data-testid="stColumn"] { min-width:0 !important; }
[class*="st-key-topbar"] [data-testid="stColumn"]:nth-child(n+2) { flex:0 0 auto !important; width:auto !important; }

/* ---- pills (page menu, period, filters) ---- */
[class*="st-key-nav"] [role="radiogroup"] { gap:.4rem; flex-wrap:wrap; }
[class*="st-key-nav"] label[data-baseweb="radio"] { background:var(--s1); border:1px solid var(--line); border-radius:999px;
  padding:.4rem 1rem; margin:0; cursor:pointer; transition:all .14s ease; }
[class*="st-key-nav"] label[data-baseweb="radio"] > div:first-child { display:none; }
[class*="st-key-nav"] label[data-baseweb="radio"]:hover { border-color:var(--blue); box-shadow:0 0 16px -4px rgba(30,167,253,.6); }
[class*="st-key-nav"] label[data-baseweb="radio"]:has(input:checked) { background:linear-gradient(135deg,var(--blue2),var(--blue));
  border-color:transparent; box-shadow:0 8px 22px -8px rgba(30,167,253,.9); }
[class*="st-key-nav"] label[data-baseweb="radio"]:has(input:checked) * { color:#fff !important; font-weight:700; }
[class*="st-key-nav"] label[data-baseweb="radio"] p { font-size:.9rem; white-space:nowrap; }
.st-key-nav_per label[data-baseweb="radio"] { padding:.3rem .8rem; }
.st-key-nav_per label[data-baseweb="radio"]:has(input:checked) { background:rgba(255,46,136,.14); border-color:var(--rose);
  box-shadow:0 0 18px -4px rgba(255,46,136,.7); }
.st-key-nav_chip label[data-baseweb="radio"] { padding:.22rem .7rem; }
.st-key-nav_chip label[data-baseweb="radio"] p { font-size:.8rem; }

/* ---- KPI cards ---- */
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:.8rem; margin:.5rem 0 .9rem; }
.kpi { border:1px solid var(--line); border-top:3px solid rgb(var(--k)); border-radius:16px; padding:.7rem 1rem;
  background:linear-gradient(180deg,rgba(var(--k),.10),rgba(255,255,255,.015)); }
.kpi .l { font-size:.7rem; color:var(--mut); letter-spacing:.08em; text-transform:uppercase; font-weight:700; }
.kpi .v { font-size:1.45rem; font-weight:800; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.perlabel { color:var(--mut); font-size:.85rem; margin:.1rem 0 .2rem; }
.perlabel b { color:var(--tx); }

/* ---- buttons ---- */
.stButton > button, [data-testid="stFormSubmitButton"] > button, .stDownloadButton > button {
  background:var(--s2); color:var(--tx); border:1px solid var(--line); border-radius:12px; font-weight:600;
  transition:transform .08s ease, box-shadow .15s ease, border-color .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color:var(--blue); box-shadow:0 0 18px -4px rgba(30,167,253,.6); color:#fff; }
.stButton > button:active, .stDownloadButton > button:active { transform:scale(.97); }
button[data-testid="stBaseButton-primary"], button[kind="primary"] { background:linear-gradient(135deg,var(--blue2),var(--blue)) !important;
  color:#fff !important; border:none !important; box-shadow:0 10px 24px -10px rgba(30,167,253,.9); }
[class*="st-key-post_"], [class*="st-key-post_"] [data-testid="stButton"], [class*="st-key-post_"] button { width:100% !important; }
[class*="st-key-post_"] button { min-height:3.4rem; font-size:1.1rem; font-weight:800; letter-spacing:.03em;
  background:linear-gradient(135deg,#e0116f,var(--rose)) !important; box-shadow:0 0 0 1px rgba(255,46,136,.5), 0 12px 30px -8px rgba(255,46,136,.85) !important; }
[class*="st-key-post_"] button:hover { filter:brightness(1.1); }
[class*="st-key-autobal"], [class*="st-key-autobal"] [data-testid="stButton"] { width:100% !important; }
[class*="st-key-autobal"] button { border-style:dashed; border-color:var(--rose); color:var(--rose2); width:100%; }

/* ---- inputs ---- */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  background:var(--s2) !important; border-radius:12px !important; border-color:var(--line) !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within { border-color:var(--blue) !important;
  box-shadow:0 0 0 3px rgba(30,167,253,.25) !important; }
[data-testid="stExpander"] { background:var(--s1); border:1px solid var(--line) !important; border-radius:14px; }

/* ---- account picker ---- */
.thdr { color:rgb(var(--c)); font-weight:800; font-size:.72rem; letter-spacing:.12em; text-transform:uppercase;
  padding:.2rem .2rem .25rem; border-bottom:2px solid rgba(var(--c),.55); margin-bottom:.1rem; }
.st-key-picker_grid [data-testid="stVerticalBlock"] { gap:.32rem !important; }
[class*="st-key-pk_"], [class*="st-key-pk_"] [data-testid="stButton"] { width:100% !important; }
[class*="st-key-pk_"] button { width:100%; min-height:2.2rem; padding:.15rem .6rem; justify-content:flex-start; text-align:left;
  background:rgba(var(--c),.09); border:1px solid rgba(var(--c),.40); border-radius:10px; box-shadow:none; }
[class*="st-key-pk_"] button p { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin:0; font-size:.8rem; font-weight:600; text-align:left; }
[class*="st-key-pk_"] button:hover { background:rgba(var(--c),.30); border-color:rgb(var(--c)); box-shadow:0 0 16px -4px rgba(var(--c),.8); }
[class*="st-key-pk_"][class*="_selected"] button { background:rgb(var(--c)); border-color:rgb(var(--c)); box-shadow:0 0 18px -4px rgba(var(--c),.9); }
[class*="st-key-pk_"][class*="_selected"] button p { color:#04101a; font-weight:800; }

/* ---- entry screen: picker left, slip right ---- */
.st-key-entry_split { display:grid !important; grid-template-columns:3fr 2fr; gap:1.1rem; align-items:stretch; }
.st-key-slippane { position:sticky; top:3.2rem; align-self:start; border:1px solid rgba(255,46,136,.35); border-radius:18px;
  padding:.85rem .9rem; background:linear-gradient(180deg,rgba(255,46,136,.07),rgba(255,255,255,.015));
  box-shadow:0 0 40px -18px rgba(255,46,136,.6); }
.st-key-pickpane { border:1px solid var(--line); border-radius:18px; padding:.85rem .9rem; background:var(--s1); }
.ptitle { font-weight:800; font-size:1rem; margin-bottom:.35rem; display:flex; justify-content:space-between; gap:.5rem; align-items:center; }
.ptitle small { color:var(--mut); font-weight:600; }
.empty { border:2px dashed var(--line); border-radius:14px; padding:1rem; text-align:center; color:var(--mut); margin:.3rem 0 .6rem; font-size:.9rem; }
[class*="st-key-lncard_"] { background:var(--s2); border-radius:14px !important; }
[class*="st-key-lncard_"] [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:.4rem !important; align-items:center; }
[class*="st-key-lncard_"] [data-testid="stColumn"] { min-width:0 !important; }
[class*="st-key-lncard_"] input { font-size:1.08rem; font-weight:700; text-align:right; font-variant-numeric:tabular-nums; }
[class*="st-key-lncard_"] button { min-height:2.3rem; padding:.1rem .5rem; }
.lname { border-left:4px solid rgb(var(--c)); padding-left:.55rem; font-weight:700; overflow-wrap:anywhere; line-height:1.25; }
.lname small { display:block; color:var(--mut); font-weight:600; font-size:.7rem; letter-spacing:.06em; text-transform:uppercase; }
.tot { display:grid; grid-template-columns:repeat(3,1fr); gap:.5rem; margin:.4rem 0 .25rem; }
.tot > div { border-radius:12px; padding:.4rem .5rem; background:rgba(255,255,255,.05); text-align:center; }
.tot small { display:block; font-size:.62rem; letter-spacing:.1em; color:var(--mut); font-weight:700; }
.tot b { font-size:1.02rem; font-variant-numeric:tabular-nums; }
.tot.ok > div { background:rgba(52,214,160,.14); } .tot.bad > div { background:rgba(255,46,136,.12); }
.totmsg { text-align:center; font-weight:700; font-size:.86rem; margin-bottom:.5rem; min-height:1.1rem; }
.totmsg.ok { color:#34d6a0; } .totmsg.warn { color:var(--rose2); }

/* ---- tables ---- */
.tbl-wrap { overflow-x:auto; margin:.2rem 0 1rem; border:1px solid var(--line); border-radius:14px; background:var(--s1); }
.tbl { width:100%; border-collapse:collapse; font-size:.9rem; }
.tbl th { text-align:left; font-weight:700; padding:.6rem .7rem; white-space:nowrap; color:var(--blue); font-size:.74rem;
  letter-spacing:.08em; text-transform:uppercase; background:rgba(30,167,253,.08); border-bottom:1px solid var(--line); }
.tbl td { padding:.5rem .7rem; border-bottom:1px solid var(--line); vertical-align:top; overflow-wrap:anywhere; }
.tbl tbody tr:hover { background:rgba(30,167,253,.07); }
.tbl .r { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.tbl th.r { text-align:right; }
.tbl tr.sub td { font-weight:800; background:rgba(30,167,253,.12); border-top:1px solid rgba(30,167,253,.35); }
.tbl tr.head td { font-weight:800; letter-spacing:.1em; font-size:.76rem; color:var(--rose2); background:rgba(255,46,136,.07); }
.tbl .neg { color:var(--rose2); }
.tbl .code { font-family:ui-monospace,Menlo,Consolas,monospace; color:var(--blue); white-space:nowrap; }

/* ---- journal cards ---- */
.jcard { border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:14px; padding:.6rem .9rem; margin:.4rem 0;
  background:var(--s1); transition:border-color .15s ease, box-shadow .15s ease; }
.jcard:hover { border-color:var(--blue); box-shadow:0 0 22px -8px rgba(30,167,253,.6); }
.jcard .jtop { display:flex; justify-content:space-between; gap:.6rem; font-size:.82rem; color:var(--mut); }
.jcard .jno { font-family:ui-monospace,Menlo,Consolas,monospace; color:var(--blue); font-weight:700; }
.jcard .jamt { font-weight:800; color:var(--tx); font-variant-numeric:tabular-nums; white-space:nowrap; }
.jcard .jdesc { font-weight:700; margin:.12rem 0; overflow-wrap:anywhere; }
.jcard .jstat { font-size:.76rem; color:var(--mut); }
.jcard.rev { opacity:.5; border-left-color:#64748b; } .jcard.revl { border-left-color:var(--rose); }

/* ---- ledger header ---- */
.lhead { display:flex; justify-content:space-between; align-items:center; gap:1rem; flex-wrap:wrap; border:1px solid var(--line);
  border-left:5px solid rgb(var(--c)); border-radius:16px; padding:.85rem 1.1rem; margin:.9rem 0 .6rem;
  background:linear-gradient(90deg,rgba(var(--c),.16),rgba(255,255,255,.015)); }
.lhead .lh-code { font-size:.74rem; color:var(--mut); letter-spacing:.06em; text-transform:uppercase; font-weight:700; }
.lhead .lh-name { font-size:1.35rem; font-weight:800; }
.lhead .lh-note { font-size:.8rem; color:var(--mut); }
.lhead .lh-bal { text-align:right; }
.lhead .lh-bal small { display:block; font-size:.66rem; letter-spacing:.1em; color:var(--mut); font-weight:700; }
.lhead .lh-bal b { font-size:1.6rem; font-variant-numeric:tabular-nums; }

.st-key-login_box { max-width:420px; margin:2.5rem auto 0; border:1px solid var(--line); border-radius:20px; padding:1.4rem 1.5rem;
  background:var(--s1); box-shadow:0 0 60px -20px rgba(30,167,253,.5); }
[class*="st-key-"][class*="_rowbtns"] { max-width:440px; }

/* ======================= tablets / small laptops ======================= */
@media (max-width: 1250px) {
  .st-key-picker_grid [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.6rem !important; }
  .st-key-picker_grid [data-testid="stColumn"] { flex:1 1 calc(33% - .6rem) !important; min-width:calc(33% - .6rem) !important; }
}
@media (max-width: 1000px) {
  .st-key-entry_split { grid-template-columns:1fr; }
  .st-key-entry_split > :is(.st-key-slippane, :has(.st-key-slippane)) { order:-1; }
  .st-key-slippane { position:static; }
  .st-key-picker_grid { max-height:42vh; overflow-y:auto; padding-right:.25rem; }   /* picker scrolls inside, slip stays in view */
}

/* ======================= PHONES ======================= */
@media (max-width: 700px) {
  [data-testid="stMainBlockContainer"], .block-container { padding:2.6rem .55rem 4rem; }
  .brand .logo { width:36px; height:36px; font-size:1.1rem; } .brand .bt { font-size:1.08rem; } .brand .bs { font-size:.7rem; }
  .kpis { grid-template-columns:1fr 1fr; gap:.45rem; } .kpi { padding:.5rem .7rem; } .kpi .v { font-size:1.08rem; } .kpi .l { font-size:.62rem; }
  .stButton button, [data-testid="stFormSubmitButton"] button, .stDownloadButton button { min-height:2.8rem; }
  input, [data-baseweb="select"] { font-size:16px !important; }
  .st-key-picker_grid [data-testid="stColumn"] { flex:1 1 calc(50% - .6rem) !important; min-width:calc(50% - .6rem) !important; }
  [class*="st-key-nav"] [role="radiogroup"] { flex-wrap:nowrap; overflow-x:auto; padding-bottom:.3rem; -webkit-overflow-scrolling:touch; }
  [class*="st-key-nav"] label[data-baseweb="radio"] { flex:0 0 auto; }
  .st-key-slippane, .st-key-pickpane { padding:.65rem .6rem; border-radius:16px; }
  .tbl { font-size:.82rem; } .tbl th, .tbl td { padding:.4rem .45rem; }
  .lhead .lh-name { font-size:1.1rem; } .lhead .lh-bal b { font-size:1.25rem; } .lhead .lh-bal { text-align:left; }

  /* edit screen lines: account on top, Debit | Credit below */
  [class*="st-key-edit"][class*="_card"] [data-testid="stHorizontalBlock"] { flex-direction:row !important; flex-wrap:wrap !important; gap:.5rem !important; }
  [class*="st-key-edit"][class*="_card"] [data-testid="stColumn"] { min-width:0 !important; width:auto !important; flex:1 1 calc(50% - .25rem) !important; }
  [class*="st-key-edit"][class*="_card"] [data-testid="stColumn"]:nth-child(1) { flex:1 1 100% !important; }
  [class*="st-key-"][class*="_rowbtns"] [data-testid="stHorizontalBlock"] { flex-direction:row !important; flex-wrap:nowrap !important; gap:.5rem !important; }
  [class*="st-key-"][class*="_rowbtns"] [data-testid="stColumn"] { min-width:0 !important; width:auto !important; flex:1 1 0 !important; }
}
"""
CSS += "".join(f'[class*="st-key-pk_{i}_"]{{--c:{type_rgb(t)};}}\n' for i, t in enumerate(TYPES))

# JS runs inside the page itself: number keypad on phones + cursor jumps into the new line's Debit box on desktop.
_PARENT_JS = r"""
(function () {
  var last;
  function q(l) { return document.querySelectorAll('input[aria-label="' + l + '"]'); }
  function fix() {
    document.querySelectorAll('input[aria-label="Debit"], input[aria-label="Credit"], input[aria-label^="Amount"]')
      .forEach(function (i) { i.setAttribute('inputmode', 'decimal'); i.setAttribute('autocomplete', 'off'); });
    var d = q('Debit'), c = q('Credit'), n = d.length;
    if (last !== undefined && n === last + 1 && window.innerWidth > 900) {
      var t = d[n - 1], u = c[n - 1];
      if (t && u && !t.value && !u.value) { t.focus(); }
    }
    last = n;
  }
  var timer = null;
  new MutationObserver(function () { clearTimeout(timer); timer = setTimeout(fix, 100); })
      .observe(document.body, {childList: true, subtree: true});
  fix();
})();
"""
KEYPAD_HTML = ("<script>(function(){var P=window.parent;if(P.__acctKeypad)return;P.__acctKeypad=true;"
               "var s=P.document.createElement('script');s.textContent=" + json.dumps(_PARENT_JS) +
               ";P.document.head.appendChild(s);})();</script>")


def esc(x):
    return _html.escape(str(x)).replace("$", "&#36;")


def html_table(headers, rows, right=(), code_cols=()):
    """rows = [(cells, css_class)]. Everything is escaped; a newline becomes a line break."""
    head = "".join(f'<th class="{"r" if i in right else ""}">{esc(t)}</th>' for i, t in enumerate(headers))
    body = []
    for cells, cls in rows:
        tds = ""
        for i, c in enumerate(cells):
            c = str(c)
            k = ("r " if i in right else "") + ("code " if i in code_cols else "") + \
                ("neg" if i in right and c.startswith("-") else "")
            tds += f'<td class="{k.strip()}">{esc(c).replace(chr(10), "<br>")}</td>'
        body.append(f'<tr class="{cls}">{tds}</tr>')
    return (f'<div class="tbl-wrap"><table class="tbl"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def show_html(markup):
    st.html(markup)


# =============================================================== small helpers
def dec_str(d):
    """Decimal -> clean text without exponent or trailing zeros (60540.00 -> '60540')."""
    return format(Decimal(str(d)).normalize(), "f")


def amount_text(stored):
    d = Decimal(stored)
    return "" if d == 0 else dec_str(d)


def parse_amount(text):
    """'' -> None. '1,250.50' -> Decimal. Raises ValueError for bad input."""
    t = (text or "").strip().replace(",", "")
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not a number")
    if not d.is_finite() or d <= 0:
        raise ValueError("Amounts must be bigger than zero")
    if d.as_tuple().exponent < -2:
        raise ValueError("Use at most 2 decimal places")
    return Decimal(dec_str(d))


def amt_or_zero(text):
    try:
        return parse_amount(text) or Z
    except ValueError:
        return Z


def drcr(net):
    """Signed (Debit - Credit) amount -> '1,000.00 Dr' / '1,000.00 Cr' / ''."""
    if net == 0:
        return ""
    return money(abs(net)) + (" Dr" if net > 0 else " Cr")


def acct_label(led, code):
    return f"{code} - {led.accounts[code].name}" if code in led.accounts else ""


def acct_options(led, only_type=None):
    return [acct_label(led, c) for c in sorted(led.accounts)
            if only_type is None or led.accounts[c].type == only_type]


def label_code(label):
    return label.split(" - ")[0].strip()


def default_index(options, led, code):
    label = acct_label(led, code)
    return options.index(label) if label in options else None


def flash(kind, message):
    st.session_state["flash"] = (kind, message)


# ---------------------------------------------------------------- entry numbers  (YYYYMM + 3 digit counter)
def entry_codes(led):
    """{internal id: '202611001'}  - the counter restarts at 001 every month (month of the entry's date)."""
    ss = st.session_state
    m = ss.get("_codes")
    if m and m[0] == ss.get("rev", 0):
        return m[1]
    counters, out = {}, {}
    for e in led.entries:
        ym = e.date[:4] + e.date[5:7]
        counters[ym] = counters.get(ym, 0) + 1
        out[e.id] = f"{ym}{counters[ym]:03d}"
    ss["_codes"] = (ss.get("rev", 0), out)
    return out


def ecode(led, entry_id):
    return entry_codes(led).get(entry_id, f"#{entry_id}")


def entry_status(led, e):
    status = f"Reversed by {ecode(led, e.reversed_by)}" if e.reversed_by else \
        f"Reversal of {ecode(led, e.reverses)}" if e.reverses else "Active"
    n = sum(1 for a in led.audit_log if a.get("entry_id") == e.id)
    return status + (f", edited x{n}" if n else "")


def lines_table_html(led, lines):
    rows = [([acct_label(led, ln["account"]),
              money(Decimal(ln["debit"])) if Decimal(ln["debit"]) else "",
              money(Decimal(ln["credit"])) if Decimal(ln["credit"]) else ""], "") for ln in lines]
    return html_table(["Account", "Debit", "Credit"], rows, right=(1, 2))


# ---------------------------------------------------------------- periods (month-wise books)
def month_period(y, m):
    start = date(y, m, 1)
    nxt = date(y + (m == 12), (m % 12) + 1, 1)
    return {"start": start.isoformat(), "end": (nxt - timedelta(days=1)).isoformat(),
            "label": start.strftime("%B %Y"), "ym": f"{y}-{m:02d}", "kind": "month"}


ALL_PERIOD = {"start": None, "end": None, "label": "Poori History", "ym": None, "kind": "all"}
PER_LABELS = {"cur": "📅 Is Mahina", "prev": "⏮ Pichla Mahina", "pick": "🗓 Mahina Chunein", "all": "♾ Poori History"}


def in_period(ds, per):
    return (per["start"] is None or ds >= per["start"]) and (per["end"] is None or ds <= per["end"])


def period_bar(led):
    """Default = current month. Everything below follows this choice."""
    with st.container(key="nav_per"):
        mode = st.radio("Period", list(PER_LABELS), horizontal=True, key="per_mode",
                        format_func=PER_LABELS.get, label_visibility="collapsed")
    today = date.today()
    if mode == "all":
        return ALL_PERIOD
    if mode == "prev":
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return month_period(y, m)
    if mode == "pick":
        months = sorted({e.date[:7] for e in led.entries} | {today.strftime("%Y-%m")}, reverse=True)
        ym = st.selectbox("Mahina", months, key="per_pick", label_visibility="collapsed",
                          format_func=lambda x: month_period(int(x[:4]), int(x[5:7]))["label"])
        return month_period(int(ym[:4]), int(ym[5:7]))
    return month_period(today.year, today.month)


def compute_raw(led, per):
    """Opening / debit / credit / closing for every account in the period.
    Asset, Liability, Equity: opening = everything before the period (carried forward).
    Revenue, Expense: start at zero each month; earlier profit goes to `retained`."""
    s, e = per["start"], per["end"]
    agg = {}
    for ent in led.entries:
        ds = ent.date
        if e and ds > e:
            continue
        inside = s is None or ds >= s
        for ln in ent.lines:
            a = agg.setdefault(ln["account"], [Z, Z, Z])
            dr, cr = Decimal(ln["debit"]), Decimal(ln["credit"])
            if inside:
                a[1] += dr
                a[2] += cr
            else:
                a[0] += dr - cr
    rows, prev_pl = {}, Z
    for c, acc in led.accounts.items():
        o, dr, cr = agg.get(c, (Z, Z, Z))
        if acc.type in PL_TYPES:
            prev_pl += o
            o = Z
        close = o + dr - cr
        sign = 1 if debit_normal(acc.type) else -1
        rows[c] = {"code": c, "name": acc.name, "type": acc.type, "open": o, "dr": dr, "cr": cr, "close": close,
                   "nat_close": close * sign, "nat_open": o * sign, "nat_move": (dr - cr) * sign}
    retained = -prev_pl

    def tot(typ, key):
        return sum((r[key] for r in rows.values() if r["type"] == typ), Z)

    revenue, expense = tot("Revenue", "nat_move"), tot("Expense", "nat_move")
    net = revenue - expense
    equity_accts = tot("Equity", "nat_close")
    return {"rows": rows, "retained": retained, "revenue": revenue, "expense": expense, "net": net,
            "assets": tot("Asset", "nat_close"), "liabilities": tot("Liability", "nat_close"),
            "equity_accts": equity_accts, "equity": equity_accts + retained + net}


def compute(led, per):
    ss = st.session_state
    key = (ss.get("rev", 0), per["start"], per["end"])
    c = ss.get("_cmp")
    if c and c[0] == key:
        return c[1]
    res = compute_raw(led, per)
    ss["_cmp"] = (key, res)
    return res


# =============================================================== data in / out (uses YOUR engine unchanged)
def ledger_from_text(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "data.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return Ledger.load(p)


def ledger_to_text(led):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "data.json")
        led.save(p)
        with open(p, encoding="utf-8") as f:
            return f.read()


def build_pdf(led):
    """Returns (pdf_bytes, error_text). Only made when you ask for it (it is slow)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "report.pdf")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = export_pdf(led, p)
        if not ok:
            return None, buf.getvalue().strip() or "The PDF could not be created"
        with open(p, "rb") as f:
            return f.read(), ""


def drop_cache():
    """Forget the in-memory ledger so the next run reads fresh data from storage."""
    ss = st.session_state
    for k in ("led", "_cmp", "_codes"):
        ss.pop(k, None)


def conflict_stop(err):
    drop_cache()  # the in-memory copy has the unsaved change - throw it away
    st.error(f"{err}\n\nYour last change was NOT saved. Press the Refresh button (or reload the page) and do it again.")
    st.stop()


def save_all(led):
    """Save to the database (or local file). Fast: no PDF is built here."""
    ss = st.session_state
    try:
        ss["ledger_version"] = STORAGE.write("ledger", ledger_to_text(led), ss.get("ledger_version", 0))
    except ConflictError as err:
        conflict_stop(err)
    except Exception as err:
        drop_cache()
        st.error(f"Could not save to the data storage ({STORAGE.name}).\n\n{err}\n\n"
                 "Your last change was NOT saved. Press Refresh and try again.")
        st.stop()
    ss["rev"] = ss.get("rev", 0) + 1
    ss.pop("pdf_bytes", None)  # any PDF made earlier is out of date now


def load_ledger():
    ss = st.session_state
    try:
        text, version = STORAGE.read("ledger")
    except Exception as err:
        st.error(f"Could not reach the data storage ({STORAGE.name}).\n\n{err}\n\n"
                 "Nothing was changed. Wait a moment and reload the page.")
        st.stop()
    if text is None:
        led = Ledger()
    else:
        try:
            led = ledger_from_text(text)
        except Exception as err:
            st.error(f"The stored data could not be read.\n\n{err}\n\nNothing was changed.")
            st.stop()
    ss["ledger_version"] = version
    before = set(led.accounts)
    load_default_accounts(led)
    ss["rev"] = ss.get("rev", 0) + 1
    if text is None or set(led.accounts) != before:
        save_all(led)
    return led


def get_ledger():
    """The ledger is loaded ONCE per session and kept in memory (this is what makes the app fast)."""
    ss = st.session_state
    if "led" not in ss:
        ss["led"] = load_ledger()
    return ss["led"]


# =============================================================== login (online mode)
@st.cache_resource
def login_guard():
    return {"fails": 0, "last": 0.0}


def check_login():
    """Stops the page until the right username + password are given."""
    password = secret("APP_PASSWORD")
    if not password:
        if not STORAGE.local:
            st.error("Online mode needs a login password. Add APP_PASSWORD in the app's Secrets. "
                     "The app stays locked until you do.")
            st.stop()
        return  # local use without login
    ss = st.session_state
    now = time.time()
    if ss.get("auth_ok") and now - ss.get("last_active", 0) < IDLE_LOGOUT_SECONDS:
        ss["last_active"] = now
        return
    if ss.get("auth_ok"):
        st.info("You were logged out after 30 minutes without activity.")
    ss["auth_ok"] = False
    drop_cache()

    with st.container(key="login_box"):
        st.html('<div class="brand"><div class="logo">◆</div><div><div class="bt">Simple <b>Accounting</b></div>'
                '<div class="bs">Please log in</div></div></div>')
        with st.form("login_form"):
            user = st.text_input("Username", key="login_user")
            pw = st.text_input("Password", type="password", key="login_pw")
            go = st.form_submit_button("Log in", key="login_submit", type="primary")
        if go:
            g = login_guard()
            if time.time() - g["last"] > 900:
                g["fails"] = 0
            if g["fails"] >= 3:  # slow down guessing: every extra failure waits longer
                time.sleep(min(2 ** (g["fails"] - 2), 20))
            ok_user = secrets.compare_digest(user.encode(), secret("APP_USERNAME", "admin").encode())
            ok_pass = secrets.compare_digest(pw.encode(), password.encode())
            if ok_user and ok_pass:
                g["fails"] = 0
                ss["auth_ok"] = True
                ss["last_active"] = time.time()
                st.rerun()
            g["fails"] += 1
            g["last"] = time.time()
            st.error("Wrong username or password.")
    st.stop()


# =============================================================== accountant PIN (same format as the terminal program)
def pin_hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), 200_000).hex()


def pin_exists():
    return STORAGE.read("pin")[0] is not None


def create_pin(pin):
    salt = secrets.token_hex(16)
    STORAGE.write("pin", json.dumps({"pin_salt": salt, "pin_hash": pin_hash(pin, salt)}, indent=2), 0)


def verify_pin(pin):
    text = STORAGE.read("pin")[0]
    if text is None:
        return False
    cfg = json.loads(text)
    salt, expected = cfg.get("pin_salt", ""), cfg.get("pin_hash", "")
    return bool(salt and expected) and secrets.compare_digest(pin_hash(pin, salt), expected)


def is_unlocked():
    return time.time() < st.session_state.get("unlocked_until", 0)


def pin_gate(scope="default"):
    """Shows the PIN screen. Returns True only while editing is unlocked.
    `scope` must be a different string at each call site."""
    ss = st.session_state
    if is_unlocked():
        left = int(ss["unlocked_until"] - time.time())
        c1, c2 = st.columns([5, 1])
        c1.success(f"Editing is unlocked (locks again in about {max(left // 60, 0) + 1} min).")
        if c2.button("Lock now", key=f"lock_now_{scope}"):
            ss["unlocked_until"] = 0
            st.rerun()
        return True

    if not pin_exists():
        st.info("First-time setup: create the accountant-only PIN (at least 6 characters). "
                "Only the accountant should know it.")
        p1 = st.text_input("Create PIN", type="password", key=f"pin_new1_{scope}")
        p2 = st.text_input("Confirm PIN", type="password", key=f"pin_new2_{scope}")
        if st.button("Create PIN", key=f"pin_create_{scope}"):
            if len(p1) < 6:
                st.error("The PIN must be at least 6 characters.")
            elif p1 != p2:
                st.error("The two PINs do not match.")
            else:
                try:
                    create_pin(p1)
                except ConflictError as err:
                    conflict_stop(err)
                ss["unlocked_until"] = time.time() + UNLOCK_SECONDS
                flash("success", "Accountant PIN created. Editing is unlocked.")
                st.rerun()
        return False

    st.warning("This needs the accountant PIN.")
    if time.time() < ss.get("pin_block_until", 0):
        st.error("Too many wrong PINs. Please wait a minute and try again.")
        return False
    pin = st.text_input("Accountant PIN", type="password", key=f"pin_try_{scope}")
    if st.button("Unlock editing", key=f"pin_unlock_{scope}"):
        if verify_pin(pin):
            ss["unlocked_until"] = time.time() + UNLOCK_SECONDS
            ss["pin_fails"] = 0
            st.rerun()
        else:
            ss["pin_fails"] = ss.get("pin_fails", 0) + 1
            if ss["pin_fails"] >= MAX_PIN_FAILS:
                ss["pin_block_until"] = time.time() + BLOCK_SECONDS
                ss["pin_fails"] = 0
            st.error("Wrong PIN. Only an authorized accountant can do this.")
    return False


# =============================================================== the account picker (one click)
def frequent_accounts(led, n=14):
    c = Counter()
    for e in led.entries[-300:]:
        for ln in e.lines:
            c[ln["account"]] += 1
    return [code for code, _ in c.most_common(n) if code in led.accounts]


def _pick_button(code, acc, scope, on_pick, selected):
    ti = TYPES.index(acc.type) if acc.type in TYPES else 0
    st.button(f"{code}  {acc.name}", key=f"pk_{ti}_{scope}_{code}" + ("_selected" if code in selected else ""),
              on_click=on_pick, args=(code,), help=acc.name)


def account_picker(led, scope, on_pick, selected=()):
    """Every account as a coloured button. Filter chips + search keep it quick on a phone."""
    selected = set(selected)
    q = st.text_input("Search account", key=f"{scope}_q", label_visibility="collapsed",
                      placeholder="🔎 Account dhoondein (naam ya code)")
    with st.container(key="nav_chip"):
        flt = st.radio("Filter", ["Sab", "⚡ Aam"] + TYPES, horizontal=True, key=f"{scope}_f", label_visibility="collapsed")
    ql = q.strip().lower()
    items = sorted(led.accounts.items())
    with st.container(key="picker_grid"):
        cols = st.columns(len(TYPES))
        if ql or flt != "Sab":
            if ql:
                items = [(c, a) for c, a in items if ql in c.lower() or ql in a.name.lower()]
            elif flt == "⚡ Aam":
                freq = frequent_accounts(led)
                items = [(c, led.accounts[c]) for c in freq]
            else:
                items = [(c, a) for c, a in items if a.type == flt]
            if not items:
                st.caption("Kuch nahi mila." if ql else "Abhi koi entry nahi - 'Sab' chunein.")
            for i, (c, a) in enumerate(items):
                with cols[i % len(cols)]:
                    _pick_button(c, a, scope, on_pick, selected)
        else:
            groups = {t: [] for t in TYPES}
            for c, a in items:
                groups.setdefault(a.type, []).append((c, a))
            for t, col in zip(TYPES, cols):
                with col:
                    if groups.get(t):
                        st.html(f'<div class="thdr" style="--c:{type_rgb(t)}">{esc(t)}</div>')
                    for c, a in groups.get(t, []):
                        _pick_button(c, a, scope, on_pick, selected)


# =============================================================== entry slip (state helpers)
def slip_totals(exclude=None):
    ss = st.session_state
    dr = cr = Z
    for ln in ss.get("slip", []):
        if ln["id"] == exclude:
            continue
        dr += amt_or_zero(ss.get(f"ln_d_{ln['id']}", ""))
        cr += amt_or_zero(ss.get(f"ln_c_{ln['id']}", ""))
    return dr, cr


def add_line(code):
    ss = st.session_state
    dr, cr = slip_totals()
    lid = ss.get("slip_next", 0) + 1
    ss["slip_next"] = lid
    ss.setdefault("slip", []).append({"id": lid, "code": code})
    ss[f"ln_d_{lid}"] = dec_str(cr - dr) if cr > dr else ""   # the other side is filled in for you
    ss[f"ln_c_{lid}"] = dec_str(dr - cr) if dr > cr else ""


def balance_last():
    ss = st.session_state
    slip = ss.get("slip", [])
    if not slip:
        return
    lid = slip[-1]["id"]
    dr, cr = slip_totals(exclude=lid)
    diff = dr - cr
    if diff > 0:
        ss[f"ln_d_{lid}"], ss[f"ln_c_{lid}"] = "", dec_str(diff)
    elif diff < 0:
        ss[f"ln_d_{lid}"], ss[f"ln_c_{lid}"] = dec_str(-diff), ""


def remove_line(lid):
    ss = st.session_state
    ss["slip"] = [x for x in ss.get("slip", []) if x["id"] != lid]
    ss.pop(f"ln_d_{lid}", None)
    ss.pop(f"ln_c_{lid}", None)


def clear_slip():
    ss = st.session_state
    for ln in ss.get("slip", []):
        ss.pop(f"ln_d_{ln['id']}", None)
        ss.pop(f"ln_c_{ln['id']}", None)
    ss["slip"] = []
    ss["form_id"] = ss.get("form_id", 0) + 1


def rows_totals(rows):
    dr = cr = Z
    bad = False
    for _, d, c in rows:
        for text, is_debit in ((d, True), (c, False)):
            try:
                v = parse_amount(text)
            except ValueError:
                bad = True
                continue
            if v is not None:
                dr, cr = (dr + v, cr) if is_debit else (dr, cr + v)
    return dr, cr, bad


def parse_rows(rows):
    lines = []
    for n, (acct, d, c) in enumerate(rows, 1):
        if not acct and not d.strip() and not c.strip():
            continue
        if not acct:
            raise ValueError(f"Line {n}: please choose an account")
        dv, cv = parse_amount(d), parse_amount(c)
        if (dv is None) == (cv is None):
            raise ValueError(f"Line {n}: type either a Debit or a Credit amount (not both, not neither)")
        lines.append((label_code(acct), dv or Z, cv or Z))
    return lines


def totals_html(rows):
    dr, cr, bad = rows_totals(rows)
    if bad:
        cls, mcls, msg = "bad", "warn", "⚠️ Kuch amounts sahi number nahi hain"
    elif dr == cr and dr > 0:
        cls, mcls, msg = "ok", "ok", "✅ Balanced - ab POST kar dein"
    elif dr == 0 and cr == 0:
        cls, mcls, msg = "", "", ""
    else:
        cls, mcls, msg = "bad", "warn", "Debit aur Credit abhi barabar nahi"
    return (f'<div class="tot {cls}"><div><small>DEBIT</small><b>{esc(money(dr))}</b></div>'
            f'<div><small>CREDIT</small><b>{esc(money(cr))}</b></div>'
            f'<div><small>FARQ</small><b>{esc(money(abs(dr - cr)))}</b></div></div>'
            f'<div class="totmsg {mcls}">{esc(msg)}</div>')


# =============================================================== page: Entry
@st.fragment
def entry_fragment():
    """Only this block reruns while you click accounts / type amounts - so it feels instant."""
    ss = st.session_state
    led = ss["led"]
    fid = ss.setdefault("form_id", 0)
    slip = ss.setdefault("slip", [])
    used = {ln["code"] for ln in slip}

    with st.container(key="entry_split"):
        with st.container(key="pickpane"):
            st.html('<div class="ptitle">1 · Account chunein</div>')
            account_picker(led, "ent", add_line, selected=used)

        with st.container(key="slippane"):
            st.html(f'<div class="ptitle">2 · Amount likhein <small>{len(slip)} line{"s" if len(slip) != 1 else ""}</small></div>')
            c1, c2 = st.columns([2, 3])
            d = c1.date_input("Date", value=date.today(), key=f"date_{fid}", format="DD/MM/YYYY")
            desc = c2.text_input("Description", key=f"desc_{fid}", placeholder="e.g. RECV FROM KHALID")

            if not slip:
                st.html('<div class="empty">Account par click karein - line yahan aa jayegi.<br>'
                        'Doosra account chunenge to baqi amount khud bhar jayegi.</div>')
            rows = []
            for ln in list(slip):
                lid, code = ln["id"], ln["code"]
                a = led.accounts.get(code)
                rgb = type_rgb(a.type) if a else "139,151,171"
                with st.container(border=True, key=f"lncard_{lid}"):
                    t1, t2 = st.columns([8, 1])
                    t1.html(f'<div class="lname" style="--c:{rgb}">{esc(code)} - {esc(a.name if a else "?")}'
                            f'<small>{esc(a.type if a else "")}</small></div>')
                    t2.button("✕", key=f"rm_{lid}", on_click=remove_line, args=(lid,), help="Line hatayein")
                    b1, b2 = st.columns(2)
                    dtxt = b1.text_input("Debit", key=f"ln_d_{lid}", label_visibility="collapsed", placeholder="Debit")
                    ctxt = b2.text_input("Credit", key=f"ln_c_{lid}", label_visibility="collapsed", placeholder="Credit")
                rows.append((acct_label(led, code), dtxt, ctxt))

            st.html(totals_html(rows))
            dr_, cr_, bad_ = rows_totals(rows)
            if len(slip) >= 2 and dr_ != cr_ and not bad_:
                st.button("⚖ Aakhri line se farq barabar karein", key="autobal", on_click=balance_last)
            p1, p2 = st.columns([3, 1])
            post = p1.button("POST ENTRY", key="post_btn", type="primary")
            p2.button("Clear", key="clear_btn", on_click=clear_slip)

    if post:
        try:
            if not desc.strip():
                raise ValueError("Please type a description")
            lines = parse_rows(rows)
            dr, cr, _ = rows_totals(rows)
            if dr != cr:
                raise ValueError(f"Debit ({money(dr)}) aur Credit ({money(cr)}) barabar nahi hain")
            e = led.post_entry(desc.strip(), lines, d.isoformat())
        except (ValueError, InvalidOperation) as err:
            st.error(str(err))
        else:
            save_all(led)
            flash("success", f"Entry {ecode(led, e.id)} posted and saved.")
            clear_slip()
            st.rerun()


def page_entry(led, per):
    entry_fragment()


# =============================================================== page: Journal
def line_editor(led, prefix, n_rows, defaults=None):
    """One card per line: Account, Debit, Credit. defaults = [(code, debit_text, credit_text)]."""
    options = acct_options(led)
    rows = []
    for i in range(n_rows):
        code, dr0, cr0 = defaults[i] if defaults and i < len(defaults) else ("", "", "")
        with st.container(border=True, key=f"{prefix}_card{i}"):
            c1, c2, c3 = st.columns([5, 2, 2])
            acct = c1.selectbox("Account", options, index=default_index(options, led, code),
                                placeholder="Choose account (type to search)", key=f"{prefix}_a{i}",
                                label_visibility="collapsed")
            dr = c2.text_input("Debit", value=dr0, key=f"{prefix}_d{i}", label_visibility="collapsed",
                               placeholder="Debit")
            cr = c3.text_input("Credit", value=cr0, key=f"{prefix}_c{i}", label_visibility="collapsed",
                               placeholder="Credit")
        rows.append((acct or "", dr, cr))
    return rows


def show_totals(rows):
    dr, cr, bad = rows_totals(rows)
    if bad:
        st.warning("Some amounts are not valid numbers.")
    elif dr == cr and dr > 0:
        st.success(f"Debit {money(dr)}  =  Credit {money(cr)}  -  balanced, ready to save")
    else:
        st.info(f"Debit {money(dr)}  |  Credit {money(cr)}  |  Difference {money(abs(dr - cr))}")
    return dr == cr and dr > 0 and not bad


def row_buttons(count_key, minimum, prefix):
    with st.container(key=f"{prefix}_rowbtns"):
        b1, b2 = st.columns(2)
        if b1.button("Add line", key=f"{prefix}_add"):
            st.session_state[count_key] += 1
            st.rerun()
        if b2.button("Remove last line", key=f"{prefix}_rem", disabled=st.session_state[count_key] <= minimum):
            st.session_state[count_key] -= 1
            st.rerun()


def edit_panel(led, e):
    ss = st.session_state
    if e.reversed_by or e.reverses:
        st.info("This entry is already reversed (or is itself a reversal), so it cannot be edited.")
        return
    if not pin_gate(scope=f"edit{e.id}"):
        return
    ver = ss.setdefault(f"edit_ver_{e.id}", 0)
    prefix = f"edit{e.id}_{ver}"
    count_key = f"{prefix}_n"
    defaults = [(ln["account"], amount_text(ln["debit"]), amount_text(ln["credit"])) for ln in e.lines]
    ss.setdefault(count_key, len(defaults))

    c1, c2 = st.columns([1, 3])
    new_date = c1.date_input("Date", value=date.fromisoformat(e.date), key=f"{prefix}_date")
    new_desc = c2.text_input("Description", value=e.description, key=f"{prefix}_desc")
    rows = line_editor(led, prefix, ss[count_key], defaults)
    row_buttons(count_key, 2, prefix)
    show_totals(rows)
    ok = st.checkbox("I have checked the changes above and want to save them", key=f"{prefix}_ok")
    if st.button("Save changes", type="primary", key=f"{prefix}_save", disabled=not ok):
        try:
            if not new_desc.strip():
                raise ValueError("Description cannot be empty")
            lines = parse_rows(rows)
            led.post_entry_dry_run(lines)  # checks: 2+ lines, each one-sided, debits = credits
        except (ValueError, InvalidOperation) as err:
            st.error(str(err))
            return
        new_lines = [{"account": c, "debit": dec_str(d), "credit": dec_str(k)} for c, d, k in lines]

        def key(ls):
            return [(x["account"], Decimal(x["debit"]), Decimal(x["credit"])) for x in ls]

        if (new_date.isoformat() == e.date and new_desc.strip() == e.description
                and key(new_lines) == key(e.lines)):
            st.info("Nothing was changed.")
            return
        before = {"date": e.date, "description": e.description, "lines": [dict(x) for x in e.lines]}
        e.date, e.description, e.lines = new_date.isoformat(), new_desc.strip(), new_lines
        led.audit_log.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "action": "EDIT_ENTRY", "entry_id": e.id, "before": before,
            "after": {"date": e.date, "description": e.description, "lines": [dict(x) for x in e.lines]},
        })
        save_all(led)
        flash("success", f"Entry {ecode(led, e.id)} updated. The old values are kept in the edit history.")
        ss[f"edit_ver_{e.id}"] = ver + 1
        st.rerun()


def page_journal(led, per):
    codes = entry_codes(led)
    pool = [e for e in led.entries if in_period(e.date, per)]
    if not pool:
        st.info(f"{per['label']} me koi entry nahi.")
        return
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("Search", key="journal_q", label_visibility="collapsed",
                      placeholder="🔎 Description, date ya entry number")
    limit = c2.selectbox("Show", ["Last 20", "Last 50", "All"], key="journal_limit", label_visibility="collapsed")
    n = {"Last 20": 20, "Last 50": 50}.get(limit, len(pool))
    ql = q.strip().lower()
    items = []
    for e in reversed(pool):
        if ql and ql not in e.description.lower() and ql not in e.date and ql not in codes.get(e.id, ""):
            continue
        items.append(e)
        if len(items) >= n:
            break
    if not items:
        st.info("Koi entry nahi mili.")
        return

    cards = []
    for e in items:
        total = sum((Decimal(x["debit"]) for x in e.lines), Z)
        cls = "rev" if e.reversed_by else "revl" if e.reverses else ""
        cards.append(f'<div class="jcard {cls}"><div class="jtop"><span><span class="jno">{esc(codes.get(e.id, e.id))}</span>'
                     f' &middot; {esc(e.date)}</span><span class="jamt">{esc(money(total))}</span></div>'
                     f'<div class="jdesc">{esc(e.description)}</div>'
                     f'<div class="jstat">{esc(entry_status(led, e))}</div></div>')
    show_html("".join(cards))

    st.subheader("Entry kholein")
    names = {x.id: f"{codes.get(x.id, x.id)} | {x.date} | {x.description}" for x in items}
    chosen = st.selectbox("Pick an entry", list(names), format_func=lambda i: names[i], key="journal_pick")
    e = led.get_entry(chosen)
    st.caption(f"Entry {codes.get(e.id, e.id)}  -  {entry_status(led, e)}")
    show_html(lines_table_html(led, e.lines))
    hist = [a for a in led.audit_log if a.get("entry_id") == e.id]
    if hist:
        with st.expander(f"Edit history of this entry ({len(hist)})"):
            for a in reversed(hist):
                b, af = a.get("before", {}), a.get("after", {})
                st.markdown(f"**{a.get('timestamp', '?')}**  \ndate: {b.get('date')} -> {af.get('date')}  \n"
                            f"description: {b.get('description')} -> {af.get('description')}")
                if b.get("lines") != af.get("lines"):
                    st.caption("Lines before")
                    show_html(lines_table_html(led, b.get("lines", [])))
                    st.caption("Lines after")
                    show_html(lines_table_html(led, af.get("lines", [])))

    with st.container(key="nav_j"):
        mode = st.radio("Action", ["✏️ Edit (PIN)", "🗑 Delete (reverse)"], horizontal=True,
                        key="journal_mode", label_visibility="collapsed")
    if mode.startswith("✏️"):
        edit_panel(led, e)
    elif e.reversed_by or e.reverses:
        st.info("This entry is already reversed (or is itself a reversal).")
    else:
        st.caption("An opposite entry is posted, so the books stay correct and the history is kept.")
        reason = st.text_input("Reason (optional)", key=f"rev_reason_{e.id}")
        sure = st.checkbox(f"Yes, reverse entry {codes.get(e.id, e.id)}", key=f"rev_sure_{e.id}")
        if st.button("Delete (reverse) entry", key=f"rev_btn_{e.id}", disabled=not sure):
            old = codes.get(e.id, e.id)
            try:
                rev = led.reverse_entry(e.id, reason.strip())
            except ValueError as err:
                st.error(str(err))
            else:
                save_all(led)
                flash("success", f"Entry {old} cancelled by reversal entry {ecode(led, rev.id)}.")
                st.rerun()


# =============================================================== page: Ledger (one click -> opens, stays on this page)
def page_ledger(led, per):
    ss = st.session_state

    def pick(code):
        ss["ledger_code"] = code

    sel = ss.get("ledger_code")
    with st.container(key="pickpane"):
        st.html('<div class="ptitle">Account chunein - ledger foran khul jayegi</div>')
        account_picker(led, "led", pick, selected=(sel,) if sel else ())
    if sel not in led.accounts:
        st.info("Upar se koi bhi account chunein.")
        return

    a = led.accounts[sel]
    is_pl = a.type in PL_TYPES
    sign = 1 if debit_normal(a.type) else -1
    codes = entry_codes(led)
    s, e = per["start"], per["end"]

    prev = Z
    inside = []
    for ent in led.entries:
        for ln in ent.lines:
            if ln["account"] != sel:
                continue
            dr, cr = Decimal(ln["debit"]), Decimal(ln["credit"])
            if s and ent.date < s:
                prev += dr - cr
            elif e and ent.date > e:
                continue
            else:
                inside.append((ent.date, codes.get(ent.id, "?"), ent.description, dr, cr))
    inside.sort(key=lambda x: x[0])

    opening = Z if is_pl else prev * sign
    run, tdr, tcr = opening, Z, Z
    out = []
    if s:
        out.append((["", "", "Opening (naya mahina)" if is_pl else "Opening balance (pichle mahine se)", "", "", money(opening)], "sub"))
    for ds, code, desc, dr, cr in inside:
        run += (dr - cr) * sign
        tdr += dr
        tcr += cr
        out.append(([ds, code, desc, money(dr) if dr else "", money(cr) if cr else "", money(run)], ""))
    out.append((["", "", "TOTAL / Closing", money(tdr), money(tcr), money(run)], "sub"))

    note = ""
    if is_pl and s and prev != 0:
        note = f'<div class="lh-note">Pichle mahinon ka total: {esc(money(abs(prev)))} (is mahine ke hisab me shamil nahi)</div>'
    st.html(f'<div class="lhead" style="--c:{type_rgb(a.type)}"><div>'
            f'<div class="lh-code">{esc(sel)} &middot; {esc(a.type)} &middot; {esc(per["label"])} &middot; {len(inside)} transactions</div>'
            f'<div class="lh-name">{esc(a.name)}</div>{note}</div>'
            f'<div class="lh-bal"><small>CLOSING BALANCE</small><b>{esc(money(run))}</b></div></div>')
    if inside or s:
        show_html(html_table(["Date", "Entry #", "Description", "Debit", "Credit", "Balance"], out,
                             right=(3, 4, 5), code_cols=(1,)))
    else:
        st.info("Is account me abhi koi transaction nahi.")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Entry No", "Description", "Debit", "Credit", "Balance"])
    r2 = opening
    for ds, code, desc, dr, cr in inside:
        r2 += (dr - cr) * sign
        w.writerow([ds, code, desc, dec_str(dr) if dr else "", dec_str(cr) if cr else "", dec_str(r2)])
    st.download_button("⬇️ Ledger CSV", buf.getvalue().encode("utf-8"),
                       file_name=f"ledger_{sel}_{per['ym'] or 'all'}.csv", mime="text/csv", key="dl_ledger")


# =============================================================== page: Reports
def page_reports(led, per):
    with st.container(key="nav_rep"):
        which = st.radio("Report", ["Trial Balance", "Income Statement", "Balance Sheet", "Month-wise Summary"],
                         horizontal=True, key="report_pick", label_visibility="collapsed")
    if which == "Month-wise Summary":
        return report_months(led)
    res = compute(led, per)
    rows = res["rows"]
    st.html(f'<div class="perlabel">{esc(which)} - <b>{esc(per["label"])}</b></div>')

    def items(typ, key):
        return [(["    " + r["name"], money(r[key])], "") for c, r in sorted(rows.items())
                if r["type"] == typ and r[key] != 0]

    def head(label):
        return ([label, ""], "head")

    def total(label, amount):
        return ([label, money(amount)], "sub")

    if which == "Trial Balance":
        out, o_sum, d_sum, c_sum, cdr, ccr = [], Z, Z, Z, Z, Z
        for c, r in sorted(rows.items()):
            if r["open"] == 0 and r["dr"] == 0 and r["cr"] == 0:
                continue
            o_sum += r["open"]
            d_sum += r["dr"]
            c_sum += r["cr"]
            cdr += r["close"] if r["close"] > 0 else Z
            ccr += -r["close"] if r["close"] < 0 else Z
            out.append(([c + "  " + r["name"], drcr(r["open"]), money(r["dr"]) if r["dr"] else "",
                         money(r["cr"]) if r["cr"] else "", money(r["close"]) if r["close"] > 0 else "",
                         money(-r["close"]) if r["close"] < 0 else ""], ""))
        if res["retained"] != 0:
            ret = res["retained"]  # credit balance carried in from earlier months
            o_sum -= ret
            ccr += ret
            out.append((["Retained Earnings (previous months)", drcr(-ret), "", "", "", money(ret)], ""))
        out.append((["TOTAL", drcr(o_sum), money(d_sum), money(c_sum), money(cdr), money(ccr)], "sub"))
        show_html(html_table(["Account", "Opening", "Debit", "Credit", "Closing Dr", "Closing Cr"], out, right=(1, 2, 3, 4, 5)))
        ok = d_sum == c_sum and cdr == ccr
        (st.success if ok else st.error)("Balanced" if ok else "NOT BALANCED")
    elif which == "Income Statement":
        out = [head("REVENUE")] + items("Revenue", "nat_move") + [total("Total Revenue", res["revenue"])]
        out += [head("EXPENSES")] + items("Expense", "nat_move") + [total("Total Expenses", res["expense"])]
        out.append((["NET PROFIT" if res["net"] >= 0 else "NET LOSS", money(abs(res["net"]))], "sub"))
        show_html(html_table(["Item", "Amount"], out, right=(1,)))
    else:
        out = [head("ASSETS")] + items("Asset", "nat_close") + [total("Total Assets", res["assets"])]
        out += [head("LIABILITIES")] + items("Liability", "nat_close") + [total("Total Liabilities", res["liabilities"])]
        out += [head("EQUITY")] + items("Equity", "nat_close")
        if res["retained"] != 0:
            out.append((["    Retained Earnings (previous months)", money(res["retained"])], ""))
        out.append((["    Net profit / (loss) - " + per["label"], money(res["net"])], ""))
        out.append(total("Total Equity", res["equity"]))
        out.append(total("Liabilities + Equity", res["liabilities"] + res["equity"]))
        show_html(html_table(["Item", "Amount"], out, right=(1,)))
        ok = res["assets"] == res["liabilities"] + res["equity"]
        (st.success if ok else st.error)("Assets = Liabilities + Equity" if ok else "The equation does NOT balance")


def report_months(led):
    months = sorted({e.date[:7] for e in led.entries} | {date.today().strftime("%Y-%m")})
    out = []
    for ym in months:
        p = month_period(int(ym[:4]), int(ym[5:7]))
        r = compute_raw(led, p)
        n = sum(1 for e in led.entries if e.date[:7] == ym)
        out.append(([p["label"], str(n), money(r["revenue"]), money(r["expense"]), money(r["net"]),
                     money(r["assets"]), money(r["liabilities"]), money(r["equity"])], ""))
    out.reverse()
    show_html(html_table(["Month", "Entries", "Revenue", "Expenses", "Net Profit", "Assets", "Liabilities", "Equity"],
                         out, right=(1, 2, 3, 4, 5, 6, 7)))
    st.caption("Assets / Liabilities / Equity = us mahine ke end par closing balance.")


# =============================================================== page: More  (accounts, transfer, history, backup)
def tool_accounts(led, per):
    res = compute(led, per)
    q = st.text_input("Search accounts", key="acct_search", placeholder="🔎 e.g. bank or 1040", label_visibility="collapsed")
    rows = [([c, a.name, a.type, money(res["rows"][c]["nat_close"])], "") for c, a in sorted(led.accounts.items())
            if not q or q.lower() in c.lower() or q.lower() in a.name.lower()]
    st.html(f'<div class="perlabel">Closing balance - <b>{esc(per["label"])}</b></div>')
    show_html(html_table(["Code", "Account", "Type", "Balance"], rows, right=(3,), code_cols=(0,)))
    with st.expander("Add a new account"):
        with st.form("add_account_form", clear_on_submit=True):
            code = st.text_input("Code")
            name = st.text_input("Name")
            typ = st.selectbox("Type", list(ACCOUNT_TYPES))
            if st.form_submit_button("Add account", key="add_account_submit"):
                try:
                    if not code.strip() or not name.strip():
                        raise ValueError("Please type both a code and a name")
                    led.add_account(code.strip(), name.strip(), typ)
                except ValueError as err:
                    st.error(str(err))
                else:
                    save_all(led)
                    flash("success", f"Account {code.strip()} - {name.strip()} added.")
                    st.rerun()


def tool_transfer(led, per):
    st.subheader("Initial investment (opening transaction)")
    with st.form("initial_form", clear_on_submit=True):
        assets, equity = acct_options(led, "Asset"), acct_options(led, "Equity")
        amt = st.text_input("Amount invested by owner")
        dep = st.selectbox("Deposit into", assets, index=default_index(assets, led, "1010"))
        cap = st.selectbox("Capital account", equity, index=default_index(equity, led, "3000"))
        idate = st.date_input("Date", value=date.today())
        if st.form_submit_button("Post initial investment", key="initial_submit"):
            try:
                a = parse_amount(amt)
                if a is None or not dep or not cap:
                    raise ValueError("Please fill in the amount and both accounts")
                e = led.initial_investment(a, label_code(dep), label_code(cap), entry_date=idate.isoformat())
            except (ValueError, InvalidOperation) as err:
                st.error(str(err))
            else:
                save_all(led)
                flash("success", f"Initial investment posted as entry {ecode(led, e.id)}.")
                st.rerun()
    st.subheader("Transfer money between accounts")
    with st.form("transfer_form", clear_on_submit=True):
        assets = acct_options(led, "Asset")
        src = st.selectbox("From", assets, index=default_index(assets, led, "1010"))
        dst = st.selectbox("To", assets, index=default_index(assets, led, "1020"))
        amt = st.text_input("Amount")
        note = st.text_input("Description / note (optional)")
        tdate = st.date_input("Date", value=date.today())
        if st.form_submit_button("Post transfer", key="transfer_submit"):
            try:
                a = parse_amount(amt)
                if a is None or not src or not dst:
                    raise ValueError("Please fill in the amount and both accounts")
                e = led.transfer(label_code(src), label_code(dst), a, note.strip(), entry_date=tdate.isoformat())
            except (ValueError, InvalidOperation) as err:
                st.error(str(err))
            else:
                save_all(led)
                flash("success", f"Transfer posted as entry {ecode(led, e.id)}.")
                st.rerun()


def tool_history(led, per):
    if not led.audit_log:
        st.info("No entry edits have been recorded.")
        return
    rows = []
    for a in reversed(led.audit_log):
        b, af = a.get("before", {}), a.get("after", {})
        what = f"Date: {b.get('date')} -> {af.get('date')}\nDescription: {b.get('description')} -> {af.get('description')}"
        if b.get("lines") != af.get("lines"):
            what += "\nAccounts / amounts changed"
        rows.append(([a.get("timestamp", ""), ecode(led, a.get("entry_id")), what], ""))
    show_html(html_table(["When", "Entry", "What changed"], rows, code_cols=(1,)))
    st.caption("Open an entry in the Journal page to see its full before/after lines.")


def tool_backup(led, per):
    ss = st.session_state
    st.subheader("Backup")
    st.caption("Your data is stored " + ("in the online database." if not STORAGE.local else "in files on this computer.")
               + " Download a backup copy now and then. (Backup = poori history.)")
    st.download_button("Download data backup (JSON)", ledger_to_text(led).encode("utf-8"),
                       file_name=f"accounting_data_{date.today().isoformat()}.json",
                       mime="application/json", key="dl_json")

    st.subheader("PDF report")
    st.caption("PDF sirf yahan se banti hai (har entry par nahi) - isi se entry post tez hai.")
    if st.button("Create PDF report", key="make_pdf"):
        data, err = build_pdf(led)
        if data:
            ss["pdf_bytes"] = data
        else:
            st.error(err)
    if ss.get("pdf_bytes"):
        st.download_button("Download PDF report", ss["pdf_bytes"], file_name="accounting_report.pdf",
                           mime="application/pdf", key="dl_pdf")

    st.subheader("Import data from a backup file")
    st.caption("Use this once to move your existing accounting_data.json into the online database. "
               "It REPLACES everything stored now.")
    up = st.file_uploader("Choose accounting_data.json", type=["json"], key="import_file")
    if up is not None:
        try:
            new = ledger_from_text(up.getvalue().decode("utf-8"))
        except Exception as err:
            st.error(f"This file could not be read as accounting data: {err}")
            return
        st.info(f"The file has {len(new.accounts)} accounts and {len(new.entries)} entries. "
                f"Stored now: {len(led.accounts)} accounts and {len(led.entries)} entries.")
        if pin_gate(scope="import"):
            sure = st.checkbox("Yes, replace the stored data with this file", key="import_sure")
            if st.button("Import now", key="import_btn", disabled=not sure):
                try:
                    ss["ledger_version"] = STORAGE.write("ledger", ledger_to_text(new), ss.get("ledger_version", 0))
                except ConflictError as err:
                    conflict_stop(err)
                drop_cache()
                ss.pop("pdf_bytes", None)
                flash("success", f"Imported {len(new.entries)} entries and {len(new.accounts)} accounts.")
                st.rerun()


MORE = {"🏷️ Accounts": tool_accounts, "🔁 Transfer / Opening": tool_transfer,
        "🕘 History": tool_history, "💾 Backup": tool_backup}


def page_more(led, per):
    with st.container(key="nav_more"):
        pick = st.radio("More", list(MORE), horizontal=True, key="more_pick", label_visibility="collapsed")
    MORE[pick](led, per)


# =============================================================== page shell
PAGES = {
    "➕ Entry": page_entry,
    "📖 Journal": page_journal,
    "📒 Ledger": page_ledger,
    "📊 Reports": page_reports,
    "⚙️ More": page_more,
}


def kpi_html(res):
    cards = [("30,167,253", "Total Assets", res["assets"]), ("255,46,136", "Liabilities", res["liabilities"]),
             ("150,130,255", "Equity (incl. profit)", res["equity"]),
             ("52,214,160" if res["net"] >= 0 else "255,46,136", "Net Profit" if res["net"] >= 0 else "Net Loss", res["net"])]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi" style="--k:{k}"><div class="l">{esc(l)}</div><div class="v">{esc(money(v))}</div></div>'
        for k, l, v in cards) + "</div>"


def main():
    st.html(f"<style>{CSS}</style>")
    check_login()
    led = get_ledger()
    ss = st.session_state
    has_login = bool(secret("APP_PASSWORD"))

    with st.container(key="topbar"):
        top = st.columns([8, 1, 1] if has_login else [8, 1])
        top[0].html(f'<div class="brand"><div class="logo">◆</div><div><div class="bt">Simple <b>Accounting</b></div>'
                    f'<div class="bs">{esc(date.today().strftime("%A, %d %B %Y"))}</div></div></div>')
        top[1].button("⟳", key="refresh", on_click=drop_cache,
                      help="Data dobara load karein (doosre device ki entries dekhne ke liye)")
        if has_login and top[2].button("⏻", key="logout", help="Log out"):
            ss["auth_ok"] = False
            drop_cache()
            st.rerun()

    f = ss.pop("flash", None)
    if f:
        if f[0] == "success":
            st.toast(f[1], icon="✅")
        else:
            getattr(st, f[0])(f[1])

    with st.container(key="nav"):
        page = st.radio("Page", list(PAGES), horizontal=True, key="page", label_visibility="collapsed")

    per = ALL_PERIOD
    if page != "➕ Entry":   # the entry screen stays clean; every other screen follows the chosen month
        per = period_bar(led)
        st.html(kpi_html(compute(led, per)))
    PAGES[page](led, per)
    components.html(KEYPAD_HTML, height=0)


main()