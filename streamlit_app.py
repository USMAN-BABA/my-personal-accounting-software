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
import calendar
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

import altair as alt
import pandas as pd
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
    bf = os.path.join(os.path.dirname(os.path.abspath(DATA_FILE)), "budget_data.json")
    return FileStorage({"ledger": DATA_FILE, "pin": SECURITY_FILE, "budget": bf})


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

CSS += """
.bhero { --g:100,116,139; --gt:156,172,196; border:1px solid rgba(var(--g),.55); border-radius:20px; padding:1rem 1.2rem; margin:.4rem 0 .8rem;
  background:linear-gradient(135deg,rgba(var(--g),.18),rgba(255,255,255,.02)); box-shadow:0 0 44px -22px rgb(var(--g)); }
.bhero .row { display:flex; justify-content:space-between; gap:1rem; flex-wrap:wrap; align-items:flex-end; }
.bhero .l { font-size:.68rem; letter-spacing:.1em; color:var(--mut); font-weight:700; text-transform:uppercase; }
.bhero .v { font-size:1.9rem; font-weight:800; font-variant-numeric:tabular-nums; }
.bhero .v.g { color:rgb(var(--gt)); }
.bhero .pc { font-size:2.3rem; font-weight:900; color:rgb(var(--gt)); line-height:1; }
.bbar { height:14px; border-radius:99px; background:rgba(255,255,255,.08); overflow:hidden; margin:.7rem 0 .45rem; }
.bbar i { display:block; height:100%; border-radius:99px; background:linear-gradient(90deg,rgba(var(--g),.6),rgb(var(--g)));
  box-shadow:0 0 14px rgb(var(--g)); transition:width .3s ease; }
.bhero .note { font-size:.84rem; color:var(--mut); } .bhero .note b { color:rgb(var(--gt)); }
.legend { display:flex; gap:.8rem; flex-wrap:wrap; font-size:.72rem; color:var(--mut); margin-top:.4rem; }
.legend span::before { content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:3px; background:var(--lc); margin-right:.3rem; }
.alertbox { border:1px solid rgba(179,18,59,.85); background:rgba(179,18,59,.15); border-radius:16px; padding:.8rem 1rem; margin:.6rem 0; }
.alertbox b { color:#ff6f91; }
.fcast { --g:100,116,139; --gt:156,172,196; border-radius:16px; padding:.75rem 1rem; margin:.5rem 0;
  border:1px solid rgba(var(--g),.55); background:rgba(var(--g),.10); } .fcast b { color:rgb(var(--gt)); }
.chip { display:inline-block; padding:.12rem .6rem; border-radius:99px; font-size:.74rem; font-weight:700; white-space:nowrap;
  background:rgba(var(--g),.16); color:rgb(var(--gt)); border:1px solid rgba(var(--g),.5); }
.pbar { min-width:80px; height:8px; border-radius:99px; background:rgba(255,255,255,.08); overflow:hidden; }
.pbar i { display:block; height:100%; border-radius:99px; background:rgb(var(--g)); }
.up { color:#ff6f8f; font-weight:700; } .down { color:#34d6a0; font-weight:700; } .same { color:var(--mut); font-weight:700; }
[data-testid="stDataFrame"], [data-testid="stDataEditor"] { border:1px solid var(--line); border-radius:14px; overflow:hidden; }
@media (max-width: 700px) { .bhero .v { font-size:1.35rem; } .bhero .pc { font-size:1.7rem; } }
"""


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


def html_table(headers, rows, right=(), code_cols=(), raw_cols=()):
    """rows = [(cells, css_class)]. Everything is escaped; a newline becomes a line break."""
    head = "".join(f'<th class="{"r" if i in right else ""}">{esc(t)}</th>' for i, t in enumerate(headers))
    body = []
    for cells, cls in rows:
        tds = ""
        for i, c in enumerate(cells):
            c = str(c)
            k = ("r " if i in right else "") + ("code " if i in code_cols else "") + \
                ("neg" if i in right and c.startswith("-") else "")
            cell = c if i in raw_cols else esc(c).replace(chr(10), "<br>")
            tds += f'<td class="{k.strip()}">{cell}</td>'
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
    for k in ("led", "_cmp", "_codes", "bud", "_exp", "bud_undo"):
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
            msg = f"Entry {ecode(led, e.id)} posted and saved."
            try:
                msg += budget_confirm_text(led, lines, d.isoformat())   # budget plan confirm hota hai
            except Exception:
                pass
            flash("success", msg)
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


def _jump_month(ym):
    st.session_state["per_mode"] = "pick"
    st.session_state["per_pick"] = ym


def page_journal(led, per):
    codes = entry_codes(led)
    pool = [e for e in led.entries if in_period(e.date, per)]
    if not pool:
        st.info(f"{per['label']} me koi entry nahi.")
        counts = Counter(e.date[:7] for e in led.entries)
        if counts:
            st.caption("Entries in mahinon me mojood hain - kholne ke liye dabayein:")
            cols = st.columns(min(len(counts), 4))
            for i, (ym, n) in enumerate(sorted(counts.items(), reverse=True)[:4]):
                cols[i].button(f"{ym_label(ym)} ({n})", key=f"jump_{ym}", on_click=_jump_month, args=(ym,))
        else:
            st.caption("Is app ke data me abhi koi entry hi nahi hai - sahi data file / database check karein.")
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


# =============================================================== BUDGET (monthly spending plan - reads the ledger, never duplicates it)
BUDGET_KEY = "budget"
MOVE_KINDS = ["Savings", "Investment", "Receivable", "Payable", "Other"]
MOVE_LABEL = {"Savings": "💰 Savings", "Investment": "📈 Investment", "Receivable": "🤝 Account Receivable",
              "Payable": "🧾 Account Payable", "Other": "📦 Other"}
PLAN_MODES = ["Fixed", "Variable", "Daily"]
MODE_LABEL = {"Fixed": "📌 Fixed (pakka)", "Variable": "🔀 Variable (andaza)", "Daily": "📅 Daily (rozana)"}
SRC_TYPES = ["Savings se", "Udhaar (wapis dena hai)"]
C_BLUE, C_ROSE, C_ORANGE, C_RED, C_MAROON, C_GREEN = "#1ea7fd", "#ff2e88", "#ffbf69", "#ff4d4d", "#b3123b", "#34d6a0"
PALETTE = ["#1ea7fd", "#ff2e88", "#9682ff", "#34d6a0", "#ffb040", "#5fd4ff", "#ff7ab8", "#b9a8ff"]


def dnum(v):
    """Anything -> Decimal (blank / NaN / junk -> 0)."""
    try:
        if v is None or (isinstance(v, str) and not v.strip()):
            return Z
        if not isinstance(v, (str, Decimal)) and pd.isna(v):
            return Z
        d = Decimal(str(v))
        return d if d.is_finite() else Z
    except (InvalidOperation, ValueError, TypeError):
        return Z


def to_iso(v):
    try:
        if v is None or pd.isna(v):
            return date.today().isoformat()
    except (TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return pd.to_datetime(v).date().isoformat()
    except Exception:
        return date.today().isoformat()


def _txt(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    return str(v).strip()


def new_month():
    return {"opening": "0", "incomes": [], "moves": [], "shortfalls": [], "plans": []}


def shift_ym(ym, delta):
    t = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) + delta
    return f"{t // 12}-{t % 12 + 1:02d}"


def ym_label(ym):
    return month_period(int(ym[:4]), int(ym[5:7]))["label"]


def short_label(ym):
    return ym_label(ym)[:3] + " " + ym[2:4]


def plan_total(p):
    if p.get("mode") == "Daily":
        return dnum(p.get("per_day")) * dnum(p.get("days"))
    return dnum(p.get("amount"))


# ---------------------------------------------------------------- budget storage (own key, own undo stack)
def load_budget():
    ss = st.session_state
    try:
        text, ver = STORAGE.read(BUDGET_KEY)
    except Exception as err:
        ss["bud_warn"] = f"Budget ka data load nahi ho saka ({err}). Abhi ke liye sirf is session me kaam hoga."
        ss["budget_version"] = 0
        return {"months": {}}
    ss["budget_version"] = ver
    if not text:
        return {"months": {}}
    try:
        data = json.loads(text)
        data.setdefault("months", {})
        return data
    except Exception as err:
        ss["bud_warn"] = f"Budget file parh nahi saka ({err})."
        return {"months": {}}


def get_budget():
    ss = st.session_state
    if "bud" not in ss:
        ss["bud"] = load_budget()
    return ss["bud"]


def save_budget():
    ss = st.session_state
    try:
        ss["budget_version"] = STORAGE.write(BUDGET_KEY, json.dumps(ss["bud"], indent=1), ss.get("budget_version", 0))
        ss.pop("bud_warn", None)
    except ConflictError:
        ss.pop("bud", None)
        ss["bud_warn"] = "Doosre device par budget badal chuka tha - aap ka akhri badlaav save nahi hua. Dobara try karein."
    except Exception as err:
        ss["bud_warn"] = f"Budget save nahi ho saka ({err})."


def bud_push_undo():
    ss = st.session_state
    stack = ss.setdefault("bud_undo", [])
    stack.append(json.dumps(ss["bud"]))
    del stack[:-30]


def bud_undo():
    ss = st.session_state
    stack = ss.get("bud_undo") or []
    if stack:
        ss["bud"] = json.loads(stack.pop())
        save_budget()


def bud_month(bud, ym):
    m = bud["months"].setdefault(ym, new_month())
    for k, v in new_month().items():
        m.setdefault(k, v)
    return m


def bud_append(ym, key, item):
    bud = get_budget()
    bud_push_undo()
    bud_month(bud, ym)[key].append(item)
    save_budget()


# ---------------------------------------------------------------- budget maths
def expense_index(led):
    """({ym: {account: spent}}, {ym: {date: spent}}) from the ledger - one pass, cached until the ledger changes."""
    ss = st.session_state
    m = ss.get("_exp")
    if m and m[0] == ss.get("rev", 0):
        return m[1]
    by_month, daily = {}, {}
    for e in led.entries:
        ym = e.date[:7]
        for ln in e.lines:
            a = led.accounts.get(ln["account"])
            if not a or a.type != "Expense":
                continue
            amt = Decimal(ln["debit"]) - Decimal(ln["credit"])
            bm = by_month.setdefault(ym, {})
            bm[ln["account"]] = bm.get(ln["account"], Z) + amt
            dd = daily.setdefault(ym, {})
            dd[e.date] = dd.get(e.date, Z) + amt
    ss["_exp"] = (ss.get("rev", 0), (by_month, daily))
    return by_month, daily


def _calc(d, carry_in, actual):
    income = sum((dnum(x.get("amount")) for x in d["incomes"]), Z)
    moves, by_kind = Z, {}
    for x in d["moves"]:
        a = dnum(x.get("amount"))
        moves += a
        k = x.get("kind", "Other")
        by_kind[k] = by_kind.get(k, Z) + a
    cover = sum((dnum(x.get("amount")) for x in d["shortfalls"]), Z)
    opening = dnum(d.get("opening"))
    S = income - moves + cover + opening + carry_in          # money that may be spent this month
    planned_by = {}
    for p in d["plans"]:
        planned_by[p.get("account")] = planned_by.get(p.get("account"), Z) + plan_total(p)
    A = sum(actual.values(), Z)
    P = sum(planned_by.values(), Z)
    codes = set(actual) | set(planned_by)
    F = sum((max(planned_by.get(c, Z), actual.get(c, Z)) for c in codes), Z)          # forecast spend
    reserved = sum((max(planned_by.get(c, Z) - actual.get(c, Z), Z) for c in codes), Z)  # planned, not yet spent
    remaining = S - A
    has = bool(d["incomes"] or d["moves"] or d["plans"] or d["shortfalls"] or opening != 0 or carry_in != 0)
    return {"income": income, "moves": moves, "moves_by_kind": by_kind, "cover": cover, "opening": opening,
            "carry_in": carry_in, "S": S, "actual": actual, "A": A, "planned_by": planned_by, "P": P,
            "forecast": F, "forecast_left": S - F, "reserved": reserved, "remaining": remaining, "has": has,
            "unplanned": sum((v for c, v in actual.items() if c not in planned_by), Z),
            "pct": (remaining / S * 100) if S > 0 else None}


def budget_month(led, bud, ym):
    """Chain month by month: what is left at the end of one month is added to the next month's budget."""
    by_month, _ = expense_index(led)
    months = bud["months"]
    keys = sorted(months)
    carry = Z
    if keys and keys[0] <= ym:
        m = keys[0]
        while True:
            d = {**new_month(), **(months.get(m) or {})}
            res = _calc(d, carry, by_month.get(m, {}))
            if m == ym:
                break
            carry = res["remaining"] if res["has"] else Z
            m = shift_ym(m, 1)
    else:
        d = {**new_month(), **(months.get(ym) or {})}
        res = _calc(d, Z, by_month.get(ym, {}))
    res["ym"] = ym
    res["d"] = d
    return res


def outstanding_udhaar(bud):
    t = Z
    for d in bud["months"].values():
        for s in d.get("shortfalls", []):
            if str(s.get("type", "")).startswith("Udhaar") and not s.get("repaid"):
                t += dnum(s.get("amount"))
    return t


def status_color(remaining, pct, has):
    """100-51% green, 50-21% light orange, 20-0% red, below 0 maroon."""
    if not has:
        return {"g": "100,116,139", "gt": "156,172,196", "label": "Budget abhi set nahi hua - income ya plan daalein"}
    if remaining < 0:
        return {"g": "179,18,59", "gt": "255,111,145", "label": "Budget se zyada kharch ho gaya (negative)"}
    if pct is None:
        return {"g": "100,116,139", "gt": "156,172,196", "label": "Income daalein taake budget bane"}
    if pct > 50:
        return {"g": "52,214,160", "gt": "52,214,160", "label": "Sab theek - budget kaafi bacha hai"}
    if pct > 20:
        return {"g": "255,191,105", "gt": "255,191,105", "label": "Ehtiyat - aadha budget kharch ho chuka"}
    return {"g": "255,77,77", "gt": "255,77,77", "label": "Khabardar - budget bohat kam bacha hai"}


def budget_confirm_text(led, lines, ds):
    """Short text shown after a journal entry: which plan lines it confirmed."""
    bud = get_budget()
    ym = ds[:7]
    if not bud["months"].get(ym):
        return ""
    r = budget_month(led, bud, ym)
    parts, seen = [], set()
    for code, dr, cr in lines:
        a = led.accounts.get(code)
        if not a or a.type != "Expense" or code in seen:
            continue
        seen.add(code)
        p, act = r["planned_by"].get(code, Z), r["actual"].get(code, Z)
        if p > 0:
            parts.append(f"{a.name} {money(act)}/{money(p)}" + (" ✅" if act >= p else ""))
        else:
            parts.append(f"{a.name} {money(act)} (plan nahi)")
    if not parts:
        return ""
    return " · Budget: " + ", ".join(parts) + f" · Bacha: {money(r['remaining'])}"


# ---------------------------------------------------------------- html pieces
def cards_html(cards):
    return '<div class="kpis">' + "".join(
        f'<div class="kpi" style="--k:{k}"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div></div>'
        for k, l, v in cards) + "</div>"


def hero_html(r):
    sc = status_color(r["remaining"], r["pct"], r["has"])
    pct = r["pct"]
    if r["remaining"] < 0:
        width, ptxt = 100, (f"{float(pct):.0f}%" if pct is not None else "<0%")
    else:
        width, ptxt = (0, "-") if pct is None else (max(0, min(100, float(pct))), f"{float(pct):.0f}%")
    leg = "".join(f'<span style="--lc:{c}">{t}</span>' for c, t in (
        (C_GREEN, "51-100% theek"), (C_ORANGE, "21-50% ehtiyat"), (C_RED, "0-20% khatra"), (C_MAROON, "negative")))
    return (f'<div class="bhero" style="--g:{sc["g"]};--gt:{sc["gt"]}"><div class="row">'
            f'<div><div class="l">Is mahine kharch kar sakte hain</div><div class="v">{esc(money(r["S"]))}</div></div>'
            f'<div><div class="l">Abhi tak bacha hua</div><div class="v g">{esc(money(r["remaining"]))}</div></div>'
            f'<div class="pc">{esc(ptxt)}</div></div>'
            f'<div class="bbar"><i style="width:{width}%"></i></div>'
            f'<div class="note"><b>{esc(sc["label"])}</b> &middot; kharch ho chuka: {esc(money(r["A"]))}</div>'
            f'<div class="legend">{leg}</div></div>')


def confirm_table_html(led, r, all_accounts=False):
    codes = set(r["planned_by"]) | set(r["actual"])
    if all_accounts:
        codes |= {c for c, a in led.accounts.items() if a.type == "Expense"}
    codes = sorted(codes, key=lambda c: (-r["planned_by"].get(c, Z), -r["actual"].get(c, Z), str(c)))
    rows = []
    tp = ta = tr = Z
    for c in codes:
        P, A = r["planned_by"].get(c, Z), r["actual"].get(c, Z)
        name = led.accounts[c].name if c in led.accounts else str(c)
        if P > 0:
            if A <= 0:
                chip, g, gt, ratio = "⏳ Intezaar", "100,116,139", "156,172,196", 0
            elif A < P:
                chip, g, gt, ratio = "◔ Jaari", "30,167,253", "30,167,253", float(A / P * 100)
            elif A == P:
                chip, g, gt, ratio = "✅ Poora", "52,214,160", "52,214,160", 100
            else:
                chip, g, gt, ratio = "⚠ Zyada", "255,77,77", "255,111,111", 100
        elif A > 0:
            chip, g, gt, ratio = "➕ Plan nahi", "255,191,105", "255,191,105", 100
        else:
            chip, g, gt, ratio = "-", "100,116,139", "156,172,196", 0
        left = max(P - A, Z)
        tp, ta, tr = tp + P, ta + A, tr + left
        chip_html = f'<span class="chip" style="--g:{g};--gt:{gt}">{esc(chip)}</span>'
        bar = f'<div class="pbar" style="--g:{g}"><i style="width:{min(ratio, 100):.0f}%"></i></div>'
        rows.append(([name, money(P) if P else "", money(A) if A else "", money(left) if left else "", chip_html, bar], ""))
    rows.append((["TOTAL", money(tp), money(ta), money(tr), "", ""], "sub"))
    return html_table(["Expense", "Plan", "Kharch (confirmed)", "Baqi plan", "Status", "Progress"], rows,
                      right=(1, 2, 3), raw_cols=(4, 5))


# ---------------------------------------------------------------- charts (Altair - ships with Streamlit)
def style_chart(ch, height=280):
    return (ch.properties(height=height, background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor="#9fb0c8", titleColor="#9fb0c8", gridColor="rgba(255,255,255,.07)",
                            domainColor="rgba(255,255,255,.15)", tickColor="rgba(255,255,255,.15)")
            .configure_legend(labelColor="#c9d4e6", titleColor="#9fb0c8", orient="bottom")
            .configure_title(color="#e9eef7"))


def show_chart(ch, empty="Is chart ke liye abhi data nahi hai."):
    if ch is None:
        st.caption(empty)
        return
    try:
        st.altair_chart(ch, use_container_width=True, theme=None)
    except TypeError:
        st.altair_chart(ch, width="stretch", theme=None)


def fl(x):
    return float(x)


def chart_donut(rows, colors=None):
    rows = [(l, fl(v)) for l, v in rows if v > 0]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["Item", "Amount"])
    df["Share %"] = (df["Amount"] / df["Amount"].sum() * 100).round(1)
    dom = list(df["Item"])
    rng = [colors.get(i, PALETTE[n % len(PALETTE)]) for n, i in enumerate(dom)] if colors else PALETTE[:len(dom)]
    ch = alt.Chart(df).mark_arc(innerRadius=62, outerRadius=108, stroke="#07080b", strokeWidth=2).encode(
        theta=alt.Theta("Amount:Q"),
        color=alt.Color("Item:N", scale=alt.Scale(domain=dom, range=rng), legend=alt.Legend(title=None)),
        tooltip=["Item:N", alt.Tooltip("Amount:Q", format=",.0f"), alt.Tooltip("Share %:Q", format=".1f")])
    return style_chart(ch, 270)


def chart_waterfall(r):
    steps = []
    for lab, val in (("Income", r["income"]), ("Carry-in", r["carry_in"] + r["opening"]), ("Cover (udhaar/savings)", r["cover"])):
        if val != 0:
            steps.append((lab, val))
    for k in MOVE_KINDS:
        v = r["moves_by_kind"].get(k, Z)
        if v:
            steps.append((k, -v))
    if not steps and r["A"] == 0:
        return None
    rows, run = [], Z
    for lab, dlt in steps:
        rows.append((lab, fl(run), fl(run + dlt), "up" if dlt > 0 else "down"))
        run += dlt
    rows.append(("Spendable", 0.0, fl(run), "total"))
    rows.append(("Spent", fl(run), fl(run - r["A"]), "down"))
    left = run - r["A"]
    rows.append(("Remaining", 0.0, fl(left), "total" if left >= 0 else "neg"))
    df = pd.DataFrame(rows, columns=["Step", "Start", "End", "Type"])
    order = list(df["Step"])
    ch = alt.Chart(df).mark_bar(cornerRadius=4, size=34).encode(
        x=alt.X("Step:N", sort=order, title=None, axis=alt.Axis(labelAngle=-25)),
        y=alt.Y("Start:Q", title="₨"), y2="End:Q",
        color=alt.Color("Type:N", legend=None, scale=alt.Scale(domain=["up", "down", "total", "neg"],
                                                               range=[C_GREEN, C_ROSE, C_BLUE, C_MAROON])),
        tooltip=["Step:N", alt.Tooltip("Start:Q", format=",.0f"), alt.Tooltip("End:Q", format=",.0f")])
    return style_chart(ch, 290)


def chart_plan_actual(led, r):
    codes = sorted(set(r["planned_by"]) | set(r["actual"]), key=lambda c: -r["planned_by"].get(c, Z))
    rows = [(led.accounts[c].name if c in led.accounts else str(c), fl(r["planned_by"].get(c, Z)), fl(r["actual"].get(c, Z)))
            for c in codes]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["Account", "Planned", "Spent"])
    order = list(df["Account"])
    base = alt.Chart(df).encode(y=alt.Y("Account:N", sort=order, title=None))
    planned = base.mark_bar(size=22, cornerRadius=4, color=C_BLUE, opacity=0.30).encode(
        x=alt.X("Planned:Q", title="₨  (neela = plan, gulabi = kharch)"),
        tooltip=["Account:N", alt.Tooltip("Planned:Q", format=",.0f"), alt.Tooltip("Spent:Q", format=",.0f")])
    spent = base.mark_bar(size=10, cornerRadius=3).encode(
        x=alt.X("Spent:Q", title="₨  (neela = plan, gulabi = kharch)"),
        color=alt.condition("datum.Spent > datum.Planned", alt.value(C_RED), alt.value(C_ROSE)))
    return style_chart(planned + spent, max(200, 38 * len(rows) + 50))


def chart_share(led, r, base):
    if not base or base <= 0:
        return None
    rows = [(led.accounts[c].name if c in led.accounts else str(c), fl(v / base * 100)) for c, v in r["actual"].items() if v > 0]
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["Account", "Pct"]).sort_values("Pct", ascending=False)
    df["Label"] = df["Pct"].map(lambda x: f"{x:.1f}%")
    order = list(df["Account"])
    b = alt.Chart(df).encode(y=alt.Y("Account:N", sort=order, title=None))
    bars = b.mark_bar(cornerRadius=4, color=C_ROSE).encode(x=alt.X("Pct:Q", title="Income ka %"),
                                                          tooltip=["Account:N", alt.Tooltip("Pct:Q", format=".1f")])
    txt = b.mark_text(align="left", dx=4, color="#e9eef7").encode(x="Pct:Q", text="Label:N")
    return style_chart(bars + txt, max(180, 34 * len(df) + 40))


def chart_cumulative(ym, r, daily):
    y, m = int(ym[:4]), int(ym[5:7])
    n = calendar.monthrange(y, m)[1]
    today = date.today()
    upto = today.day if ym == today.strftime("%Y-%m") else (n if ym < today.strftime("%Y-%m") else 0)
    if upto <= 0:
        return None
    dd = daily.get(ym, {})
    run, rows = Z, []
    for dday in range(1, upto + 1):
        run += dd.get(f"{ym}-{dday:02d}", Z)
        rows.append((dday, fl(run)))
    df = pd.DataFrame(rows, columns=["Day", "Spent"])
    S = fl(r["S"])
    xs = alt.X("Day:Q", scale=alt.Scale(domain=[1, n]), title="Mahine ka din")
    area = alt.Chart(df).mark_area(opacity=0.22, color=C_BLUE).encode(x=xs, y=alt.Y("Spent:Q", title="₨ (jama kharch)"))
    line = alt.Chart(df).mark_line(color=C_BLUE, strokeWidth=3, point=alt.OverlayMarkDef(size=28, color=C_BLUE)).encode(
        x=xs, y="Spent:Q", tooltip=["Day:Q", alt.Tooltip("Spent:Q", format=",.0f")])
    layers = [area, line]
    if S > 0:
        pace = pd.DataFrame({"Day": [1, n], "Pace": [S / n, S]})
        layers.append(alt.Chart(pace).mark_line(color=C_ROSE, strokeDash=[5, 4]).encode(x=xs, y="Pace:Q"))
        layers.append(alt.Chart(pd.DataFrame({"y": [S]})).mark_rule(color=C_ORANGE, strokeDash=[7, 4]).encode(y="y:Q"))
    return style_chart(alt.layer(*layers), 290)


def chart_trend(led, bud, ym):
    by_month, _ = expense_index(led)
    months = [shift_ym(ym, -i) for i in range(5, -1, -1)]
    tot = {}
    for mm in months:
        for c, v in by_month.get(mm, {}).items():
            tot[c] = tot.get(c, Z) + v
    top = [c for c, _ in sorted(tot.items(), key=lambda kv: -kv[1])[:6]]
    rows = []
    for mm in months:
        other = Z
        for c, v in by_month.get(mm, {}).items():
            if c in top:
                rows.append((short_label(mm), led.accounts[c].name, fl(v)))
            else:
                other += v
        if other:
            rows.append((short_label(mm), "Other", fl(other)))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["Month", "Account", "Amount"])
    order = [short_label(mm) for mm in months]
    dom = list(dict.fromkeys(df["Account"]))
    bars = alt.Chart(df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("Month:N", sort=order, title=None), y=alt.Y("Amount:Q", title="₨ kharch"),
        color=alt.Color("Account:N", scale=alt.Scale(domain=dom, range=PALETTE[:len(dom)]), legend=alt.Legend(title=None)),
        tooltip=["Month:N", "Account:N", alt.Tooltip("Amount:Q", format=",.0f")])
    inc_rows = [(short_label(mm), fl(sum((dnum(x.get("amount")) for x in (bud["months"].get(mm) or {}).get("incomes", [])), Z)))
                for mm in months]
    if any(v for _, v in inc_rows):
        idf = pd.DataFrame(inc_rows, columns=["Month", "Income"])
        line = alt.Chart(idf).mark_line(color="#ffffff", strokeWidth=2, point=True).encode(
            x=alt.X("Month:N", sort=order), y="Income:Q", tooltip=["Month:N", alt.Tooltip("Income:Q", format=",.0f")])
        return style_chart(bars + line, 290)
    return style_chart(bars, 290)


def chart_delta(led, cur, prev):
    rows = []
    for c in set(cur) | set(prev):
        dl = cur.get(c, Z) - prev.get(c, Z)
        if dl != 0:
            rows.append((led.accounts[c].name if c in led.accounts else str(c), fl(dl)))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["Account", "Change"]).sort_values("Change", ascending=False)
    order = list(df["Account"])
    ch = alt.Chart(df).mark_bar(cornerRadius=4).encode(
        y=alt.Y("Account:N", sort=order, title=None), x=alt.X("Change:Q", title="₨ farq (pichle mahine se)"),
        color=alt.condition("datum.Change > 0", alt.value(C_ROSE), alt.value(C_GREEN)),
        tooltip=["Account:N", alt.Tooltip("Change:Q", format=",.0f")])
    return style_chart(ch, max(180, 34 * len(df) + 40))


# ---------------------------------------------------------------- editable tables (add / edit / delete in one widget)
def table_editor(ym, key, name, cols, to_rows, from_df, cfg, num_cols=(), date_cols=(), bool_cols=()):
    ss = st.session_state
    bud = get_budget()
    items = (bud["months"].get(ym) or {}).get(key, [])
    df = pd.DataFrame(to_rows(items), columns=cols)
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in date_cols:
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    for c in bool_cols:
        df[c] = df[c].astype(bool)
    ver = ss.get(f"ed_ver_{name}", 0)
    kw = dict(key=f"ed_{name}_{ym}_{ver}", num_rows="dynamic", hide_index=True, column_config=cfg)
    try:
        edited = st.data_editor(df, use_container_width=True, **kw)
    except TypeError:
        edited = st.data_editor(df, width="stretch", **kw)
    new, old = from_df(edited), from_df(df)
    if new != old:
        bud_push_undo()
        bud_month(bud, ym)[key] = new
        save_budget()
        ss[f"ed_ver_{name}"] = ver + 1
        st.rerun()


def _num_cfg(label, **kw):
    return st.column_config.NumberColumn(label, min_value=0, format="%.2f", **kw)


def editor_incomes(ym):
    def to_rows(items):
        return [(x.get("date"), x.get("desc", ""), float(dnum(x.get("amount")))) for x in items]

    def from_df(df):
        out = []
        for _, row in df.iterrows():
            amt = dnum(row["Amount"])
            if amt > 0:
                out.append({"date": to_iso(row["Date"]), "desc": _txt(row["Description"]), "amount": dec_str(amt)})
        return out

    table_editor(ym, "incomes", "inc", ["Date", "Description", "Amount"], to_rows, from_df,
                 {"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                  "Description": st.column_config.TextColumn("Source"), "Amount": _num_cfg("Amount")},
                 num_cols=["Amount"], date_cols=["Date"])


def editor_moves(ym):
    orig = {"Income": "income", "Expense budget": "withdraw"}
    back = {v: k for k, v in orig.items()}

    def to_rows(items):
        return [(x.get("date"), x.get("kind", "Other"), float(dnum(x.get("amount"))), x.get("note", ""),
                 back.get(x.get("origin"), "Income")) for x in items]

    def from_df(df):
        out = []
        for _, row in df.iterrows():
            amt, kind = dnum(row["Amount"]), _txt(row["Kind"])
            if amt > 0 and kind in MOVE_KINDS:
                out.append({"date": to_iso(row["Date"]), "kind": kind, "amount": dec_str(amt), "note": _txt(row["Note"]),
                            "origin": orig.get(_txt(row["From"]), "income")})
        return out

    table_editor(ym, "moves", "mov", ["Date", "Kind", "Amount", "Note", "From"], to_rows, from_df,
                 {"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                  "Kind": st.column_config.SelectboxColumn("Kahan gaya", options=MOVE_KINDS, required=True),
                  "Amount": _num_cfg("Amount"), "Note": st.column_config.TextColumn("Note"),
                  "From": st.column_config.SelectboxColumn("Kahan se", options=list(orig), required=True)},
                 num_cols=["Amount"], date_cols=["Date"])


def editor_shortfalls(ym):
    def to_rows(items):
        return [(x.get("date"), float(dnum(x.get("amount"))), x.get("source", ""), x.get("type", SRC_TYPES[1]),
                 bool(x.get("repaid")), x.get("note", "")) for x in items]

    def from_df(df):
        out = []
        for _, row in df.iterrows():
            amt = dnum(row["Amount"])
            if amt > 0:
                typ = _txt(row["Type"])
                out.append({"date": to_iso(row["Date"]), "amount": dec_str(amt), "source": _txt(row["Source"]),
                            "type": typ if typ in SRC_TYPES else SRC_TYPES[1],
                            "repaid": bool(row["Repaid"]) if not pd.isna(row["Repaid"]) else False, "note": _txt(row["Note"])})
        return out

    table_editor(ym, "shortfalls", "sho", ["Date", "Amount", "Source", "Type", "Repaid", "Note"], to_rows, from_df,
                 {"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"), "Amount": _num_cfg("Amount"),
                  "Source": st.column_config.TextColumn("Kis se / kahan se"),
                  "Type": st.column_config.SelectboxColumn("Zariya", options=SRC_TYPES, required=True),
                  "Repaid": st.column_config.CheckboxColumn("Chuka diya?"), "Note": st.column_config.TextColumn("Note")},
                 num_cols=["Amount"], date_cols=["Date"], bool_cols=["Repaid"])


def editor_plans(led, ym):
    opts = acct_options(led, "Expense")

    def to_rows(items):
        return [(acct_label(led, x.get("account")) or str(x.get("account")), x.get("name", ""), x.get("mode", "Fixed"),
                 float(dnum(x.get("amount"))), float(dnum(x.get("per_day"))), float(dnum(x.get("days"))),
                 float(plan_total(x))) for x in items]

    def from_df(df):
        out = []
        for _, row in df.iterrows():
            code = label_code(_txt(row["Account"]))
            mode = _txt(row["Type"])
            if code not in led.accounts or led.accounts[code].type != "Expense" or mode not in PLAN_MODES:
                continue
            daily = mode == "Daily"
            out.append({"account": code, "name": _txt(row["Item"]), "mode": mode,
                        "amount": "0" if daily else dec_str(dnum(row["Amount"])),
                        "per_day": dec_str(dnum(row["Per day"])) if daily else "0",
                        "days": dec_str(dnum(row["Days"])) if daily else "0"})
        return out

    table_editor(ym, "plans", "pln", ["Account", "Item", "Type", "Amount", "Per day", "Days", "Total"], to_rows, from_df,
                 {"Account": st.column_config.SelectboxColumn("Expense account", options=opts, required=True),
                  "Item": st.column_config.TextColumn("Item"),
                  "Type": st.column_config.SelectboxColumn("Qism", options=PLAN_MODES, required=True),
                  "Amount": _num_cfg("Amount (fixed/variable)"), "Per day": _num_cfg("Ek din ka"),
                  "Days": st.column_config.NumberColumn("Din", min_value=0, step=1, format="%d"),
                  "Total": st.column_config.NumberColumn("Total", format="%.2f", disabled=True)},
                 num_cols=["Amount", "Per day", "Days", "Total"])


# ---------------------------------------------------------------- guided flows
def _fill(key, val):
    st.session_state[key] = val


@st.fragment
def income_flow(ym):
    """Income dalte hi software poochta hai: savings / investment / receivable / payable me kitne jayenge."""
    ss = st.session_state
    led, bud = ss["led"], get_budget()
    fk = ss.setdefault("inc_form", 0)

    def k(n):
        return f"inc_{n}_{fk}"

    st.html('<div class="ptitle">1 · Income daalein &nbsp;→&nbsp; 2 · batayein kahan kahan jayegi</div>')
    c1, c2, c3 = st.columns([2, 3, 2])
    amt_t = c1.text_input("Amount - Income (₨)", key=k("amt"), placeholder="e.g. 100000")
    desc = c2.text_input("Income ka source", key=k("desc"), placeholder="Salary / Freelance ...")
    dt = c3.date_input("Date", value=date.today(), key=k("date"), format="DD/MM/YYYY")

    led_rev = compute(led, month_period(int(ym[:4]), int(ym[5:7])))["revenue"]
    booked = sum((dnum(x.get("amount")) for x in (bud["months"].get(ym) or {}).get("incomes", [])), Z)
    if led_rev - booked > 0:
        st.button(f"📥 Ledger ki income bharein ({money(led_rev - booked)})", key=k("fill"), on_click=_fill,
                  args=(k("amt"), dec_str(led_rev - booked)))

    owe = outstanding_udhaar(bud)
    a1, a2 = st.columns(2)
    sav = a1.text_input("Amount - Savings me", key=k("sav"), placeholder="0")
    inv = a2.text_input("Amount - Investment me", key=k("inv"), placeholder="0")
    a3, a4 = st.columns(2)
    rec = a3.text_input("Amount - Account Receivable me (kisi ko dena)", key=k("rec"), placeholder="0")
    pay = a4.text_input("Amount - Account Payable me (qarz chukana)", key=k("pay"), placeholder=dec_str(owe) if owe > 0 else "0")
    if owe > 0:
        st.caption(f"Pichla udhaar baqi hai: {money(owe)} - payable me daalna na bhoolen.")

    income = amt_or_zero(amt_t)
    alloc = amt_or_zero(sav) + amt_or_zero(inv) + amt_or_zero(rec) + amt_or_zero(pay)
    spend = income - alloc
    pct = (spend / income * 100) if income > 0 else None
    sc = status_color(spend, pct, income > 0)
    pct_txt = "" if pct is None else f" ({float(pct):.0f}%)"
    st.html(f'<div class="fcast" style="--g:{sc["g"]};--gt:{sc["gt"]}"><b>Expense ke liye bachega: {esc(money(spend))}'
            f'{pct_txt}</b><br><span class="lh-note">{esc(sc["label"])}</span></div>')
    if dt.isoformat()[:7] != ym:
        st.caption(f"Note: ye income {ym_label(dt.isoformat()[:7])} ke budget me jayegi.")

    if st.button("💾 Income aur allocation save karein", key=k("save"), type="primary"):
        if income <= 0:
            st.error("Income ki raqam likhein.")
        elif spend < 0:
            st.error("Allocation income se zyada hai - raqam kam karein.")
        else:
            tm = dt.isoformat()[:7]
            bud_push_undo()
            m = bud_month(bud, tm)
            m["incomes"].append({"date": dt.isoformat(), "desc": desc.strip(), "amount": dec_str(income)})
            for kind, v in (("Savings", sav), ("Investment", inv), ("Receivable", rec), ("Payable", pay)):
                a = amt_or_zero(v)
                if a > 0:
                    m["moves"].append({"date": dt.isoformat(), "kind": kind, "amount": dec_str(a), "note": "", "origin": "income"})
            save_budget()
            ss["inc_form"] = fk + 1
            flash("success", f"Income save: {ym_label(tm)} ke liye {money(spend)} kharch budget bana.")
            st.rerun()


@st.fragment
def plan_flow(ym):
    """Fixed / Variable / Daily kharcha - software poochta hai ek din ka kitna aur kitne din."""
    ss = st.session_state
    led = ss["led"]
    by_month, _ = expense_index(led)
    fk = ss.setdefault("plan_form", 0)
    opts = acct_options(led, "Expense")
    if not opts:
        st.info("Koi Expense account nahi mila.")
        return

    def k(n):
        return f"pl_{n}_{fk}"

    st.html('<div class="ptitle">➕ Naya kharcha plan karein</div>')
    c1, c2 = st.columns(2)
    acct = c1.selectbox("Expense account", opts, key=k("acct"))
    name = c2.text_input("Item ka naam", key=k("name"), placeholder="e.g. University petrol")
    code = label_code(acct)
    last = by_month.get(shift_ym(ym, -1), {}).get(code, Z)
    if last > 0:
        st.caption(f"Pichle mahine is account me {money(last)} kharch hua tha.")
    mode = st.radio("Ye kharcha kis tarah ka hai?", PLAN_MODES, horizontal=True, key=k("mode"), format_func=MODE_LABEL.get)
    if mode == "Daily":
        d1, d2 = st.columns(2)
        per_day = d1.text_input("Amount - ek din ka kharcha", key=k("pd"), placeholder="e.g. 300")
        days = d2.text_input("Is mahine kitne din?", key=k("days"), placeholder="e.g. 20")
        total = amt_or_zero(per_day) * amt_or_zero(days)
    else:
        amt = st.text_input("Amount - is mahine ka kharcha", key=k("amt"), placeholder="e.g. 15000")
        total = amt_or_zero(amt)
    st.caption(f"Is item ka total: {money(total)}")
    if st.button("➕ Plan me add karein", key=k("add"), type="primary"):
        if total <= 0:
            st.error("Amount (aur daily ho to din) likhein.")
        else:
            item = {"account": code, "name": name.strip(), "mode": mode, "amount": "0", "per_day": "0", "days": "0"}
            if mode == "Daily":
                item.update(per_day=dec_str(amt_or_zero(per_day)), days=dec_str(amt_or_zero(days)))
            else:
                item["amount"] = dec_str(total)
            bud_append(ym, "plans", item)
            ss["plan_form"] = fk + 1
            flash("success", f"Plan add: {led.accounts[code].name} {money(total)}.")
            st.rerun()


def shortfall_prompt(ym, need):
    st.html(f'<div class="alertbox"><b>⚠ Budget se {esc(money(need))} zyada kharch ho gaya.</b><br>'
            'Ye raqam kahan se aa rahi hai? Agar udhaar hai to agli income aane par chukana parega.</div>')
    with st.form("short_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        typ = c1.selectbox("Zariya", SRC_TYPES)
        who = c2.text_input("Kis se / kahan se", placeholder="e.g. Ali bhai / Credit card")
        amt = st.text_input("Amount - kami ki raqam", value=dec_str(need))
        if st.form_submit_button("✅ Source save karein"):
            a = amt_or_zero(amt)
            if a <= 0:
                st.error("Raqam likhein.")
            else:
                bud_append(ym, "shortfalls", {"date": date.today().isoformat(), "amount": dec_str(a),
                                              "source": who.strip() or typ, "type": typ, "repaid": False, "note": ""})
                flash("success", "Kami ka source save ho gaya - budget me add ho gaya.")
                st.rerun()


# ---------------------------------------------------------------- budget page
def bud_overview(led, bud, ym, r):
    st.html(hero_html(r))
    if r["has"] and r["remaining"] < 0:
        shortfall_prompt(ym, -r["remaining"])
    st.html(cards_html([("30,167,253", "Income", money(r["income"])),
                        ("255,46,136", "Savings / investment etc.", money(r["moves"])),
                        ("255,176,64", "Pichla bacha + cover", money(r["carry_in"] + r["opening"] + r["cover"])),
                        ("52,214,160", "Kharch budget", money(r["S"]))]))
    st.html(cards_html([("150,130,255", "Plan ka total", money(r["P"])),
                        ("255,46,136", "Kharch ho chuka", money(r["A"])),
                        ("30,167,253", "Plan me baqi", money(r["reserved"])),
                        ("52,214,160" if r["forecast_left"] >= 0 else "255,77,77", "Month-end andaza", money(r["forecast_left"]))]))

    if r["has"]:
        fc = r["forecast_left"]
        if fc >= 0:
            g, gt = "52,214,160", "52,214,160"
            msg = (f"✅ Agar diye gaye plan ke mutabiq chale to month end par <b>{esc(money(fc))}</b> bach sakte hain. "
                   "Jo bachega wo agle mahine ke budget me khud add ho jayega.")
        else:
            g, gt = "179,18,59", "255,111,145"
            msg = (f"⚠ Plan ke mutabiq chale to month end par <b>{esc(money(-fc))}</b> ki kami (negative) hogi. "
                   "Plan kam karein ya income / source ka intezam karein.")
        st.html(f'<div class="fcast" style="--g:{g};--gt:{gt}">{msg}</div>')
        gap = r["S"] - r["P"]
        st.html(f'<div class="perlabel">Pehle socha hua budget <b>{esc(money(r["S"]))}</b> &nbsp;vs&nbsp; ab ka plan '
                f'(fixed + variable + daily) <b>{esc(money(r["P"]))}</b> &nbsp;→&nbsp; farq '
                f'<b style="color:{C_GREEN if gap >= 0 else C_RED}">{"+" if gap >= 0 else "-"}{esc(money(abs(gap)))}</b> '
                f'({"plan budget se kam hai - safe side" if gap >= 0 else "plan budget se zyada hai"})</div>')

    st.html('<div class="ptitle">✅ Kharch confirmation <small>journal entry karte hi khud update</small></div>')
    show_html(confirm_table_html(led, r))

    with st.expander("💸 Bachi hui raqam expense budget se nikalein (savings / investment / receivable ...)"):
        with st.form("wd_form", clear_on_submit=True):
            kind = st.selectbox("Kahan lagani hai", MOVE_KINDS, format_func=MOVE_LABEL.get)
            amt = st.text_input("Amount - nikalni hai")
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Budget se nikalein"):
                a = amt_or_zero(amt)
                if a <= 0:
                    st.error("Raqam likhein.")
                elif a > max(r["remaining"], Z):
                    st.error(f"Abhi sirf {money(max(r['remaining'], Z))} bacha hai - us se zyada nahi nikal sakte.")
                else:
                    bud_append(ym, "moves", {"date": date.today().isoformat(), "kind": kind, "amount": dec_str(a),
                                             "note": note.strip(), "origin": "withdraw"})
                    flash("success", f"{money(a)} expense budget se {kind} me chali gayi.")
                    st.rerun()
    st.caption("Asal paisa kisi account me bhejna ho to Entry page par normal journal entry (ya More → Transfer) karein.")


def bud_income(led, bud, ym, r):
    income_flow(ym)
    st.divider()
    owe = outstanding_udhaar(bud)
    if owe > 0:
        st.html(f'<div class="alertbox"><b>Udhaar baqi: {esc(money(owe))}</b> - agli income me Account Payable me daalein.</div>')
    st.html('<div class="ptitle">Incomes <small>edit / delete / nayi row</small></div>')
    editor_incomes(ym)
    st.html('<div class="ptitle">Allocations aur nikaasi <small>savings, investment, receivable, payable</small></div>')
    editor_moves(ym)
    st.html('<div class="ptitle">Kami ke source <small>jahan se kami poori ki</small></div>')
    editor_shortfalls(ym)


def bud_plan(led, bud, ym, r):
    gap = r["S"] - r["P"]
    st.html(cards_html([("255,176,64", "Pehle socha hua budget", money(r["S"])),
                        ("150,130,255", "Ab ka plan (fixed+variable+daily)", money(r["P"])),
                        ("52,214,160" if gap >= 0 else "255,77,77", "Farq", ("+" if gap >= 0 else "-") + money(abs(gap))),
                        ("30,167,253", "Month-end andaza", money(r["forecast_left"]))]))
    plan_flow(ym)
    st.html('<div class="ptitle">Saare plan items <small>edit / delete / nayi row</small></div>')
    editor_plans(led, ym)
    st.html('<div class="ptitle">Saare Expense accounts <small>plan vs asal kharch</small></div>')
    show_html(confirm_table_html(led, r, all_accounts=True))


def bud_analysis(led, bud, ym, r):
    by_month, daily = expense_index(led)
    cur, prev = by_month.get(ym, {}), by_month.get(shift_ym(ym, -1), {})
    base = r["income"] if r["income"] > 0 else (r["S"] if r["S"] > 0 else None)
    tc, tp = sum(cur.values(), Z), sum(prev.values(), Z)
    dl = tc - tp
    pc = f"{float(dl / tp * 100):+.1f}%" if tp > 0 else "-"
    st.html(cards_html([("255,46,136", "Is mahine kharch", money(tc)), ("30,167,253", "Pichle mahine kharch", money(tp)),
                        ("255,77,77" if dl > 0 else "52,214,160", "Farq (₨)", ("+" if dl > 0 else "-" if dl < 0 else "") + money(abs(dl))),
                        ("255,77,77" if dl > 0 else "52,214,160", "Farq (%)", pc)]))

    rows = []
    for c in sorted(set(cur) | set(prev), key=lambda x: -cur.get(x, Z)):
        c_, p_ = cur.get(c, Z), prev.get(c, Z)
        d_ = c_ - p_
        cls, arrow, word = ("up", "▲", "badha") if d_ > 0 else ("down", "▼", "kam hua") if d_ < 0 else ("same", "━", "no change")
        pch = f"{float(d_ / p_ * 100):+.1f}%" if p_ > 0 else ("naya" if c_ > 0 else "0%")
        share = f"{float(c_ / base * 100):.1f}%" if base else "-"
        diff_html = (f'<span class="{cls}">{arrow} {esc(money(abs(d_)))} ({esc(word)})</span>' if d_
                     else '<span class="same">━ no change</span>')
        rows.append(([led.accounts[c].name if c in led.accounts else str(c), money(p_) if p_ else "", money(c_) if c_ else "",
                      diff_html, f'<span class="{cls}">{esc(pch)}</span>', share], ""))
    if rows:
        show_html(html_table(["Expense", "Pichla mahina", "Is mahina", "Farq ₨", "Farq %", "Income ka %"], rows,
                             right=(1, 2, 3, 4, 5), raw_cols=(3, 4)))
        if base:
            top = sorted(((v, c) for c, v in cur.items() if v > 0), reverse=True)[:3]
            if top:
                st.html('<div class="perlabel">Income ka sab se zyada hissa: ' + " &nbsp;·&nbsp; ".join(
                    f'<b>{esc(led.accounts[c].name if c in led.accounts else c)}</b> {float(v / base * 100):.1f}%' for v, c in top) + "</div>")
    else:
        st.info("Is mahine ya pichle mahine koi expense nahi mila.")

    g1, g2 = st.columns(2)
    with g1:
        st.html('<div class="ptitle">Income kahan gayi</div>')
        rows_d = [(MOVE_LABEL[k].split(" ", 1)[1], v) for k, v in r["moves_by_kind"].items() if k in MOVE_LABEL]
        rows_d.append(("Kharch budget", max(r["S"], Z)))
        show_chart(chart_donut(rows_d), "Income daalein to ye chart banega.")
    with g2:
        st.html('<div class="ptitle">Kharcha kahan gaya</div>')
        show_chart(chart_donut([(led.accounts[c].name if c in led.accounts else str(c), v) for c, v in cur.items()]),
                   "Is mahine abhi koi expense nahi.")
    st.html('<div class="ptitle">Income se bacha-kharcha (waterfall)</div>')
    show_chart(chart_waterfall(r), "Income ya kharch daalein.")
    st.html('<div class="ptitle">Plan vs asal kharch</div>')
    show_chart(chart_plan_actual(led, r), "Plan ya kharch daalein.")
    g3, g4 = st.columns(2)
    with g3:
        st.html('<div class="ptitle">Kharch income ka kitna % kha raha hai</div>')
        show_chart(chart_share(led, r, base), "Income daalein taake % nikle.")
    with g4:
        st.html('<div class="ptitle">Pichle mahine se farq</div>')
        show_chart(chart_delta(led, cur, prev), "Pichle mahine se koi farq nahi.")
    st.html('<div class="ptitle">Roz ka jama kharch vs budget</div>')
    show_chart(chart_cumulative(ym, r, daily), "Is mahine abhi kharch nahi hua.")
    st.caption("🟦 asli jama kharch &nbsp;·&nbsp; gulabi lakeer = barabar raftaar se budget ka hisaab &nbsp;·&nbsp; orange lakeer = poora budget")
    st.html('<div class="ptitle">Pichle 6 mahine ka kharch (safed lakeer = income)</div>')
    show_chart(chart_trend(led, bud, ym), "Abhi itna data nahi.")


BUD_SECTIONS = {"📊 Overview": bud_overview, "💰 Income": bud_income, "🧮 Plan": bud_plan, "📈 Analysis": bud_analysis}


def page_budget(led, per):
    ss = st.session_state
    bud = get_budget()
    today_ym = date.today().strftime("%Y-%m")
    if per["kind"] == "month":
        ym = per["ym"]
    else:
        ym = today_ym
        st.caption("Budget hamesha ek mahine ka hota hai - is mahine ka dikha rahe hain. Doosra mahina upar se chunein.")
    if ss.get("bud_warn"):
        st.warning(ss["bud_warn"])
    n_undo = len(ss.get("bud_undo") or [])
    c1, c2 = st.columns([5, 1])
    c1.html(f'<div class="ptitle">🎯 Budget - {esc(ym_label(ym))}</div>')
    c2.button(f"↩ Undo ({n_undo})", key="bud_undo_btn", on_click=bud_undo, disabled=n_undo == 0)
    with st.container(key="nav_bud"):
        sec = st.radio("Budget section", list(BUD_SECTIONS), horizontal=True, key="bud_sec", label_visibility="collapsed")
    r = budget_month(led, bud, ym)
    BUD_SECTIONS[sec](led, bud, ym, r)


# =============================================================== page shell
PAGES = {
    "➕ Entry": page_entry,
    "📖 Journal": page_journal,
    "📒 Ledger": page_ledger,
    "🎯 Budget": page_budget,
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
        n_in = sum(1 for e in led.entries if in_period(e.date, per))
        latest = max((e.date for e in led.entries), default="-")
        st.caption(f"📋 {per['label']}: {n_in} entries · kul {len(led.entries)} · sabse nayi entry: {latest}")
        if page != "🎯 Budget":
            st.html(kpi_html(compute(led, per)))
    PAGES[page](led, per)
    components.html(KEYPAD_HTML, height=0)


main()