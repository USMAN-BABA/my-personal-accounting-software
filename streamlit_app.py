"""
Simple Accounting - Streamlit version (fast + colourful). Works on laptop AND phone.

Run locally :   streamlit run streamlit_app.py
Deploy online:  Streamlit Community Cloud + a free Neon Postgres database

Keep simple_accounting.py (your own file) and storage.py in the same folder.

Modes (picked automatically):
    * No DATABASE_URL secret  -> LOCAL mode: your JSON files on this computer, no login needed
    * DATABASE_URL secret set -> ONLINE mode: data in the database, login (APP_PASSWORD) required

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

TYPE_ORDER = ["Asset", "Liability", "Equity", "Revenue", "Expense"]
TYPE_RGB = {"Asset": "37,99,235", "Liability": "234,88,12", "Equity": "124,58,237",
            "Revenue": "5,150,105", "Expense": "225,29,72"}
TYPES = [t for t in TYPE_ORDER if t in ACCOUNT_TYPES] + [t for t in ACCOUNT_TYPES if t not in TYPE_ORDER]


def type_rgb(t):
    return TYPE_RGB.get(t, "100,116,139")


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


# =============================================================== look & feel
CSS = """
:root { --line: rgba(128,128,128,.28); }
[data-testid="stMainBlockContainer"], .block-container { max-width: 1600px; padding-top: 2.4rem; padding-bottom: 5rem; }
header[data-testid="stHeader"] { background: transparent; }

/* ---- hero banner ---- */
.hero { background: linear-gradient(135deg,#4f46e5 0%,#7c3aed 50%,#db2777 100%); color:#fff; border-radius: 18px;
        padding: .95rem 1.3rem; box-shadow: 0 12px 30px -12px rgba(79,70,229,.7); }
.hero .t { font-size: 1.6rem; font-weight: 800; letter-spacing: .01em; line-height: 1.2; }
.hero .s { font-size: .82rem; opacity: .85; }

/* ---- KPI cards ---- */
.kpis { display: grid; grid-template-columns: repeat(4,1fr); gap: .8rem; margin: .9rem 0 .6rem; }
.kpi { border-radius: 16px; padding: .75rem 1rem; color: #fff; box-shadow: 0 10px 22px -14px rgba(0,0,0,.6); }
.kpi .l { font-size: .72rem; opacity: .92; letter-spacing: .06em; text-transform: uppercase; font-weight: 600; }
.kpi .v { font-size: 1.5rem; font-weight: 800; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.k1 { background: linear-gradient(135deg,#2563eb,#38bdf8); }
.k2 { background: linear-gradient(135deg,#ea580c,#f59e0b); }
.k3 { background: linear-gradient(135deg,#7c3aed,#c084fc); }
.k4 { background: linear-gradient(135deg,#059669,#34d399); }
.k4.loss { background: linear-gradient(135deg,#e11d48,#fb7185); }

/* ---- page menu (pills) ---- */
[class*="st-key-nav"] [role="radiogroup"] { gap: .4rem; flex-wrap: wrap; }
[class*="st-key-nav"] label[data-baseweb="radio"] { background: rgba(128,128,128,.12); border: 1px solid var(--line);
        border-radius: 999px; padding: .38rem .95rem; margin: 0; cursor: pointer; transition: all .12s ease; }
[class*="st-key-nav"] label[data-baseweb="radio"] > div:first-child { display: none; }
[class*="st-key-nav"] label[data-baseweb="radio"]:hover { background: rgba(124,58,237,.18); transform: translateY(-1px); }
[class*="st-key-nav"] label[data-baseweb="radio"]:has(input:checked) { background: linear-gradient(135deg,#4f46e5,#7c3aed);
        border-color: transparent; box-shadow: 0 8px 18px -8px rgba(124,58,237,.9); }
[class*="st-key-nav"] label[data-baseweb="radio"]:has(input:checked) * { color: #fff !important; font-weight: 700; }
[class*="st-key-nav"] label[data-baseweb="radio"] p { font-size: .92rem; }

/* ---- buttons (premium) ---- */
.stButton > button, [data-testid="stFormSubmitButton"] > button, .stDownloadButton > button {
    border-radius: 12px; font-weight: 600; border: 1px solid var(--line);
    transition: transform .08s ease, box-shadow .15s ease, filter .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover { transform: translateY(-1px);
    box-shadow: 0 8px 18px -8px rgba(0,0,0,.45); }
.stButton > button:active, .stDownloadButton > button:active { transform: scale(.97); }
button[data-testid="stBaseButton-primary"], button[kind="primary"] {
    background: linear-gradient(135deg,#4f46e5,#7c3aed) !important; color: #fff !important; border: none !important;
    box-shadow: 0 10px 22px -10px rgba(124,58,237,.9); }
button[data-testid="stBaseButton-primary"]:hover, button[kind="primary"]:hover { filter: brightness(1.08); }
[class*="st-key-post_"], [class*="st-key-post_"] [data-testid="stButton"], [class*="st-key-post_"] button { width: 100% !important; }
[class*="st-key-post_"] button { min-height: 3.3rem; font-size: 1.08rem; font-weight: 800; letter-spacing: .02em;
    background: linear-gradient(135deg,#059669,#10b981) !important; box-shadow: 0 12px 24px -10px rgba(5,150,105,.9) !important; }
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] { border-radius: 10px !important; }

/* ---- account picker ---- */
.thdr { background: rgb(var(--c)); color: #fff; font-weight: 800; font-size: .74rem; letter-spacing: .08em;
        text-transform: uppercase; padding: .32rem .6rem; border-radius: 9px; text-align: center; }
[class*="st-key-col_"] { gap: .3rem !important; }
[class*="st-key-col_"] [data-testid="stVerticalBlock"] { gap: .3rem !important; }
[class*="st-key-pk_"], [class*="st-key-pk_"] [data-testid="stButton"] { width: 100% !important; }
[class*="st-key-pk_"] button { width: 100%; min-height: 2.15rem; padding: .15rem .55rem; justify-content: flex-start;
    text-align: left; border-radius: 10px; background: rgba(var(--c),.13); border: 1px solid rgba(var(--c),.5); box-shadow: none; }
[class*="st-key-pk_"] button p { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0;
    font-size: .8rem; font-weight: 600; text-align: left; }
[class*="st-key-pk_"] button:hover { background: rgb(var(--c)); border-color: rgb(var(--c)); transform: none;
    box-shadow: 0 6px 14px -6px rgba(var(--c),.9); }
[class*="st-key-pk_"] button:hover p { color: #fff; }
[class*="st-key-pk_sel_"] button { background: rgb(var(--c)); border-color: rgb(var(--c)); }
[class*="st-key-pk_sel_"] button p { color: #fff; }

/* ---- entry screen layout: picker left, slip right ---- */
.st-key-entry_split { display: grid !important; grid-template-columns: 3fr 2fr; gap: 1.1rem; align-items: stretch; }
.st-key-slippane { position: sticky; top: 3.4rem; align-self: start; }
.st-key-slippane { border: 1px solid var(--line); border-radius: 16px; padding: .8rem .85rem;
    background: rgba(128,128,128,.06); box-shadow: 0 14px 30px -20px rgba(0,0,0,.5); }
.slip-title { font-weight: 800; font-size: 1.05rem; margin-bottom: .2rem; }
.empty { border: 2px dashed var(--line); border-radius: 12px; padding: 1rem; text-align: center; opacity: .75; margin: .3rem 0 .6rem; }
[class*="st-key-lncard_"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: .4rem !important; align-items: center; }
[class*="st-key-lncard_"] [data-testid="stColumn"] { min-width: 0 !important; }
[class*="st-key-lncard_"] input { font-size: 1.05rem; font-weight: 700; text-align: right; font-variant-numeric: tabular-nums; }
[class*="st-key-lncard_"] button { min-height: 2.4rem; padding: .1rem .5rem; }
.lname { border-left: 5px solid rgb(var(--c)); padding-left: .55rem; font-weight: 700; overflow-wrap: anywhere; line-height: 1.25; }
.lname small { display: block; opacity: .7; font-weight: 600; font-size: .72rem; letter-spacing: .04em; }

.tot { display: grid; grid-template-columns: repeat(3,1fr); gap: .5rem; margin: .4rem 0 .3rem; }
.tot > div { border-radius: 12px; padding: .4rem .6rem; background: rgba(128,128,128,.12); text-align: center; }
.tot small { display: block; font-size: .66rem; letter-spacing: .08em; opacity: .75; font-weight: 700; }
.tot b { font-size: 1.05rem; font-variant-numeric: tabular-nums; }
.tot.ok > div { background: rgba(5,150,105,.18); }
.tot.bad > div { background: rgba(225,29,72,.16); }
.totmsg { text-align: center; font-weight: 700; font-size: .88rem; margin-bottom: .5rem; }
.totmsg.ok { color: #059669; } .totmsg.bad { color: #e11d48; } .totmsg.warn { color: #d97706; }

/* ---- tables ---- */
.tbl-wrap { overflow-x: auto; margin: .2rem 0 1rem; border: 1px solid var(--line); border-radius: 12px; }
.tbl { width: 100%; border-collapse: collapse; font-size: .92rem; }
.tbl th { text-align: left; font-weight: 700; padding: .55rem .65rem; white-space: nowrap; background: rgba(79,70,229,.14);
          border-bottom: 2px solid var(--line); }
.tbl td { padding: .45rem .65rem; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
.tbl tbody tr:nth-child(even) { background: rgba(128,128,128,.06); }
.tbl tbody tr:hover { background: rgba(124,58,237,.10); }
.tbl .r { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tbl th.r { text-align: right; }
.tbl tr.sub td  { font-weight: 700; background: rgba(79,70,229,.13); }
.tbl tr.head td { font-weight: 800; letter-spacing: .05em; background: rgba(128,128,128,.16); }
.neg { color: #e11d48; }

/* ---- journal cards ---- */
.jcard { border: 1px solid var(--line); border-left: 5px solid #4f46e5; border-radius: 12px; padding: .6rem .9rem; margin: .4rem 0;
         background: rgba(128,128,128,.05); transition: transform .1s ease, box-shadow .15s ease; }
.jcard:hover { transform: translateY(-1px); box-shadow: 0 10px 20px -14px rgba(0,0,0,.6); }
.jcard .jtop { display: flex; justify-content: space-between; gap: .6rem; font-size: .84rem; opacity: .85; }
.jcard .jamt { font-weight: 800; opacity: 1; font-variant-numeric: tabular-nums; white-space: nowrap; color: #4f46e5; }
.jcard .jdesc { font-weight: 700; margin: .12rem 0; overflow-wrap: anywhere; }
.jcard .jstat { font-size: .78rem; opacity: .75; }
.jcard.rev  { opacity: .5; border-left-color: #94a3b8; }
.jcard.revl { border-left-color: #d97706; }

/* ---- ledger header ---- */
.lhead { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; color: #fff;
         background: linear-gradient(135deg, rgb(var(--c)), rgba(var(--c),.65)); border-radius: 16px; padding: .85rem 1.1rem;
         margin: .9rem 0 .6rem; box-shadow: 0 12px 26px -14px rgba(var(--c),.9); }
.lhead .lh-code { font-size: .78rem; opacity: .9; letter-spacing: .05em; text-transform: uppercase; font-weight: 700; }
.lhead .lh-name { font-size: 1.35rem; font-weight: 800; }
.lhead .lh-bal { text-align: right; }
.lhead .lh-bal small { display: block; font-size: .68rem; letter-spacing: .08em; opacity: .9; font-weight: 700; }
.lhead .lh-bal b { font-size: 1.6rem; font-variant-numeric: tabular-nums; }

/* ---- login box ---- */
.st-key-login_box { max-width: 420px; margin: 2.5rem auto 0; border: 1px solid var(--line); border-radius: 18px;
                    padding: 1.4rem 1.5rem; box-shadow: 0 20px 40px -24px rgba(0,0,0,.6); }

[class*="st-key-"][class*="_rowbtns"] { max-width: 440px; }

/* ======================= medium screens ======================= */
@media (max-width: 1250px) {
  .st-key-picker_grid [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .6rem !important; }
  .st-key-picker_grid [data-testid="stColumn"] { flex: 1 1 calc(33% - .6rem) !important; min-width: calc(33% - .6rem) !important; }
}
@media (max-width: 1000px) {
  .st-key-entry_split { grid-template-columns: 1fr; }
  .st-key-entry_split > :is(.st-key-slippane, :has(.st-key-slippane)) { order: -1; }
  .st-key-slippane { position: static; }
}

/* ======================= PHONES ======================= */
@media (max-width: 700px) {
  [data-testid="stMainBlockContainer"], .block-container { padding: 3rem .6rem 4rem; }
  .hero .t { font-size: 1.25rem; }
  .kpis { grid-template-columns: 1fr 1fr; gap: .5rem; }
  .kpi .v { font-size: 1.1rem; }
  .stButton button, [data-testid="stFormSubmitButton"] button, .stDownloadButton button { min-height: 2.8rem; }
  input, [data-baseweb="select"] { font-size: 16px !important; }   /* stops iPhone zooming into fields */
  .st-key-picker_grid [data-testid="stColumn"] { flex: 1 1 calc(50% - .6rem) !important; min-width: calc(50% - .6rem) !important; }
  [class*="st-key-nav"] [role="radiogroup"] { flex-wrap: nowrap; overflow-x: auto; padding-bottom: .3rem; }
  [class*="st-key-nav"] label[data-baseweb="radio"] { white-space: nowrap; }

  /* edit screen lines: account on top, Debit | Credit below */
  [class*="st-key-"][class*="_card"] [data-testid="stHorizontalBlock"] {
        flex-direction: row !important; flex-wrap: wrap !important; gap: .5rem !important; }
  [class*="st-key-"][class*="_card"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important;
        flex: 1 1 calc(50% - .25rem) !important; }
  [class*="st-key-"][class*="_card"] [data-testid="stColumn"]:nth-child(1) { flex: 1 1 100% !important; }

  [class*="st-key-"][class*="_rowbtns"] [data-testid="stHorizontalBlock"] {
        flex-direction: row !important; flex-wrap: nowrap !important; gap: .5rem !important; }
  [class*="st-key-"][class*="_rowbtns"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
  .tbl { font-size: .85rem; }
  .tbl th, .tbl td { padding: .38rem .4rem; }
}
"""
CSS += "".join(f".st-key-col_{i}{{--c:{type_rgb(t)};}}\n" for i, t in enumerate(TYPES))

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


def html_table(headers, rows, right=()):
    """rows = [(cells, css_class)]. Everything is escaped; a newline becomes a line break."""
    head = "".join(f'<th class="{"r" if i in right else ""}">{esc(t)}</th>' for i, t in enumerate(headers))
    body = []
    for cells, cls in rows:
        tds = ""
        for i, c in enumerate(cells):
            c = str(c)
            k = ("r " if i in right else "") + ("neg" if i in right and c.startswith("-") else "")
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
        return parse_amount(text) or Decimal("0")
    except ValueError:
        return Decimal("0")


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


def entry_status(led, e):
    status = f"Reversed by #{e.reversed_by}" if e.reversed_by else \
        f"Reversal of #{e.reverses}" if e.reverses else "Active"
    n = sum(1 for a in led.audit_log if a.get("entry_id") == e.id)
    return status + (f", edited x{n}" if n else "")


def lines_table_html(led, lines):
    rows = [([acct_label(led, ln["account"]),
              money(Decimal(ln["debit"])) if Decimal(ln["debit"]) else "",
              money(Decimal(ln["credit"])) if Decimal(ln["credit"]) else ""], "") for ln in lines]
    return html_table(["Account", "Debit", "Credit"], rows, right=(1, 2))


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
    ss.pop("led", None)
    ss.pop("_tot", None)


def conflict_stop(err):
    drop_cache()  # the in-memory copy has the unsaved change - throw it away
    st.error(f"{err}\n\nYour last change was NOT saved. Press the Refresh button (or reload the page) and do it again.")
    st.stop()


def save_all(led):
    """Save to the database (or local file). Fast: no PDF is built here any more."""
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


def totals(led):
    ss = st.session_state
    m = ss.get("_tot")
    if not m or m[0] != ss.get("rev", 0):
        m = (ss.get("rev", 0), led.totals_by_type())
        ss["_tot"] = m
    return m[1]


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
        st.html('<div class="hero"><div class="t">📒 Simple Accounting</div><div class="s">Please log in</div></div>')
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


# =============================================================== the account picker (all accounts on screen, 1 click)
def account_picker(led, scope, on_pick, selected=()):
    """Every account as a coloured button, grouped by type. Clicking calls on_pick(code)."""
    q = st.text_input("Search account", key=f"{scope}_q", label_visibility="collapsed",
                      placeholder="🔎 Account dhoondein (naam ya code)...")
    ql = q.strip().lower()
    groups = {t: [] for t in TYPES}
    for c, a in sorted(led.accounts.items()):
        if ql and ql not in c.lower() and ql not in a.name.lower():
            continue
        groups.setdefault(a.type, []).append((c, a))
    selected = set(selected)
    with st.container(key="picker_grid"):
        cols = st.columns(len(TYPES))
        for i, (t, col) in enumerate(zip(TYPES, cols)):
            with col, st.container(key=f"col_{i}"):
                if not groups.get(t):
                    continue
                st.html(f'<div class="thdr" style="--c:{type_rgb(t)}">{esc(t)}</div>')
                for c, a in groups[t]:
                    sel = "sel_" if c in selected else ""
                    st.button(f"{c}  {a.name}", key=f"pk_{sel}{scope}_{c}", on_click=on_pick, args=(c,), help=a.name)


# =============================================================== entry slip (state helpers)
def slip_totals(exclude=None):
    ss = st.session_state
    dr = cr = Decimal("0")
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


def balance_line(lid):
    ss = st.session_state
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
    dr = cr = Decimal("0")
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
        lines.append((label_code(acct), dv or Decimal("0"), cv or Decimal("0")))
    return lines


def totals_html(rows):
    dr, cr, bad = rows_totals(rows)
    if bad:
        cls, msg = "bad", "⚠️ Kuch amounts sahi number nahi hain"
    elif dr == cr and dr > 0:
        cls, msg = "ok", "✅ Balanced - ab POST kar dein"
    elif dr == 0 and cr == 0:
        cls, msg = "", ""
    else:
        cls, msg = "bad", "Debit aur Credit abhi barabar nahi"
    mcls = {"ok": "ok", "bad": "warn"}.get(cls, "")
    return (f'<div class="tot {cls}"><div><small>DEBIT</small><b>{esc(money(dr))}</b></div>'
            f'<div><small>CREDIT</small><b>{esc(money(cr))}</b></div>'
            f'<div><small>FARQ</small><b>{esc(money(abs(dr - cr)))}</b></div></div>'
            f'<div class="totmsg {mcls}">{esc(msg)}</div>')


# =============================================================== New Entry page
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
            st.html('<div class="slip-title">👆 Account par click karein</div>')
            account_picker(led, "ent", add_line, selected=used)

        with st.container(key="slippane"):
            st.html('<div class="slip-title">🧾 Entry Slip</div>')
            c1, c2 = st.columns([2, 3])
            d = c1.date_input("Date", value=date.today(), key=f"date_{fid}", format="DD/MM/YYYY")
            desc = c2.text_input("Description", key=f"desc_{fid}", placeholder="e.g. RECV FROM KHALID")

            if not slip:
                st.html('<div class="empty">👈 Left se account par click karein - line yahan add ho jayegi.<br>'
                        'Doosra account chunenge to baqi amount khud bhar jayegi.</div>')
            rows = []
            for ln in list(slip):
                lid, code = ln["id"], ln["code"]
                a = led.accounts.get(code)
                rgb = type_rgb(a.type) if a else "100,116,139"
                with st.container(border=True, key=f"lncard_{lid}"):
                    t1, t2 = st.columns([8, 1])
                    t1.html(f'<div class="lname" style="--c:{rgb}">{esc(code)} - {esc(a.name if a else "?")}'
                            f'<small>{esc(a.type if a else "")}</small></div>')
                    t2.button("✕", key=f"rm_{lid}", on_click=remove_line, args=(lid,), help="Line hatayein")
                    b1, b2, b3 = st.columns([3, 3, 1])
                    dtxt = b1.text_input("Debit", key=f"ln_d_{lid}", label_visibility="collapsed", placeholder="Debit")
                    ctxt = b2.text_input("Credit", key=f"ln_c_{lid}", label_visibility="collapsed", placeholder="Credit")
                    b3.button("⚖", key=f"bal_{lid}", on_click=balance_line, args=(lid,),
                              help="Is line ko balance karne wali amount bhar do")
                rows.append((acct_label(led, code), dtxt, ctxt))

            st.html(totals_html(rows))
            p1, p2 = st.columns([3, 1])
            post = p1.button("✅  POST ENTRY", key="post_btn", type="primary")
            p2.button("Clear", key="clear_btn", on_click=clear_slip)

    if post:
        try:
            if not desc.strip():
                raise ValueError("Please type a description")
            lines = parse_rows(rows)
            dr, cr, _ = rows_totals([("", d_, c_) for _, d_, c_ in rows])
            if dr != cr:
                raise ValueError(f"Debit ({money(dr)}) aur Credit ({money(cr)}) barabar nahi hain")
            e = led.post_entry(desc.strip(), lines, d.isoformat())
        except (ValueError, InvalidOperation) as err:
            st.error(str(err))
        else:
            save_all(led)
            flash("success", f"Entry #{e.id} posted and saved.")
            clear_slip()
            st.rerun()


def page_new_entry(led):
    entry_fragment()
    st.divider()
    with st.expander("Initial investment (opening transaction)"):
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
                    flash("success", f"Initial investment posted as entry #{e.id}.")
                    st.rerun()
    with st.expander("Transfer money between accounts"):
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
                    e = led.transfer(label_code(src), label_code(dst), a, note.strip(),
                                     entry_date=tdate.isoformat())
                except (ValueError, InvalidOperation) as err:
                    st.error(str(err))
                else:
                    save_all(led)
                    flash("success", f"Transfer posted as entry #{e.id}.")
                    st.rerun()


# =============================================================== Journal page (view / edit / reverse)
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
        flash("success", f"Entry #{e.id} updated. The old values are kept in the edit history.")
        ss[f"edit_ver_{e.id}"] = ver + 1
        st.rerun()


def page_journal(led):
    if not led.entries:
        st.info("No entries yet.")
        return
    c1, c2 = st.columns([3, 1])
    q = c1.text_input("Search", key="journal_q", label_visibility="collapsed",
                      placeholder="🔎 Description, date ya entry number se dhoondein")
    limit = c2.selectbox("Show", ["Last 20", "Last 50", "All"], key="journal_limit", label_visibility="collapsed")
    n = {"Last 20": 20, "Last 50": 50}.get(limit, len(led.entries))
    ql = q.strip().lower().lstrip("#")
    items = []
    for e in reversed(led.entries):
        if ql and ql not in e.description.lower() and ql not in e.date and ql != str(e.id):
            continue
        items.append(e)
        if len(items) >= n:
            break
    if not items:
        st.info("Koi entry nahi mili.")
        return

    cards = []
    for e in items:
        total = sum((Decimal(x["debit"]) for x in e.lines), Decimal("0"))
        cls = "rev" if e.reversed_by else "revl" if e.reverses else ""
        cards.append(f'<div class="jcard {cls}"><div class="jtop"><span>#{e.id} &middot; {esc(e.date)}</span>'
                     f'<span class="jamt">{esc(money(total))}</span></div>'
                     f'<div class="jdesc">{esc(e.description)}</div>'
                     f'<div class="jstat">{esc(entry_status(led, e))}</div></div>')
    show_html("".join(cards))

    st.subheader("Entry dekhein / edit / delete karein")
    names = {x.id: f"#{x.id} | {x.date} | {x.description}" for x in items}
    chosen = st.selectbox("Pick an entry", list(names), format_func=lambda i: names[i], key="journal_pick")
    e = led.get_entry(chosen)
    st.caption(f"Entry #{e.id}  -  {entry_status(led, e)}")
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
        mode = st.radio("Action", ["✏️ Edit (accountant PIN)", "🗑 Delete (reverse)"], horizontal=True,
                        key="journal_mode", label_visibility="collapsed")
    if mode.startswith("✏️"):
        edit_panel(led, e)
    elif e.reversed_by or e.reverses:
        st.info("This entry is already reversed (or is itself a reversal).")
    else:
        st.caption("An opposite entry is posted, so the books stay correct and the history is kept.")
        reason = st.text_input("Reason (optional)", key=f"rev_reason_{e.id}")
        sure = st.checkbox(f"Yes, reverse entry #{e.id}", key=f"rev_sure_{e.id}")
        if st.button("Delete (reverse) entry", key=f"rev_btn_{e.id}", disabled=not sure):
            try:
                rev = led.reverse_entry(e.id, reason.strip())
            except ValueError as err:
                st.error(str(err))
            else:
                save_all(led)
                flash("success", f"Entry #{e.id} cancelled by reversal entry #{rev.id}.")
                st.rerun()


# =============================================================== Ledger page (one click -> ledger opens, stays on this page)
def page_ledger(led):
    ss = st.session_state

    def pick(code):
        ss["ledger_code"] = code

    sel = ss.get("ledger_code")
    st.html('<div class="slip-title">👆 Account par click karein - ledger foran khul jayegi</div>')
    account_picker(led, "led", pick, selected=(sel,) if sel else ())
    if sel not in led.accounts:
        st.info("Upar se koi bhi account chunein.")
        return

    code, a = sel, led.accounts[sel]
    normal_debit = ACCOUNT_TYPES[a.type] == "debit"

    p1, p2, p3 = st.columns([2, 2, 2])
    period = p1.selectbox("Period", ["Sab", "Is mahine", "Pichhle 30 din", "Is saal", "Custom"], key="led_period")
    today = date.today()
    start = end = None
    if period == "Is mahine":
        start = today.replace(day=1)
    elif period == "Pichhle 30 din":
        start = today - timedelta(days=30)
    elif period == "Is saal":
        start = today.replace(month=1, day=1)
    elif period == "Custom":
        start = p2.date_input("From", value=today.replace(day=1), key="led_from", format="DD/MM/YYYY")
        end = p3.date_input("To", value=today, key="led_to", format="DD/MM/YYYY")
    s_iso, e_iso = (start.isoformat() if start else None), (end.isoformat() if end else None)

    def signed(dr, cr):
        return (dr - cr) if normal_debit else (cr - dr)

    opening = Decimal("0")
    body = []
    for d, desc, dr, cr in led.ledger_for(code):
        ds = str(d)
        if s_iso and ds < s_iso:
            opening += signed(dr, cr)
        elif e_iso and ds > e_iso:
            continue
        else:
            body.append((ds, desc, dr, cr))

    run, tdr, tcr = opening, Decimal("0"), Decimal("0")
    out = []
    if s_iso:
        out.append((["", "Opening balance", "", "", money(opening)], "sub"))
    for ds, desc, dr, cr in body:
        run += signed(dr, cr)
        tdr += dr
        tcr += cr
        out.append(([ds, desc, money(dr) if dr else "", money(cr) if cr else "", money(run)], ""))
    out.append((["", "TOTAL / Closing", money(tdr), money(tcr), money(run)], "sub"))

    st.html(f'<div class="lhead" style="--c:{type_rgb(a.type)}"><div>'
            f'<div class="lh-code">{esc(code)} &middot; {esc(a.type)} &middot; {len(body)} transactions</div>'
            f'<div class="lh-name">{esc(a.name)}</div></div>'
            f'<div class="lh-bal"><small>CLOSING BALANCE</small><b>{esc(money(run))}</b></div></div>')
    if body or s_iso:
        show_html(html_table(["Date", "Description", "Debit", "Credit", "Balance"], out, right=(2, 3, 4)))
    else:
        st.info("Is account me abhi koi transaction nahi.")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Description", "Debit", "Credit", "Balance"])
    r2 = opening
    for ds, desc, dr, cr in body:
        r2 += signed(dr, cr)
        w.writerow([ds, desc, dec_str(dr) if dr else "", dec_str(cr) if cr else "", dec_str(r2)])
    st.download_button("⬇️ Ledger CSV download", buf.getvalue().encode("utf-8"),
                       file_name=f"ledger_{code}_{today.isoformat()}.csv", mime="text/csv", key="dl_ledger")


# =============================================================== Accounts / Reports / History / Backup
def page_accounts(led):
    q = st.text_input("Search accounts by code or name", key="acct_search", placeholder="e.g. bank or 1040",
                      label_visibility="collapsed")
    rows = [([c, a.name, a.type, money(led.balance(c))], "") for c, a in sorted(led.accounts.items())
            if not q or q.lower() in c.lower() or q.lower() in a.name.lower()]
    show_html(html_table(["Code", "Account", "Type", "Balance"], rows, right=(3,)))
    st.caption("Kisi account ki ledger dekhni ho to upar menu me 📒 Ledger par jayein.")

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


def page_reports(led):
    with st.container(key="nav_rep"):
        which = st.radio("Report", ["Trial Balance", "Income Statement", "Balance Sheet"], horizontal=True,
                         key="report_pick", label_visibility="collapsed")
    t = totals(led)
    net = t["Revenue"] - t["Expense"]

    def items(typ):
        return [(["    " + a.name, money(led.balance(c))], "") for c, a in sorted(led.accounts.items())
                if a.type == typ and led.balance(c) != 0]

    def head(label):
        return ([label, ""], "head")

    def total(label, amount):
        return ([label, money(amount)], "sub")

    if which == "Trial Balance":
        rows, tdr, tcr = led.trial_balance()
        out = [([c, n, money(d) if d else "", money(k) if k else ""], "") for c, n, d, k in rows]
        out.append((["", "TOTAL", money(tdr), money(tcr)], "sub"))
        show_html(html_table(["Code", "Account", "Debit", "Credit"], out, right=(2, 3)))
        (st.success if tdr == tcr else st.error)("Balanced" if tdr == tcr else "NOT BALANCED")
    elif which == "Income Statement":
        out = [head("REVENUE")] + items("Revenue") + [total("Total Revenue", t["Revenue"])]
        out += [head("EXPENSES")] + items("Expense") + [total("Total Expenses", t["Expense"])]
        out.append((["NET PROFIT" if net >= 0 else "NET LOSS", money(abs(net))], "sub"))
        show_html(html_table(["Item", "Amount"], out, right=(1,)))
    else:
        out = [head("ASSETS")] + items("Asset") + [total("Total Assets", t["Asset"])]
        out += [head("LIABILITIES")] + items("Liability") + [total("Total Liabilities", t["Liability"])]
        out += [head("EQUITY")] + items("Equity") + [
            (["    Net profit / (loss) for the period", money(net)], ""),
            total("Total Equity", t["Equity"] + net)]
        show_html(html_table(["Item", "Amount"], out, right=(1,)))
        ok = t["Asset"] == t["Liability"] + t["Equity"] + net
        (st.success if ok else st.error)("Assets = Liabilities + Equity" if ok else "The equation does NOT balance")


def page_history(led):
    if not led.audit_log:
        st.info("No entry edits have been recorded.")
        return
    rows = []
    for a in reversed(led.audit_log):
        b, af = a.get("before", {}), a.get("after", {})
        what = f"Date: {b.get('date')} -> {af.get('date')}\nDescription: {b.get('description')} -> {af.get('description')}"
        if b.get("lines") != af.get("lines"):
            what += "\nAccounts / amounts changed"
        rows.append(([a.get("timestamp", ""), f"#{a.get('entry_id')}", what], ""))
    show_html(html_table(["When", "Entry", "What changed"], rows))
    st.caption("Open an entry in the Journal page to see its full before/after lines.")


def page_backup(led):
    ss = st.session_state
    st.subheader("Backup")
    st.caption("Your data is stored " + ("in the online database." if not STORAGE.local else "in files on this computer.")
               + " Download a backup copy now and then.")
    st.download_button("Download data backup (JSON)", ledger_to_text(led).encode("utf-8"),
                       file_name=f"accounting_data_{date.today().isoformat()}.json",
                       mime="application/json", key="dl_json")

    st.subheader("PDF report")
    st.caption("PDF ab sirf yahan se banti hai (har entry par nahi) - isi se entry post tez ho gayi.")
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


# =============================================================== page
PAGES = {
    "➕ Nayi Entry": page_new_entry,
    "📖 Journal": page_journal,
    "📒 Ledger": page_ledger,
    "🏷️ Accounts": page_accounts,
    "📊 Reports": page_reports,
    "🕘 History": page_history,
    "💾 Backup": page_backup,
}


def kpi_html(led):
    t = totals(led)
    net = t["Revenue"] - t["Expense"]
    loss = " loss" if net < 0 else ""
    cards = [("k1", "Total Assets", t["Asset"]), ("k2", "Liabilities", t["Liability"]),
             ("k3", "Equity (incl. profit)", t["Equity"] + net),
             (f"k4{loss}", "Net Loss" if net < 0 else "Net Profit", net)]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi {c}"><div class="l">{esc(l)}</div><div class="v">{esc(money(v))}</div></div>'
        for c, l, v in cards) + "</div>"


def main():
    st.html(f"<style>{CSS}</style>")
    check_login()
    led = get_ledger()
    ss = st.session_state

    top = st.columns([8, 1, 1] if secret("APP_PASSWORD") else [9, 1])
    top[0].html(f'<div class="hero"><div class="t">📒 Simple Accounting</div>'
                f'<div class="s">{esc(date.today().strftime("%A, %d %B %Y"))}</div></div>')
    top[1].button("🔄", key="refresh", on_click=drop_cache, help="Data dobara load karein (doosre device ki entries dekhne ke liye)")
    if secret("APP_PASSWORD") and top[2].button("Log out", key="logout"):
        ss["auth_ok"] = False
        drop_cache()
        st.rerun()

    st.html(kpi_html(led))

    f = ss.pop("flash", None)
    if f:
        if f[0] == "success":
            st.toast(f[1], icon="✅")
        else:
            getattr(st, f[0])(f[1])

    with st.container(key="nav"):
        page = st.radio("Page", list(PAGES), horizontal=True, key="page", label_visibility="collapsed")
    PAGES[page](led)  # only the page you are looking at is built - the others cost nothing
    components.html(KEYPAD_HTML, height=0)


main()