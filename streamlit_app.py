"""
Simple Accounting - Streamlit (web page) version.

Keep this file in the SAME folder as your simple_accounting.py (the original one, unchanged).
Run:   streamlit run streamlit_app.py
Or double-click run_app.bat (Windows).

It uses the same files as your terminal program:
    accounting_data.json      - your accounts and entries
    accounting_security.json  - the accountant PIN (same PIN works in both programs)
    accounting_report.pdf     - full report, rebuilt after every change
"""
import hashlib
import io
import json
import os
import secrets
import time
from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import streamlit as st

from simple_accounting import (ACCOUNT_TYPES, DATA_FILE, PDF_FILE, SECURITY_FILE, Ledger,
                               export_pdf, load_default_accounts, money)

UNLOCK_SECONDS = 300   # editing stays unlocked this long, then locks itself again
MAX_PIN_FAILS = 5      # wrong PINs allowed before a short pause
BLOCK_SECONDS = 60

st.set_page_config(page_title="Simple Accounting", layout="wide")


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
    return [""] + [acct_label(led, c) for c in sorted(led.accounts)
                   if only_type is None or led.accounts[c].type == only_type]


def label_code(label):
    return label.split(" - ")[0].strip()


def default_index(options, led, code):
    label = acct_label(led, code)
    return options.index(label) if label in options else 0


def show_table(data, height=None):
    kw = {} if height is None else {"height": height}
    try:
        st.dataframe(data, hide_index=True, width="stretch", **kw)
    except Exception:
        st.dataframe(data, hide_index=True, use_container_width=True, **kw)


def flash(kind, message):
    st.session_state["flash"] = (kind, message)


def save_all(led):
    """Save the data file and rebuild the PDF immediately."""
    led.save(DATA_FILE)
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok = export_pdf(led, PDF_FILE)
    st.session_state["pdf_note"] = "" if ok else (buf.getvalue().strip() or "PDF was not updated")
    return ok


def entry_status(led, e):
    status = f"Reversed by #{e.reversed_by}" if e.reversed_by else \
        f"Reversal of #{e.reverses}" if e.reverses else "Active"
    n = sum(1 for a in led.audit_log if a.get("entry_id") == e.id)
    return status + (f", edited x{n}" if n else "")


def entry_lines_table(led, lines):
    return [{"Account": acct_label(led, ln["account"]),
             "Debit": money(Decimal(ln["debit"])) if Decimal(ln["debit"]) else "",
             "Credit": money(Decimal(ln["credit"])) if Decimal(ln["credit"]) else ""} for ln in lines]


# =============================================================== accountant PIN (same format as the terminal program)
def pin_hash(pin, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), 200_000).hex()


def pin_exists():
    return os.path.exists(SECURITY_FILE)


def create_pin(pin):
    salt = secrets.token_hex(16)
    with open(SECURITY_FILE, "w", encoding="utf-8") as f:
        json.dump({"pin_salt": salt, "pin_hash": pin_hash(pin, salt)}, f, indent=2)


def verify_pin(pin):
    with open(SECURITY_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    salt, expected = cfg.get("pin_salt", ""), cfg.get("pin_hash", "")
    return bool(salt and expected) and secrets.compare_digest(pin_hash(pin, salt), expected)


def is_unlocked():
    return time.time() < st.session_state.get("unlocked_until", 0)


def pin_gate():
    """Shows the PIN screen. Returns True only while editing is unlocked."""
    ss = st.session_state
    if is_unlocked():
        left = int(ss["unlocked_until"] - time.time())
        c1, c2 = st.columns([5, 1])
        c1.success(f"Editing is unlocked (locks again in about {max(left // 60, 0) + 1} min).")
        if c2.button("Lock now", key="lock_now"):
            ss["unlocked_until"] = 0
            st.rerun()
        return True

    if not pin_exists():
        st.info("First-time setup: create the accountant-only PIN (at least 6 characters). "
                "Only the accountant should know it.")
        p1 = st.text_input("Create PIN", type="password", key="pin_new1")
        p2 = st.text_input("Confirm PIN", type="password", key="pin_new2")
        if st.button("Create PIN", key="pin_create"):
            if len(p1) < 6:
                st.error("The PIN must be at least 6 characters.")
            elif p1 != p2:
                st.error("The two PINs do not match.")
            else:
                create_pin(p1)
                ss["unlocked_until"] = time.time() + UNLOCK_SECONDS
                flash("success", "Accountant PIN created. Editing is unlocked.")
                st.rerun()
        return False

    st.warning("Editing posted entries needs the accountant PIN.")
    if time.time() < ss.get("pin_block_until", 0):
        st.error("Too many wrong PINs. Please wait a minute and try again.")
        return False
    pin = st.text_input("Accountant PIN", type="password", key="pin_try")
    if st.button("Unlock editing", key="pin_unlock"):
        if verify_pin(pin):
            ss["unlocked_until"] = time.time() + UNLOCK_SECONDS
            ss["pin_fails"] = 0
            st.rerun()
        else:
            ss["pin_fails"] = ss.get("pin_fails", 0) + 1
            if ss["pin_fails"] >= MAX_PIN_FAILS:
                ss["pin_block_until"] = time.time() + BLOCK_SECONDS
                ss["pin_fails"] = 0
            st.error("Wrong PIN. Only an authorized accountant can edit entries.")
    return False


# =============================================================== entry line editor
def line_editor(led, prefix, n_rows, defaults=None):
    """Account / Debit / Credit rows. defaults = [(code, debit_text, credit_text)]."""
    options = acct_options(led)
    h = st.columns([5, 2, 2])
    h[0].markdown("**Account**  (click and type to search)")
    h[1].markdown("**Debit**")
    h[2].markdown("**Credit**")
    rows = []
    for i in range(n_rows):
        c1, c2, c3 = st.columns([5, 2, 2])
        code, dr0, cr0 = defaults[i] if defaults and i < len(defaults) else ("", "", "")
        acct = c1.selectbox("Account", options, index=default_index(options, led, code),
                            key=f"{prefix}_a{i}", label_visibility="collapsed")
        dr = c2.text_input("Debit", value=dr0, key=f"{prefix}_d{i}", label_visibility="collapsed",
                           placeholder="Debit")
        cr = c3.text_input("Credit", value=cr0, key=f"{prefix}_c{i}", label_visibility="collapsed",
                           placeholder="Credit")
        rows.append((acct, dr, cr))
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
        st.info(f"Debit {money(dr)}   |   Credit {money(cr)}   |   Difference {money(abs(dr - cr))}")
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
    b1, b2, _ = st.columns([1, 1.3, 6])
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
            flash("success", f"Entry #{e.id} posted. Saved and PDF updated.")
            ss["form_id"] += 1
            ss["n_rows"] = 2
            st.rerun()

    st.divider()
    q1, q2 = st.columns(2)
    with q1.expander("Initial investment (opening transaction)"):
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
    with q2.expander("Transfer money between accounts"):
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
    if not pin_gate():
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
    show_table([{"#": e.id, "Date": e.date, "Description": e.description,
                 "Amount": money(sum((Decimal(x["debit"]) for x in e.lines), Decimal("0"))),
                 "Status": entry_status(led, e)} for e in reversed(led.entries)], height=330)

    names = {x.id: f"#{x.id} | {x.date} | {x.description}" for x in led.entries}
    chosen = st.selectbox("Pick an entry to look at, edit or delete", [x.id for x in reversed(led.entries)],
                          format_func=lambda i: names[i], key="journal_pick")  # stays on the same entry after edits
    e = led.get_entry(chosen)
    st.caption(f"Entry #{e.id}  -  {entry_status(led, e)}")
    show_table(entry_lines_table(led, e.lines))
    hist = [a for a in led.audit_log if a.get("entry_id") == e.id]
    if hist:
        with st.expander(f"Edit history of this entry ({len(hist)})"):
            for a in reversed(hist):
                b, af = a.get("before", {}), a.get("after", {})
                st.markdown(f"**{a.get('timestamp', '?')}**  -  date: {b.get('date')} -> {af.get('date')}  |  "
                            f"description: {b.get('description')} -> {af.get('description')}")
                if b.get("lines") != af.get("lines"):
                    c1, c2 = st.columns(2)
                    c1.caption("Lines before")
                    c1.dataframe(entry_lines_table(led, b.get("lines", [])), hide_index=True)
                    c2.caption("Lines after")
                    c2.dataframe(entry_lines_table(led, af.get("lines", [])), hide_index=True)

    t_edit, t_rev = st.tabs(["Edit this entry (accountant PIN)", "Delete (reverse) this entry"])
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
    rows = [{"Code": c, "Account": a.name, "Type": a.type, "Balance": money(led.balance(c))}
            for c, a in sorted(led.accounts.items())
            if not q or q.lower() in c.lower() or q.lower() in a.name.lower()]
    show_table(rows, height=380)

    with st.expander("Add a new account"):
        with st.form("add_account_form", clear_on_submit=True):
            c1, c2, c3 = st.columns([1, 3, 1.5])
            code = c1.text_input("Code")
            name = c2.text_input("Name")
            typ = c3.selectbox("Type", list(ACCOUNT_TYPES))
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
    options = acct_options(led)
    pick = st.selectbox("Choose an account", options, key="ledger_pick")
    if pick:
        code = label_code(pick)
        normal_debit = ACCOUNT_TYPES[led.accounts[code].type] == "debit"
        run, out = Decimal("0"), []
        for d, desc, dr, cr in led.ledger_for(code):
            run += (dr - cr) if normal_debit else (cr - dr)
            out.append({"Date": d, "Description": desc, "Debit": money(dr) if dr else "",
                        "Credit": money(cr) if cr else "", "Balance": money(run)})
        show_table(out) if out else st.info("No transactions in this account yet.")
        st.write(f"**Balance: {money(run)}**")


def tab_reports(led):
    which = st.radio("Report", ["Trial Balance", "Income Statement", "Balance Sheet"], horizontal=True,
                     key="report_pick")
    t = led.totals_by_type()
    net = t["Revenue"] - t["Expense"]

    def items(typ):
        return [{"Item": "     " + a.name, "Amount": money(led.balance(c))}
                for c, a in sorted(led.accounts.items()) if a.type == typ and led.balance(c) != 0]

    if which == "Trial Balance":
        rows, tdr, tcr = led.trial_balance()
        show_table([{"Code": c, "Account": n, "Debit": money(d) if d else "", "Credit": money(k) if k else ""}
                    for c, n, d, k in rows], height=420)
        st.write(f"**Total Debit {money(tdr)}   |   Total Credit {money(tcr)}**")
        (st.success if tdr == tcr else st.error)("Balanced" if tdr == tcr else "NOT BALANCED")
    elif which == "Income Statement":
        out = [{"Item": "REVENUE", "Amount": ""}] + items("Revenue") + [{"Item": "Total Revenue", "Amount": money(t["Revenue"])}]
        out += [{"Item": "EXPENSES", "Amount": ""}] + items("Expense") + [{"Item": "Total Expenses", "Amount": money(t["Expense"])}]
        out.append({"Item": "NET PROFIT" if net >= 0 else "NET LOSS", "Amount": money(abs(net))})
        show_table(out)
    else:
        out = [{"Item": "ASSETS", "Amount": ""}] + items("Asset") + [{"Item": "Total Assets", "Amount": money(t["Asset"])}]
        out += [{"Item": "LIABILITIES", "Amount": ""}] + items("Liability") + [{"Item": "Total Liabilities", "Amount": money(t["Liability"])}]
        out += [{"Item": "EQUITY", "Amount": ""}] + items("Equity") + [
            {"Item": "     Net profit / (loss) for the period", "Amount": money(net)},
            {"Item": "Total Equity", "Amount": money(t["Equity"] + net)}]
        show_table(out)
        ok = t["Asset"] == t["Liability"] + t["Equity"] + net
        (st.success if ok else st.error)("Assets = Liabilities + Equity" if ok else "The equation does NOT balance")


def tab_history(led):
    if not led.audit_log:
        st.info("No entry edits have been recorded.")
        return
    show_table([{"When": a.get("timestamp"), "Entry #": a.get("entry_id"),
                 "Date": f"{a['before'].get('date')} -> {a['after'].get('date')}",
                 "Description": f"{a['before'].get('description')} -> {a['after'].get('description')}",
                 "Amounts/accounts changed": "Yes" if a["before"].get("lines") != a["after"].get("lines") else "No"}
                for a in reversed(led.audit_log)])
    st.caption("Open an entry in the Journal tab to see its full before/after lines.")


# =============================================================== page
def get_ledger():
    try:
        led = Ledger.load(DATA_FILE)
    except Exception as err:
        st.error(f"Your data file could not be read:\n\n{DATA_FILE}\n\n{err}\n\n"
                 "Nothing was changed. Fix or restore the file, then reload this page.")
        st.stop()
    before = set(led.accounts)
    load_default_accounts(led)
    if set(led.accounts) != before or not os.path.exists(PDF_FILE):
        save_all(led)
    return led


def main():
    led = get_ledger()
    st.title("Simple Accounting")

    with st.sidebar:
        st.header("Files")
        st.caption(f"Data: {DATA_FILE}")
        if os.path.exists(PDF_FILE):
            with open(PDF_FILE, "rb") as f:
                st.download_button("Download PDF report", f.read(), file_name="accounting_report.pdf",
                                   mime="application/pdf", key="dl_pdf")
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "rb") as f:
                st.download_button("Download data backup (JSON)", f.read(),
                                   file_name=f"accounting_data_{date.today().isoformat()}.json",
                                   mime="application/json", key="dl_json")
        st.caption("The PDF is rebuilt after every change. Make a backup copy of the JSON now and then.")

    t = led.totals_by_type()
    net = t["Revenue"] - t["Expense"]
    m = st.columns(4)
    m[0].metric("Total Assets", money(t["Asset"]))
    m[1].metric("Liabilities", money(t["Liability"]))
    m[2].metric("Equity (incl. profit)", money(t["Equity"] + net))
    m[3].metric("Net Profit / Loss", money(net))

    f = st.session_state.pop("flash", None)
    if f:
        getattr(st, f[0])(f[1])
    if st.session_state.get("pdf_note"):
        st.warning(f"Saved, but the PDF was not updated: {st.session_state['pdf_note']}")

    tabs = st.tabs(["New Entry", "Journal", "Accounts", "Reports", "Edit History"])
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


main()
