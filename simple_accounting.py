"""
Simple Accounting Software - double-entry bookkeeping built on the 5 account types:
    Asset, Liability, Equity, Revenue, Expense
Accounting equation: Assets = Liabilities + Equity (+ Revenue - Expenses)

Run:  python simple_accounting.py
Needs (for the PDF report only):  pip install reportlab

Files (created next to where you run the program):
    accounting_data.json   - your data (the program reads this back)
    accounting_report.pdf  - full report, rebuilt after EVERY change
"""
import json
import os
import hashlib
import getpass
import secrets
from datetime import datetime
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_FILE = os.path.join(BASE_DIR, "accounting_data.json")
LEGACY_DATA_FILE = os.path.join(BASE_DIR, "data.json")
# Support both filenames. If both exist, use the most recently modified data file.
_existing_data_files = [p for p in (DEFAULT_DATA_FILE, LEGACY_DATA_FILE) if os.path.exists(p)]
DATA_FILE = max(_existing_data_files, key=os.path.getmtime) if _existing_data_files else DEFAULT_DATA_FILE
PDF_FILE = os.path.join(BASE_DIR, "accounting_report.pdf")
SECURITY_FILE = os.path.join(BASE_DIR, "accounting_security.json")

# Account types and their "normal" balance side
ACCOUNT_TYPES = {
    "Asset": "debit",
    "Expense": "debit",
    "Liability": "credit",
    "Equity": "credit",
    "Revenue": "credit",
}


@dataclass
class Account:
    code: str
    name: str
    type: str


@dataclass
class JournalEntry:
    id: int
    date: str
    description: str
    lines: list = field(default_factory=list)  # [{"account", "debit", "credit"}]
    reverses: int = None     # id of the entry this one cancels (if it is a reversal)
    reversed_by: int = None  # id of the reversal entry (if this one was cancelled)


class Ledger:
    def __init__(self):
        self.accounts = {}   # code -> Account
        self.entries = []    # list of JournalEntry
        self.audit_log = []   # history of edits to posted entries

    # ---------- Accounts ----------
    def add_account(self, code, name, acc_type):
        if acc_type not in ACCOUNT_TYPES:
            raise ValueError(f"Type must be one of: {', '.join(ACCOUNT_TYPES)}")
        if code in self.accounts:
            raise ValueError(f"Account code {code} already exists")
        self.accounts[code] = Account(code, name, acc_type)

    # ---------- Journal ----------
    def post_entry(self, description, lines, entry_date=None):
        """lines: list of (account_code, debit, credit). Must balance."""
        if entry_date:
            try:
                date.fromisoformat(entry_date)
            except ValueError:
                raise ValueError("Date must be in YYYY-MM-DD format, e.g. 2026-10-03")
        clean = []
        total_dr = total_cr = Decimal("0")
        for code, dr, cr in lines:
            if code not in self.accounts:
                raise ValueError(f"Unknown account: {code}")
            dr, cr = Decimal(str(dr)), Decimal(str(cr))
            if dr < 0 or cr < 0 or (dr > 0 and cr > 0) or (dr == 0 and cr == 0):
                raise ValueError("Each line needs exactly one positive debit OR credit")
            total_dr += dr
            total_cr += cr
            clean.append({"account": code, "debit": str(dr), "credit": str(cr)})
        if len(clean) < 2:
            raise ValueError("An entry needs at least two lines")
        if total_dr != total_cr:
            raise ValueError(f"Entry not balanced: debits {total_dr} != credits {total_cr}")
        entry = JournalEntry(len(self.entries) + 1,
                             entry_date or date.today().isoformat(),
                             description, clean)
        self.entries.append(entry)
        return entry

    def initial_investment(self, amount, asset_code="1010", capital_code="3000", entry_date=None):
        """Opening transaction: owner puts money into the Head Account.
        Dr Head Account, Cr Owner's Capital."""
        if self.accounts[asset_code].type != "Asset":
            raise ValueError("Deposit account must be an Asset account")
        if self.accounts[capital_code].type != "Equity":
            raise ValueError("Capital account must be an Equity account")
        return self.post_entry("Initial investment",
                               [(asset_code, amount, 0), (capital_code, 0, amount)], entry_date)

    def transfer(self, from_code, to_code, amount, note="", entry_date=None):
        """Move money between two Asset accounts (e.g. Head Account -> Savings)."""
        amount = Decimal(str(amount))
        for c in (from_code, to_code):
            if c not in self.accounts or self.accounts[c].type != "Asset":
                raise ValueError(f"{c} must be an existing Asset account")
        if from_code == to_code:
            raise ValueError("Choose two different accounts")
        if self.balance(from_code) < amount:
            raise ValueError(f"Insufficient funds in {self.accounts[from_code].name} "
                             f"(balance {self.balance(from_code)})")
        desc = f"Transfer {self.accounts[from_code].name} -> {self.accounts[to_code].name}"
        desc += f" ({note})" if note else ""
        return self.post_entry(desc, [(to_code, amount, 0), (from_code, 0, amount)], entry_date)

    def get_entry(self, entry_id):
        for e in self.entries:
            if e.id == entry_id:
                return e
        raise ValueError(f"No entry with id {entry_id}")

    def reverse_entry(self, entry_id, reason="", entry_date=None):
        """'Delete' an entry the proper way: post an opposite entry."""
        orig = self.get_entry(entry_id)
        if orig.reversed_by is not None:
            raise ValueError(f"Entry #{entry_id} is already reversed")
        if orig.reverses is not None:
            raise ValueError("A reversal entry cannot itself be reversed")
        lines = [(ln["account"], ln["credit"], ln["debit"]) for ln in orig.lines]  # swap sides
        desc = f"REVERSAL of #{entry_id}: {orig.description}" + (f" ({reason})" if reason else "")
        rev = self.post_entry(desc, lines, entry_date)
        rev.reverses = orig.id
        orig.reversed_by = rev.id
        return rev

    def correct_entry(self, entry_id, new_description, new_lines, reason="Correction"):
        """'Edit' an entry: reverse the old one, then post the corrected one."""
        orig = self.get_entry(entry_id)
        if orig.reversed_by is not None:
            raise ValueError(f"Entry #{entry_id} is already reversed")
        self.post_entry_dry_run(new_lines)  # validate first, so we never reverse without a valid replacement
        self.reverse_entry(entry_id, reason)
        return self.post_entry(new_description, new_lines)

    def post_entry_dry_run(self, lines):
        snapshot = len(self.entries)
        try:
            self.post_entry("dry run", lines)
        finally:
            del self.entries[snapshot:]

    # ---------- Reports ----------
    def balance(self, code):
        """Balance shown on the account's normal side."""
        acc = self.accounts[code]
        dr = cr = Decimal("0")
        for e in self.entries:
            for ln in e.lines:
                if ln["account"] == code:
                    dr += Decimal(ln["debit"])
                    cr += Decimal(ln["credit"])
        return dr - cr if ACCOUNT_TYPES[acc.type] == "debit" else cr - dr

    def ledger_for(self, code):
        rows = []
        for e in self.entries:
            for ln in e.lines:
                if ln["account"] == code:
                    rows.append((e.date, e.description, Decimal(ln["debit"]), Decimal(ln["credit"])))
        return rows

    def trial_balance(self):
        rows, tot_dr, tot_cr = [], Decimal("0"), Decimal("0")
        for code in sorted(self.accounts):
            acc, bal = self.accounts[code], self.balance(code)
            on_debit = ACCOUNT_TYPES[acc.type] == "debit"
            if (bal >= 0) == on_debit:
                dr, cr = abs(bal), Decimal("0")
            else:
                dr, cr = Decimal("0"), abs(bal)
            rows.append((code, acc.name, dr, cr))
            tot_dr += dr
            tot_cr += cr
        return rows, tot_dr, tot_cr

    def totals_by_type(self):
        t = {k: Decimal("0") for k in ACCOUNT_TYPES}
        for code, acc in self.accounts.items():
            t[acc.type] += self.balance(code)
        return t

    def income_statement(self):
        t = self.totals_by_type()
        return t["Revenue"], t["Expense"], t["Revenue"] - t["Expense"]

    def balance_sheet(self):
        t = self.totals_by_type()
        net_income = t["Revenue"] - t["Expense"]
        equity_total = t["Equity"] + net_income
        return t["Asset"], t["Liability"], equity_total, net_income

    # ---------- Persistence ----------
    def save(self, path=DATA_FILE):
        data = {
            "accounts": [vars(a) for a in self.accounts.values()],
            "entries": [vars(e) for e in self.entries],
            "audit_log": self.audit_log,
        }
        tmp = path + ".tmp"          # write to a temp file first, so a crash can
        with open(tmp, "w") as f:    # never leave a half-written data file
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path=DATA_FILE):
        led = cls()
        if not os.path.exists(path):
            return led
        with open(path) as f:
            data = json.load(f)
        for a in data["accounts"]:
            led.accounts[a["code"]] = Account(a["code"], a["name"], a["type"])
        for e in data["entries"]:
            # Older data files may not contain newer optional fields.
            e.setdefault("reverses", None)
            e.setdefault("reversed_by", None)
            led.entries.append(JournalEntry(**e))
        led.audit_log = data.get("audit_log", [])
        return led


# ====================== Command-line interface ======================
def money(x):
    return f"{x:,.2f}"


def export_pdf(led, path=PDF_FILE):
    """Rebuild the full PDF report. Never crashes the program; returns True/False."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        print("  PDF not updated - install reportlab first:  pip install reportlab")
        return False
    from xml.sax.saxutils import escape

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, textColor=colors.HexColor("#1F3A5F"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=4,
                        textColor=colors.HexColor("#1F3A5F"), keepWithNext=1)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    NAVY, LIGHT = colors.HexColor("#1F3A5F"), colors.HexColor("#EEF2F7")
    GRID, SUB = colors.HexColor("#C9D1DC"), colors.HexColor("#DCE4EE")
    W = A4[0] - 30 * mm

    def P(text, bold=False):
        text = escape(str(text))
        return Paragraph(f"<b>{text}</b>" if bold else text, small)

    def table(rows, widths, right=(), bold_rows=(), zebra=True, extra=()):
        t = Table(rows, colWidths=widths, repeatRows=1)
        st = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
              ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8),
              ("GRID", (0, 0), (-1, -1), 0.25, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if zebra:
            st.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]))
        for c in right:
            st.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
        for r in bold_rows:
            st += [("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"), ("BACKGROUND", (0, r), (-1, r), SUB)]
        t.setStyle(TableStyle(st + list(extra)))
        return t

    leaf = sorted(led.accounts)
    t_by = led.totals_by_type()
    net = t_by["Revenue"] - t_by["Expense"]
    rows_tb, tdr, tcr = led.trial_balance()

    story = [Paragraph("Accounting Report", h1), Paragraph(f"Generated: {now}", small), Spacer(1, 6)]

    # Summary
    story.append(table([["Summary", "Amount"],
                        ["Total Assets", money(t_by["Asset"])],
                        ["Total Liabilities", money(t_by["Liability"])],
                        ["Equity (incl. net income)", money(t_by["Equity"] + net)],
                        ["Net Profit / (Loss)", money(net)],
                        ["Journal entries", str(len(led.entries))],
                        ["Trial balance", "Balanced" if tdr == tcr else "NOT BALANCED"]],
                       [W * 0.6, W * 0.4], right=(1,)))

    # Chart of accounts
    story.append(Paragraph("Chart of Accounts", h2))
    rows = [["Code", "Account", "Type", "Balance"]]
    rows += [[c, P(led.accounts[c].name), led.accounts[c].type, money(led.balance(c))] for c in leaf]
    story.append(table(rows, [W * 0.12, W * 0.43, W * 0.2, W * 0.25], right=(3,)))

    # Journal
    story.append(Paragraph("Journal", h2))
    rows = [["#", "Date", "Account / Description", "Debit", "Credit"]]
    extra = []
    for e in led.entries:
        tag = ""
        if e.reversed_by:
            tag = f'  <font color="#B00020">[REVERSED by #{e.reversed_by}]</font>'
        elif e.reverses:
            tag = f'  <font color="#9A6700">[REVERSAL of #{e.reverses}]</font>'
        r = len(rows)
        rows.append([f"#{e.id}", e.date, Paragraph(f"<b>{escape(e.description)}</b>{tag}", small), "", ""])
        extra += [("SPAN", (2, r), (4, r)), ("BACKGROUND", (0, r), (-1, r), SUB)]
        for ln in e.lines:
            dr, cr = Decimal(ln["debit"]), Decimal(ln["credit"])
            nm = escape(f'{ln["account"]} {led.accounts[ln["account"]].name}')
            rows.append(["", "", Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;{nm}", small),
                         money(dr) if dr else "", money(cr) if cr else ""])
    story.append(table(rows, [W * 0.07, W * 0.14, W * 0.43, W * 0.18, W * 0.18], right=(3, 4),
                       zebra=False, extra=extra))

    # Trial balance
    story.append(Paragraph("Trial Balance", h2))
    rows = [["Code", "Account", "Debit", "Credit"]]
    rows += [[c, P(n), money(d) if d else "", money(k) if k else ""] for c, n, d, k in rows_tb]
    rows.append(["", "TOTAL", money(tdr), money(tcr)])
    story.append(table(rows, [W * 0.12, W * 0.43, W * 0.225, W * 0.225], right=(2, 3), bold_rows=(len(rows) - 1,)))

    # Income statement (itemised)
    story.append(Paragraph("Income Statement", h2))
    rows, bold = [["Item", "Amount"]], []
    for typ, label in (("Revenue", "Revenue"), ("Expense", "Expenses")):
        rows.append([label, ""]); bold.append(len(rows) - 1)
        rows += [[P("   " + led.accounts[c].name), money(led.balance(c))]
                 for c in leaf if led.accounts[c].type == typ and led.balance(c) != 0]
        rows.append([f"Total {label}", money(t_by[typ])]); bold.append(len(rows) - 1)
    rows.append(["Net Profit" if net >= 0 else "Net Loss", money(abs(net))]); bold.append(len(rows) - 1)
    story.append(table(rows, [W * 0.7, W * 0.3], right=(1,), bold_rows=bold))

    # Balance sheet (itemised)
    story.append(Paragraph("Balance Sheet", h2))
    rows, bold = [["Item", "Amount"]], []
    for typ, label in (("Asset", "Assets"), ("Liability", "Liabilities"), ("Equity", "Equity")):
        rows.append([label, ""]); bold.append(len(rows) - 1)
        rows += [[P("   " + led.accounts[c].name), money(led.balance(c))]
                 for c in leaf if led.accounts[c].type == typ and led.balance(c) != 0]
        if typ == "Equity":
            rows.append([P("   Net profit / (loss) for the period"), money(net)])
        total = t_by[typ] + (net if typ == "Equity" else 0)
        rows.append([f"Total {label}", money(total)]); bold.append(len(rows) - 1)
    ok = t_by["Asset"] == t_by["Liability"] + t_by["Equity"] + net
    rows.append(["Assets = Liabilities + Equity ?", "YES" if ok else "NO - CHECK"]); bold.append(len(rows) - 1)
    story.append(table(rows, [W * 0.7, W * 0.3], right=(1,), bold_rows=bold))

    # Account ledgers
    story.append(Paragraph("Account Ledgers", h2))
    for c in leaf:
        movements = led.ledger_for(c)
        if not movements:
            continue
        normal_debit = ACCOUNT_TYPES[led.accounts[c].type] == "debit"
        run, rows = Decimal("0"), [["Date", "Description", "Debit", "Credit", "Balance"]]
        for d, desc, dr, cr in movements:
            run += (dr - cr) if normal_debit else (cr - dr)
            rows.append([d, P(desc), money(dr) if dr else "", money(cr) if cr else "", money(run)])
        story.append(Paragraph(escape(f"{c} - {led.accounts[c].name}"), ParagraphStyle(
            "h3", parent=styles["Heading3"], fontSize=10, spaceBefore=8, spaceAfter=3, keepWithNext=1)))
        story.append(table(rows, [W * 0.14, W * 0.38, W * 0.16, W * 0.16, W * 0.16], right=(2, 3, 4)))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(15 * mm, 9 * mm, f"Simple Accounting - generated {now}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    tmp = path + ".tmp"
    try:
        doc = SimpleDocTemplate(tmp, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                                topMargin=15 * mm, bottomMargin=16 * mm, title="Accounting Report")
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        os.replace(tmp, path)
        return True
    except PermissionError:
        print(f"  PDF not updated: close '{path}' if it is open in a viewer. "
              "It will refresh on the next change.")
    except Exception as err:  # never let a PDF problem break bookkeeping
        print(f"  PDF not updated: {err}")
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def persist(led):
    """Save the data file and rebuild the PDF immediately after any change."""
    led.save()
    if export_pdf(led):
        print("  [Saved - PDF report updated]")
    else:
        print("  [Saved - data is safe in accounting_data.json]")


def load_default_accounts(led):
    defaults = [
        ("1000", "Cash", "Asset"),
        ("1010", "Head Account", "Asset"),
        ("1020", "Savings Account", "Asset"),
        ("1100", "Account Receivable", "Asset"),
        ("1500", "Equipment", "Asset"),
        ("2000", "Accounts Payable", "Liability"),
        ("2100", "Bank Loan", "Liability"),
        ("3000", "Owner's Capital", "Equity"),
        ("4000", "Sales Revenue", "Revenue"),
        ("4100", "Interest Income", "Revenue"),
        ("5000", "Rent Expense", "Expense"),
        ("5100", "Salaries Expense", "Expense"),
        ("5200", "Utilities Expense", "Expense"),
        ("1040", "BANK ACCOUNT", "Asset"),
        ("1050", "UNKNOWN", "Asset"),
        ("5050", "TRANSPORT EXPENSE", "Expense"),
        ("1060", "MAMA'S A/R", "Asset"),
        ("4200", "SALARY INCOME", "Revenue"),
        ("5060", "STATIONARY EXPENSE", "Expense"),
        ("1070", "INVESTMENT ACCOUNT", "Asset"),
        ("5070", "BIKE MAINTAINANCE", "Expense"),
    ]
    # Add missing starter accounts without overwriting existing account details.
    for code, name, typ in defaults:
        if code not in led.accounts:
            led.add_account(code, name, typ)


def show_accounts(led):
    print(f"\n{'Code':<8}{'Name':<28}{'Type':<12}{'Balance':>14}")
    print("-" * 62)
    for code in sorted(led.accounts):
        a = led.accounts[code]
        print(f"{code:<8}{a.name:<28}{a.type:<12}{money(led.balance(code)):>14}")


def ask_add_account(led):
    code = input("Account code: ").strip()
    name = input("Account name: ").strip()
    print("Types:", ", ".join(ACCOUNT_TYPES))
    typ = input("Type: ").strip().capitalize()
    led.add_account(code, name, typ)
    print("Account added.")
    persist(led)


BACK, CANCEL = "BACK", "CANCEL"


def prompt_line(led, d=None):
    """Ask for one line: account -> side -> amount.
    'b' = go back one step. If defaults (d) are given, Enter keeps the old value.
    Returns (code, side, amount), BACK (b pressed at the first step) or CANCEL."""
    code, side, amt = d if d else (None, None, None)
    step = 0
    while True:
        if step == 0:
            hint = f" [{code}]" if code else ""
            v = input(f"  Account code{hint} (b = back): ").strip()
            if v.lower() == "b":
                return BACK
            if not v:
                if code:
                    step = 1
                    continue
                return CANCEL
            if v not in led.accounts:
                print("  Unknown account code, try again.")
                continue
            code, step = v, 1
        elif step == 1:
            hint = f" [{side}]" if side else ""
            v = input(f"  {led.accounts[code].name} - Debit or Credit (d/c){hint} (b = back): ").strip().lower()
            if v == "b":
                step = 0
                continue
            if not v and side:
                step = 2
                continue
            if v not in ("d", "c"):
                print("  Type d for Debit or c for Credit.")
                continue
            side, step = v, 2
        else:
            hint = f" [{amt}]" if amt else ""
            v = input(f"  Amount{hint} (b = back): ").strip()
            if v.lower() == "b":
                step = 1
                continue
            if not v and amt:
                return (code, side, amt)
            try:
                a = Decimal(v)
                if not a.is_finite() or a <= 0:
                    raise InvalidOperation
            except InvalidOperation:
                print("  Invalid amount, try again.")
                continue
            return (code, side, a)


def totals(lines):
    dr = sum((a for _, s, a in lines if s == "d"), Decimal("0"))
    cr = sum((a for _, s, a in lines if s == "c"), Decimal("0"))
    return dr, cr


def review_lines(led, lines):
    """Show the entry before posting. Returns 'post', 'cancel' or 'more'."""
    while True:
        print("\n  --- Review ---")
        for n, (c, s, a) in enumerate(lines, 1):
            print(f"  {n}. {c} {led.accounts[c].name:<22} {'Debit ' if s == 'd' else 'Credit'} {money(a):>12}")
        ch = input("  Post this entry? (Enter = Yes, e = fix a line, n = cancel): ").strip().lower()
        if ch in ("", "y"):
            return "post"
        if ch == "n":
            return "cancel"
        if ch == "e":
            try:
                n = int(input("  Which line number to fix: ").strip())
            except ValueError:
                print("  Please type a number.")
                continue
            if not 1 <= n <= len(lines):
                print("  No such line.")
                continue
            print("  (Press Enter to keep a value, or type the correct one)")
            r = prompt_line(led, lines[n - 1])
            if isinstance(r, tuple):
                lines[n - 1] = r
            dr, cr = totals(lines)
            if dr != cr:
                print(f"  Entry is not balanced now [Debit {money(dr)} | Credit {money(cr)}] - fix or add lines.")
                return "more"


def read_lines(led):
    """Read account lines. Posts automatically once Debit = Credit (after a review).
    Returns a list of (code, debit, credit), or None if cancelled."""
    lines, pending = [], None
    print("Enter lines. Type b at any question to go back one step. "
          "(Blank account code = cancel)")
    while True:
        r = prompt_line(led, pending)
        if r == CANCEL:
            if not lines or input("  Cancel the whole entry? (y/n): ").strip().lower() == "y":
                return None
            continue
        if r == BACK:
            if lines:
                pending = lines.pop()
                print("  Back to the previous line - press Enter to keep a value, or type the correct one.")
            else:
                print("  Nothing to go back to.")
            continue
        lines.append(r)
        pending = None
        dr, cr = totals(lines)
        print(f"  [Debit {money(dr)} | Credit {money(cr)}]")
        if len(lines) >= 2 and dr == cr:
            action = review_lines(led, lines)
            if action == "post":
                return [(c, a if s == "d" else 0, a if s == "c" else 0) for c, s, a in lines]
            if action == "cancel":
                return None


def ask_post_entry(led):
    show_accounts(led)
    while True:
        desc = input("\nDescription (blank = back to menu): ").strip()
        if not desc:
            break
        d = input("Date (YYYY-MM-DD, blank = today, b = back): ").strip() or None
        if d and d.lower() == "b":
            continue
        try:
            if d:
                date.fromisoformat(d)
        except ValueError:
            print("Invalid date format. Start this entry again.")
            continue
        lines = read_lines(led)
        if lines is None:
            print("Entry cancelled.")
            continue
        try:
            e = led.post_entry(desc, lines, d)
            print(f"Entry #{e.id} posted.")
            persist(led)
        except ValueError as err:
            print(f"Error: {err}")


def show_journal(led):
    for e in led.entries:
        tag = f"  [REVERSED by #{e.reversed_by}]" if e.reversed_by else ""
        print(f"\n#{e.id}  {e.date}  {e.description}{tag}")
        for ln in e.lines:
            name = led.accounts[ln["account"]].name
            dr, cr = Decimal(ln["debit"]), Decimal(ln["credit"])
            if dr:
                print(f"     Dr {name:<26}{money(dr):>12}")
            else:
                print(f"        Cr {name:<23}{money(cr):>12}")


def show_ledger(led):
    code = input("Account code: ").strip()
    if code not in led.accounts:
        print("Unknown account.")
        return
    print(f"\nLedger: {led.accounts[code].name}")
    print(f"{'Date':<12}{'Description':<30}{'Debit':>12}{'Credit':>12}")
    for d, desc, dr, cr in led.ledger_for(code):
        print(f"{d:<12}{desc[:28]:<30}{money(dr):>12}{money(cr):>12}")
    print(f"Balance: {money(led.balance(code))}")


def show_trial_balance(led):
    rows, tdr, tcr = led.trial_balance()
    print(f"\n{'Code':<8}{'Account':<28}{'Debit':>14}{'Credit':>14}")
    print("-" * 64)
    for code, name, dr, cr in rows:
        print(f"{code:<8}{name:<28}{money(dr) if dr else '':>14}{money(cr) if cr else '':>14}")
    print("-" * 64)
    print(f"{'TOTAL':<36}{money(tdr):>14}{money(tcr):>14}")
    print("Balanced!" if tdr == tcr else "WARNING: not balanced")


def show_income_statement(led):
    rev, exp, net = led.income_statement()
    print("\n--- Income Statement ---")
    print(f"Revenue:   {money(rev):>14}")
    print(f"Expenses:  {money(exp):>14}")
    print(f"Net {'Profit' if net >= 0 else 'Loss'}: {money(abs(net)):>14}")


def show_balance_sheet(led):
    a, l, e, ni = led.balance_sheet()
    print("\n--- Balance Sheet ---")
    print(f"Assets:                      {money(a):>14}")
    print(f"Liabilities:                 {money(l):>14}")
    print(f"Equity (incl. net income):   {money(e):>14}  (net income {money(ni)})")
    print(f"Liabilities + Equity:        {money(l + e):>14}")
    print("Equation holds: Assets = Liabilities + Equity" if a == l + e
          else "WARNING: equation does not balance")


def ask_reverse_entry(led):
    show_journal(led)
    eid = int(input("\nEntry # to delete (reverse): ").strip())
    reason = input("Reason: ").strip()
    rev = led.reverse_entry(eid, reason)
    print(f"Entry #{eid} cancelled by reversal entry #{rev.id}.")
    persist(led)


def accountant_authorized():
    """Require an accountant PIN before allowing posted-entry edits."""
    if not os.path.exists(SECURITY_FILE):
        print("\nFirst-time setup: create an accountant-only PIN.")
        while True:
            pin1 = getpass.getpass("Create accountant PIN (minimum 6 characters): ")
            if len(pin1) < 6:
                print("PIN must be at least 6 characters.")
                continue
            pin2 = getpass.getpass("Confirm accountant PIN: ")
            if pin1 != pin2:
                print("PINs do not match. Try again.")
                continue
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac(
                "sha256", pin1.encode("utf-8"), bytes.fromhex(salt), 200_000
            ).hex()
            with open(SECURITY_FILE, "w", encoding="utf-8") as f:
                json.dump({"pin_salt": salt, "pin_hash": digest}, f, indent=2)
            print("Accountant PIN created.")
            return True

    with open(SECURITY_FILE, encoding="utf-8") as f:
        config = json.load(f)
    entered = getpass.getpass("Accountant PIN required to edit an entry: ")
    salt = config.get("pin_salt", "")
    expected = config.get("pin_hash", "")
    digest = hashlib.pbkdf2_hmac(
        "sha256", entered.encode("utf-8"), bytes.fromhex(salt), 200_000
    ).hex() if salt and expected else ""
    if not secrets.compare_digest(digest, expected):
        print("Access denied. Only an authorized accountant can edit entries.")
        return False
    return True


def ask_correct_entry(led):
    """Edit an entry in place, but keep an audit record of the old and new values."""
    show_journal(led)
    if not accountant_authorized():
        return

    try:
        eid = int(input("\nEntry number to edit: ").strip())
    except ValueError:
        print("Please enter a valid entry number.")
        return

    old = led.get_entry(eid)
    if old.reversed_by is not None:
        print("This entry has already been reversed. It cannot be edited.")
        return
    if old.reverses is not None:
        print("A reversal entry cannot be edited. Edit the original entry if appropriate.")
        return

    old_snapshot = {
        "date": old.date,
        "description": old.description,
        "lines": [dict(line) for line in old.lines],
    }

    print(f"\nEditing entry #{old.id}: {old.date} | {old.description}")
    desc_input = input("New description (Enter = keep current): ").strip()
    new_desc = desc_input or old.description

    date_input = input("New date YYYY-MM-DD (Enter = keep current): ").strip()
    new_date = old.date
    if date_input:
        try:
            date.fromisoformat(date_input)
            new_date = date_input
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD. Nothing changed.")
            return

    change_lines = input("Change accounts/debit/credit amounts too? (y/n): ").strip().lower()
    new_lines = old.lines
    if change_lines == "y":
        print("Enter the corrected complete set of debit/credit lines.")
        entered_lines = read_lines(led)
        if entered_lines is None:
            print("Edit cancelled. Nothing changed.")
            return
        # Validate without leaving a temporary entry behind.
        try:
            led.post_entry_dry_run(entered_lines)
        except (ValueError, InvalidOperation) as err:
            print(f"Invalid replacement lines: {err}. Nothing changed.")
            return
        new_lines = [
            {"account": code, "debit": str(Decimal(str(dr))), "credit": str(Decimal(str(cr)))}
            for code, dr, cr in entered_lines
        ]
    elif change_lines not in ("n", ""):
        print("Please type y or n. Nothing changed.")
        return

    print("\n--- Proposed correction ---")
    print(f"Entry #:     {old.id}")
    print(f"Date:        {old.date} -> {new_date}")
    print(f"Description: {old.description} -> {new_desc}")
    if new_lines != old.lines:
        print("Accounting lines/amounts will also change.")
    confirm = input("Save this correction? (y/n): ").strip().lower()
    if confirm != "y":
        print("Edit cancelled. Nothing changed.")
        return

    old.date = new_date
    old.description = new_desc
    old.lines = new_lines
    led.audit_log.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": "EDIT_ENTRY",
        "entry_id": eid,
        "before": old_snapshot,
        "after": {
            "date": old.date,
            "description": old.description,
            "lines": [dict(line) for line in old.lines],
        },
    })
    print(f"Entry #{eid} updated. The previous values were saved in the audit log.")
    persist(led)



def show_audit_log(led):
    print("\n--- Entry Edit History ---")
    if not led.audit_log:
        print("No entry edits have been recorded.")
        return
    for item in led.audit_log:
        before = item.get("before", {})
        after = item.get("after", {})
        print(f"\n{item.get('timestamp', 'Unknown time')} | Entry #{item.get('entry_id', '?')}")
        print(f"  Date:        {before.get('date', '?')} -> {after.get('date', '?')}")
        print(f"  Description: {before.get('description', '?')} -> {after.get('description', '?')}")
        old_lines = before.get("lines", [])
        new_lines = after.get("lines", [])
        if old_lines != new_lines:
            print("  Accounting lines/amounts changed.")


def ask_initial_investment(led):
    amt = Decimal(input("Initial amount invested by owner: ").strip())
    d = input("Date (YYYY-MM-DD, blank = today): ").strip() or None
    e = led.initial_investment(amt, entry_date=d)
    print(f"Initial investment posted in Head Account as entry #{e.id}.")
    persist(led)


def ask_transfer(led):
    show_accounts(led)
    src = input("\nTransfer FROM account code [1010 Head Account]: ").strip() or "1010"
    dst = input("Transfer TO account code [1020 Savings Account]: ").strip() or "1020"
    amt = Decimal(input("Amount: ").strip())
    note = input("Description / note (optional): ").strip()
    d = input("Date (YYYY-MM-DD, blank = today): ").strip() or None
    e = led.transfer(src, dst, amt, note, entry_date=d)
    print(f"Transfer posted as entry #{e.id}.")
    persist(led)


MENU = """
===== SIMPLE ACCOUNTING =====
1. View chart of accounts
2. Add account
3. Post journal entry
4. View journal
5. View account ledger
6. Trial balance
7. Income statement
8. Balance sheet
9. Delete an entry (post reversal)
10. Edit an entry (accountant PIN required; changes are audit-logged)
11. Initial investment (into Head Account)
12. Transfer between accounts (e.g. to Savings)
13. View entry edit history
0. Save & exit
"""


def main():
    led = Ledger.load()
    before_codes = set(led.accounts)
    load_default_accounts(led)
    added_codes = set(led.accounts) - before_codes
    if not before_codes:
        print("Starter chart of accounts created.")
    elif added_codes:
        print("Added missing default accounts:", ", ".join(sorted(added_codes)))
    persist(led)  # makes sure the PDF exists and matches the data right at startup
    actions = {"1": show_accounts, "2": ask_add_account, "3": ask_post_entry,
               "4": show_journal, "5": show_ledger, "6": show_trial_balance,
               "7": show_income_statement, "8": show_balance_sheet,
               "9": ask_reverse_entry, "10": ask_correct_entry,
               "11": ask_initial_investment, "12": ask_transfer, "13": show_audit_log}
    while True:
        print(MENU)
        choice = input("Choose: ").strip()
        if choice == "0":
            persist(led)
            print("Goodbye!")
            break
        action = actions.get(choice)
        if not action:
            print("Invalid choice.")
            continue
        try:
            action(led)
        except (ValueError, InvalidOperation) as err:
            print(f"Error: {err}")


if __name__ == "__main__":
    main()