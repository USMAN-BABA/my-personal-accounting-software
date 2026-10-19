"""
Simple Accounting - Streamlit version (works on laptop AND phone, locally or online).

Run locally :   streamlit run streamlit_app.py
Deploy online:  Streamlit Community Cloud + a free Neon Postgres database

Keep simple_accounting.py (your own file) and storage.py in the same folder.

Modes (picked automatically):
    * No DATABASE_URL secret  -> LOCAL mode: your JSON files on this computer, no login needed
    * DATABASE_URL secret set -> ONLINE mode: data in the database, login (APP_PASSWORD) required
"""
import hashlib
import html as _html
import io
import json
import os
import secrets
import tempfile
import time
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import streamlit as st
import streamlit.components.v1 as components

from simple_accounting import (ACCOUNT_TYPES, DATA_FILE, PDF_FILE, SECURITY_FILE, Ledger,
                               export_pdf, load_default_accounts, money)
from storage import ConflictError, FileStorage, PostgresStorage

st.set_page_config(page_title="Simple Accounting", page_icon="📒", layout="wide",
                   initial_sidebar_state="collapsed")

UNLOCK_SECONDS = 300        # editing stays unlocked this long, then locks itself again
MAX_PIN_FAILS = 5           # wrong PINs allowed before a short pause
BLOCK_SECONDS = 60
IDLE_LOGOUT_SECONDS = 1800  # logged out after 30 minutes without activity


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


def setup_timezone():
    """Servers usually run on UTC. Use your local time so 'today' and timestamps are right."""
    tz = secret("APP_TIMEZONE") or ("PKT-5" if secret("DATABASE_URL") else None)
    if tz and hasattr(time, "tzset"):
        os.environ["TZ"] = tz
        time.tzset()


setup_timezone()


@st.cache_resource
def get_storage():
    url = secret("DATABASE_URL")
    if url:
        return PostgresStorage(url)
    return FileStorage({"ledger": DATA_FILE, "pin": SECURITY_FILE})


STORAGE = get_storage()


# =============================================================== look & feel (works on phones too)
CSS = """
:root { --acc-line: rgba(128,128,128,.30); }
[data-testid="stMainBlockContainer"], .block-container { max-width: 1100px; padding-top: 3.6rem; }

/* ---- tables ---- */
.tbl-wrap { overflow-x: auto; margin: .2rem 0 1rem; }
.tbl { width: 100%; border-collapse: collapse; font-size: .92rem; }
.tbl th { text-align: left; font-weight: 600; padding: .45rem .55rem; white-space: nowrap;
          border-bottom: 2px solid var(--acc-line); }
.tbl td { padding: .42rem .55rem; border-bottom: 1px solid var(--acc-line); vertical-align: top;
          overflow-wrap: anywhere; }
.tbl .r { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tbl th.r { text-align: right; }
.tbl tr.sub td  { font-weight: 600; background: rgba(128,128,128,.13); }
.tbl tr.head td { font-weight: 700; letter-spacing: .03em; border-bottom: 2px solid var(--acc-line); }
.neg { color: #c62828; }

/* ---- journal cards ---- */
.jcard { border: 1px solid var(--acc-line); border-radius: 10px; padding: .6rem .85rem; margin: .4rem 0; }
.jcard .jtop { display: flex; justify-content: space-between; gap: .6rem; font-size: .84rem; opacity: .8; }
.jcard .jamt { font-weight: 700; opacity: 1; font-variant-numeric: tabular-nums; white-space: nowrap; }
.jcard .jdesc { font-weight: 600; margin: .12rem 0; overflow-wrap: anywhere; }
.jcard .jstat { font-size: .78rem; opacity: .75; }
.jcard.rev   { opacity: .5; }
.jcard.revl  { border-left: 4px solid #9a6700; }

/* ---- login box ---- */
.st-key-login_box { max-width: 420px; margin: 2.5rem auto 0; }

/* ---- buttons: easy to tap ---- */
[class*="st-key-post_"], [class*="st-key-post_"] [data-testid="stButton"], [class*="st-key-post_"] button
    { width: 100% !important; }
[class*="st-key-post_"] button { min-height: 3rem; font-weight: 700; }

[class*="st-key-"][class*="_rowbtns"] { max-width: 440px; }

/* ======================= PHONES ======================= */
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"], .block-container { padding: 3.4rem .6rem 4rem; }
  h1 { font-size: 1.55rem !important; }
  button[role="tab"] { padding: .45rem .65rem; font-size: .9rem; }
  .stButton button, [data-testid="stFormSubmitButton"] button, .stDownloadButton button { min-height: 2.8rem; }
  input, [data-baseweb="select"] { font-size: 16px !important; }   /* stops iPhone zooming into fields */

  /* the four summary numbers: 2 x 2 */
  .st-key-metrics [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .4rem !important; }
  .st-key-metrics [data-testid="stColumn"] { flex: 1 1 calc(50% - .4rem) !important;
        min-width: calc(50% - .4rem) !important; width: calc(50% - .4rem) !important; }
  .st-key-metrics [data-testid="stMetricValue"] { font-size: 1.3rem; }

  /* each entry line: account on top, Debit | Credit side by side below */
  [class*="st-key-"][class*="_card"] [data-testid="stHorizontalBlock"] {
        flex-direction: row !important; flex-wrap: wrap !important; gap: .5rem !important; }
  [class*="st-key-"][class*="_card"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important;
        flex: 1 1 calc(50% - .25rem) !important; }
  [class*="st-key-"][class*="_card"] [data-testid="stColumn"]:nth-child(1) { flex: 1 1 100% !important; }

  /* "Add line / Remove last line" stay on one row */
  [class*="st-key-"][class*="_rowbtns"] [data-testid="stHorizontalBlock"] {
        flex-direction: row !important; flex-wrap: nowrap !important; gap: .5rem !important; }
  [class*="st-key-"][class*="_rowbtns"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important;
        flex: 1 1 0 !important; }
  [class*="st-key-"][class*="_rowbtns"] button { width: 100%; }

  .tbl { font-size: .85rem; }
  .tbl th, .tbl td { padding: .38rem .4rem; }
}
"""

# Number keypad on phones for the amount boxes
KEYPAD_JS = """
<script>
(function () {
  var doc = window.parent.document;
  function fix() {
    doc.querySelectorAll('input[aria-label="Debit"], input[aria-label="Credit"], input[aria-label^="Amount"]')
       .forEach(function (i) { i.setAttribute('inputmode', 'decimal'); });
  }
  fix();
  if (!window.parent.__acctKeypad) {
    window.parent.__acctKeypad = true;
    new MutationObserver(fix).observe(doc.body, {childList: true, subtree: true});
  }
})();
</script>
"""


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
    """Returns (pdf_bytes, error_text)."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "report.pdf")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = export_pdf(led, p)
        if not ok:
            return None, buf.getvalue().strip() or "The PDF could not be created"
        with open(p, "rb") as f:
            return f.read(), ""


def conflict_stop(err):
    st.error(f"{err}\n\nYour last change was NOT saved. Reload the page and do it again.")
    st.stop()


def save_all(led):
    """Save to the database (or local file). Locally the PDF file is also rebuilt, like before."""
    ss = st.session_state
    try:
        ss["ledger_version"] = STORAGE.write("ledger", ledger_to_text(led), ss.get("ledger_version", 0))
    except ConflictError as err:
        conflict_stop(err)
    ss.pop("pdf_bytes", None)  # any PDF made earlier is out of date now
    ss["pdf_note"] = ""
    if STORAGE.local:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = export_pdf(led, PDF_FILE)
        if not ok:
            ss["pdf_note"] = buf.getvalue().strip() or "PDF was not updated"


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
    if text is None or set(led.accounts) != before:
        save_all(led)
    elif STORAGE.local and not os.path.exists(PDF_FILE):
        save_all(led)
    return led


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

    with st.container(key="login_box"):
        st.title("Simple Accounting")
        st.caption("Please log in")
        with st.form("login_form"):
            user = st.text_input("Username", key="login_user")
            pw = st.text_input("Password", type="password", key="login_pw")
            go = st.form_submit_button("Log in", key="login_submit")
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
    `scope` must be a different string at each call site, so the PIN screen can be
    shown in more than one place (e.g. Journal AND Backup tab) without clashing."""
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


# =============================================================== entry line editor
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


def show_totals(rows):
    dr, cr, bad = rows_totals(rows)
    if bad:
        st.warning("Some amounts are not valid numbers.")
    elif dr == cr and dr > 0:
        st.success(f"Debit {money(dr)}  =  Credit {money(cr)}  -  balanced, ready to post")
    else:
        st.info(f"Debit {money(dr)}  |  Credit {money(cr)}  |  Difference {money(abs(dr - cr))}")
    return dr == cr and dr > 0 and not bad


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


def row_buttons(count_key, minimum, prefix):
    with st.container(key=f"{prefix}_rowbtns"):
        b1, b2 = st.columns(2)
        if b1.button("Add line", key=f"{prefix}_add"):
            st.session_state[count_key] += 1
            st.rerun()
        if b2.button("Remove last line", key=f"{prefix}_rem", disabled=st.session_state[count_key] <= minimum):
            st.session_state[count_key] -= 1
            st.rerun()


# =============================================================== tabs
def tab_new_entry(led):
    ss = st.session_state
    fid = ss.setdefault("form_id", 0)
    ss.setdefault("n_rows", 2)
    c1, c2 = st.columns([1, 3])
    d = c1.date_input("Date", value=date.today(), key=f"date_{fid}")
    desc = c2.text_input("Description", key=f"desc_{fid}", placeholder="e.g. RECV FROM KHALID")
    rows = line_editor(led, f"new{fid}", ss["n_rows"])
    row_buttons("n_rows", 2, f"new{fid}")
    ready = show_totals(rows)
    if st.button("POST ENTRY", type="primary", key=f"post_{fid}", disabled=not ready):
        try:
            if not desc.strip():
                raise ValueError("Please type a description")
            lines = parse_rows(rows)
            e = led.post_entry(desc.strip(), lines, d.isoformat())
        except (ValueError, InvalidOperation) as err:
            st.error(str(err))
        else:
            save_all(led)
            flash("success", f"Entry #{e.id} posted and saved.")
            ss["form_id"] += 1
            ss["n_rows"] = 2
            st.rerun()

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


def tab_journal(led):
    if not led.entries:
        st.info("No entries yet.")
        return
    limit = st.selectbox("Show", ["Last 20", "Last 50", "All"], key="journal_limit")
    n = {"Last 20": 20, "Last 50": 50}.get(limit, len(led.entries))
    cards = []
    for e in list(reversed(led.entries))[:n]:
        total = sum((Decimal(x["debit"]) for x in e.lines), Decimal("0"))
        cls = "rev" if e.reversed_by else "revl" if e.reverses else ""
        cards.append(f'<div class="jcard {cls}"><div class="jtop"><span>#{e.id} &middot; {esc(e.date)}</span>'
                     f'<span class="jamt">{esc(money(total))}</span></div>'
                     f'<div class="jdesc">{esc(e.description)}</div>'
                     f'<div class="jstat">{esc(entry_status(led, e))}</div></div>')
    show_html("".join(cards))

    st.subheader("Look at, edit or delete an entry")
    names = {x.id: f"#{x.id} | {x.date} | {x.description}" for x in led.entries}
    chosen = st.selectbox("Pick an entry", [x.id for x in reversed(led.entries)],
                          format_func=lambda i: names[i], key="journal_pick")  # stays on the same entry after edits
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

    t_edit, t_rev = st.tabs(["Edit (accountant PIN)", "Delete (reverse)"])
    with t_edit:
        edit_panel(led, e)
    with t_rev:
        if e.reversed_by or e.reverses:
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


def tab_accounts(led):
    q = st.text_input("Search accounts by code or name", key="acct_search", placeholder="e.g. bank or 1040")
    rows = [([c, a.name, a.type, money(led.balance(c))], "") for c, a in sorted(led.accounts.items())
            if not q or q.lower() in c.lower() or q.lower() in a.name.lower()]
    show_html(html_table(["Code", "Account", "Type", "Balance"], rows, right=(3,)))

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

    st.subheader("Account ledger")
    pick = st.selectbox("Choose an account", acct_options(led), index=None,
                        placeholder="Choose an account", key="ledger_pick")
    if pick:
        code = label_code(pick)
        normal_debit = ACCOUNT_TYPES[led.accounts[code].type] == "debit"
        run, out = Decimal("0"), []
        for d, desc, dr, cr in led.ledger_for(code):
            run += (dr - cr) if normal_debit else (cr - dr)
            out.append(([d, desc, money(dr) if dr else "", money(cr) if cr else "", money(run)], ""))
        if out:
            show_html(html_table(["Date", "Description", "Debit", "Credit", "Balance"], out, right=(2, 3, 4)))
        else:
            st.info("No transactions in this account yet.")
        st.write(f"**Balance: {money(run)}**")


def tab_reports(led):
    which = st.radio("Report", ["Trial Balance", "Income Statement", "Balance Sheet"], horizontal=True,
                     key="report_pick")
    t = led.totals_by_type()
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


def tab_history(led):
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
    st.caption("Open an entry in the Journal tab to see its full before/after lines.")


def tab_backup(led):
    ss = st.session_state
    st.subheader("Backup")
    st.caption("Your data is stored " + ("in the online database." if not STORAGE.local else "in files on this computer.")
               + " Download a backup copy now and then.")
    st.download_button("Download data backup (JSON)", ledger_to_text(led).encode("utf-8"),
                       file_name=f"accounting_data_{date.today().isoformat()}.json",
                       mime="application/json", key="dl_json")

    st.subheader("PDF report")
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
                ss.pop("pdf_bytes", None)
                flash("success", f"Imported {len(new.entries)} entries and {len(new.accounts)} accounts.")
                st.rerun()


# =============================================================== page
def main():
    st.html(f"<style>{CSS}</style>")
    check_login()
    led = load_ledger()
    ss = st.session_state

    top = st.columns([4, 1])
    top[0].title("Simple Accounting")
    if secret("APP_PASSWORD") and top[1].button("Log out", key="logout"):
        ss["auth_ok"] = False
        st.rerun()

    t = led.totals_by_type()
    net = t["Revenue"] - t["Expense"]
    with st.container(key="metrics"):
        m = st.columns(4)
        m[0].metric("Total Assets", money(t["Asset"]))
        m[1].metric("Liabilities", money(t["Liability"]))
        m[2].metric("Equity (incl. profit)", money(t["Equity"] + net))
        m[3].metric("Net Profit / Loss", money(net))

    f = ss.pop("flash", None)
    if f:
        getattr(st, f[0])(f[1])
    if ss.get("pdf_note"):
        st.warning(f"Saved, but the PDF file was not updated: {ss['pdf_note']}")

    tabs = st.tabs(["New Entry", "Journal", "Accounts", "Reports", "History", "Backup"])
    with tabs[0]:
        tab_new_entry(led)
    with tabs[1]:
        tab_journal(led)
    with tabs[2]:
        tab_accounts(led)
    with tabs[3]:
        tab_reports(led)
    with tabs[4]:
        tab_history(led)
    with tabs[5]:
        tab_backup(led)
    components.html(KEYPAD_JS, height=0)


main()