import os
import re
import io
import csv
import hmac
import json
import math
import time
import uuid
import base64
import hashlib
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from docx import Document
from docx.shared import Inches, Pt, RGBColor

APP_NAME = "GST FINDER"
DB_PATH = Path(os.getenv("GST_DB_PATH", "gst_finder.sqlite3"))
DISCLAIMER = (
    "GST FINDER IS AN EDUCATIONAL TOOL, NOT AN OFFICIAL GOVERNMENT PLATFORM OR SUBSTITUTE "
    "FOR PROFESSIONAL TAX ADVICE. VERIFY ALL GST RATES, CLASSIFICATIONS AND VALUATION METHODS "
    "AGAINST CURRENT OFFICIAL NOTIFICATIONS BEFORE MAKING TAX OR BUSINESS DECISIONS."
)
CATEGORIES = [
    "Essential food products", "Agricultural products", "Healthcare and medicines",
    "Educational products", "Household essentials", "Personal care", "Electronics",
    "Clothing and footwear", "Industrial products", "Luxury goods", "Other products"
]
GST_STATUSES = ["TAXABLE", "EXEMPT", "NIL_RATED", "ZERO_RATED", "NON_GST", "UNVERIFIED"]
VERIFICATION = ["VERIFIED", "PROVISIONAL", "UNVERIFIED"]
CENT = Decimal("0.01")

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide", initial_sidebar_state="expanded")

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with get_conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            manufacturer TEXT DEFAULT '',
            flavour TEXT DEFAULT '',
            hsn TEXT DEFAULT '',
            description TEXT DEFAULT '',
            gst_status TEXT NOT NULL,
            gst_rate TEXT,
            cgst_rate TEXT,
            sgst_rate TEXT,
            igst_rate TEXT,
            cess_rate TEXT,
            source_name TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            notification_ref TEXT DEFAULT '',
            effective_date TEXT DEFAULT '',
            verified_date TEXT DEFAULT '',
            verification_status TEXT NOT NULL,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_products_name ON products(product_name);
        CREATE INDEX IF NOT EXISTS idx_products_hsn ON products(hsn);
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            record_type TEXT NOT NULL,
            query_text TEXT DEFAULT '',
            product_id INTEGER,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)
init_db()

def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return digest.hex(), salt.hex()

def verify_password(password, stored_hash, salt_hex):
    try:
        candidate, _ = hash_password(password, salt_hex)
        return hmac.compare_digest(candidate, stored_hash)
    except Exception:
        return False

def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)

def dec(value, field="Amount"):
    try:
        result = Decimal(str(value).strip())
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, AttributeError):
        raise ValueError(f"{field} must be a valid number.")

def fmt(value):
    return f"₹{money(value):,.2f}"

def record_history(record_type, query_text, product_id, payload):
    user = st.session_state.get("user")
    if not user:
        return
    with get_conn() as con:
        con.execute(
            "INSERT INTO history(user_id,record_type,query_text,product_id,payload,created_at) VALUES(?,?,?,?,?,?)",
            (user["id"], record_type, query_text, product_id, json.dumps(payload, default=str), now_iso())
        )

def authenticate(username, password):
    with get_conn() as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if row and verify_password(password, row["password_hash"], row["salt"]):
        return {"id": row["id"], "username": row["username"]}
    return None

def register_user(username, password, consent):
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        return False, "Username must be 3–40 characters (letters, numbers, _, . or -)."
    if len(password) < 12:
        return False, "Use a password with at least 12 characters."
    if not consent:
        return False, "Consent is required to create an account."
    pwh, salt = hash_password(password)
    try:
        with get_conn() as con:
            con.execute(
                "INSERT INTO users(username,password_hash,salt,created_at,consent_at) VALUES(?,?,?,?,?)",
                (username, pwh, salt, now_iso(), now_iso())
            )
        return True, "Account created. You can now sign in."
    except sqlite3.IntegrityError:
        return False, "That username is already registered."

def product_search(query, category=None, manufacturer=None, hsn=None):
    q = (query or "").strip().casefold()
    h = (hsn or "").strip()
    with get_conn() as con:
        rows = con.execute("SELECT * FROM products ORDER BY product_name").fetchall()
    found = []
    for row in rows:
        d = dict(row)
        name = d["product_name"].casefold()
        desc = (d["description"] or "").casefold()
        maker = (d["manufacturer"] or "").casefold()
        row_hsn = (d["hsn"] or "").replace(" ", "")
        if category and category != "All categories" and d["category"] != category:
            continue
        if manufacturer and manufacturer.strip() and manufacturer.casefold() not in maker:
            continue
        if h and h.replace(" ", "") not in row_hsn:
            continue
        if q and not (q == name or q in name or name in q or q in desc or q in maker):
            continue
        found.append(d)
    return found

def gst_calculation(amount, rate, amount_type, tax_type):
    amount = dec(amount, "Amount")
    rate = dec(rate, "GST rate")
    if amount < 0:
        raise ValueError("Amount cannot be negative.")
    if rate < 0 or rate > 100:
        raise ValueError("GST rate must be between 0 and 100.")
    if amount_type == "GST Inclusive":
        taxable = amount / (Decimal("1") + rate / Decimal("100")) if rate else amount
        tax = amount - taxable
        total = amount
    else:
        taxable = amount
        tax = taxable * rate / Decimal("100")
        total = taxable + tax
    taxable, tax, total = money(taxable), money(tax), money(total)
    if tax_type == "CGST + SGST":
        cgst = money(tax / 2)
        sgst = money(tax - cgst)
        igst = Decimal("0.00")
    else:
        cgst = sgst = Decimal("0.00")
        igst = tax
    return {
        "taxable": taxable, "rate": rate, "tax": tax, "cgst": cgst,
        "sgst": sgst, "igst": igst, "total": total, "amount_type": amount_type,
        "tax_type": tax_type
    }

def bill_text(product, quantity, hsn, result):
    name = product or "User-entered item"
    return (
        "GST CALCULATION PREVIEW\n"
        "----------------------------------------\n"
        f"Product: {name}\nQuantity: {quantity}\nHSN: {hsn or 'Not supplied'}\n"
        f"Taxable value: {fmt(result['taxable'])}\nGST rate: {result['rate']}%\n"
        f"CGST: {fmt(result['cgst'])}\nSGST: {fmt(result['sgst'])}\nIGST: {fmt(result['igst'])}\n"
        f"Total GST: {fmt(result['tax'])}\n"
        "----------------------------------------\n"
        f"TOTAL PAYABLE: {fmt(result['total'])}\n\n"
        "This is a calculation preview, not necessarily a legally compliant GST tax invoice.\n"
        + DISCLAIMER
    )

def make_pdf(text_value):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    story = [Paragraph("GST FINDER — Calculation Preview", styles["Title"]), Spacer(1, 8)]
    for line in text_value.splitlines():
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(safe or " ", styles["BodyText"]))
        story.append(Spacer(1, 3))
    doc.build(story)
    return buf.getvalue()

def make_docx(text_value):
    doc = Document()
    doc.add_heading("GST FINDER — Calculation Preview", 0)
    for line in text_value.splitlines():
        p = doc.add_paragraph(line)
        if line.startswith("TOTAL PAYABLE"):
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(14)
                run.font.color.rgb = RGBColor(20, 55, 100)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()

def admin_key_valid(candidate):
    expected = os.getenv("GST_ADMIN_KEY", "")
    return bool(expected) and hmac.compare_digest(candidate, expected)

def import_product_csv(upload):
    df = pd.read_csv(upload, dtype=str).fillna("")
    required = {"product_name", "category", "gst_status", "verification_status"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError("CSV missing required columns: " + ", ".join(sorted(missing)))
    allowed = {
        "product_name","category","manufacturer","flavour","hsn","description","gst_status",
        "gst_rate","cgst_rate","sgst_rate","igst_rate","cess_rate","source_name","source_url",
        "notification_ref","effective_date","verified_date","verification_status","notes"
    }
    unknown = set(df.columns) - allowed
    if unknown:
        raise ValueError("Unsupported CSV columns: " + ", ".join(sorted(unknown)))
    inserted = 0
    with get_conn() as con:
        for _, r in df.iterrows():
            if not r["product_name"].strip():
                continue
            status = r["gst_status"].strip().upper()
            ver = r["verification_status"].strip().upper()
            if status not in GST_STATUSES or ver not in VERIFICATION:
                raise ValueError("Invalid GST or verification status in CSV.")
            rate = r.get("gst_rate", "").strip()
            if ver == "VERIFIED" and (not r.get("source_url","").strip() or not r.get("effective_date","").strip()):
                raise ValueError("Verified rows require source_url and effective_date.")
            if rate:
                rv = dec(rate, "GST rate")
                if rv < 0 or rv > 100:
                    raise ValueError("GST rate must be between 0 and 100.")
            values = {k: r.get(k, "") for k in allowed}
            con.execute("""
                INSERT INTO products(product_name,category,manufacturer,flavour,hsn,description,gst_status,
                gst_rate,cgst_rate,sgst_rate,igst_rate,cess_rate,source_name,source_url,notification_ref,
                effective_date,verified_date,verification_status,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, tuple(values.get(k,"") for k in [
                "product_name","category","manufacturer","flavour","hsn","description","gst_status",
                "gst_rate","cgst_rate","sgst_rate","igst_rate","cess_rate","source_name","source_url",
                "notification_ref","effective_date","verified_date","verification_status","notes"
            ]) + (now_iso(),))
            inserted += 1
    return inserted

def show_auth():
    st.title("Welcome to GST FINDER")
    st.caption("Search reference data and calculate GST. Guest mode does not save history.")
    tab1, tab2 = st.tabs(["Sign In", "Create Account"])
    with tab1:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("Sign In", use_container_width=True)
        if submit:
            user = authenticate(username, password)
            if user:
                st.session_state.user = user
                st.session_state.guest = False
                st.rerun()
            st.error("Invalid username or password.")
    with tab2:
        with st.form("register_form"):
            new_user = st.text_input("Choose username")
            new_password = st.text_input("Password (minimum 12 characters)", type="password")
            consent = st.checkbox("I consent to account creation and understand that my username and password hash are stored for authentication.")
            create = st.form_submit_button("Create Account", use_container_width=True)
        if create:
            ok, msg = register_user(new_user, new_password, consent)
            (st.success if ok else st.error)(msg)
    st.divider()
    if st.button("Continue as Guest", use_container_width=True):
        st.session_state.user = None
        st.session_state.guest = True
        st.rerun()
    st.caption("Avoid entering Aadhaar, PAN, bank credentials or other unnecessary personal information.")

def main():
    if "user" not in st.session_state:
        st.session_state.user = None
    if "guest" not in st.session_state:
        st.session_state.guest = False
    if "theme" not in st.session_state:
        st.session_state.theme = "Light"
    if "calc_result" not in st.session_state:
        st.session_state.calc_result = None
    if not st.session_state.user and not st.session_state.guest:
        show_auth()
        return

    st.markdown("""
    <style>
    .stApp {background-color: #f5f8fc;}
    [data-testid="stSidebar"] {background: #eaf1fb;}
    .hero {padding: 1.1rem 1.3rem; border-radius: 14px; background: linear-gradient(110deg,#102f59,#2467a8); color: white;}
    .small-note {font-size: .88rem; color: #526477;}
    div.stButton > button {border-radius: 9px;}
    </style>
    """, unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("## 🧾 GST FINDER")
        st.caption("Indian GST reference & calculator")
        st.session_state.theme = st.toggle("Dark mode", value=(st.session_state.theme == "Dark")) and "Dark" or "Light"
        page = st.radio("NAVIGATION", ["HOME", "INFO", "HISTORY"])
        st.divider()
        who = st.session_state.user["username"] if st.session_state.user else "Guest"
        st.caption(f"Signed in as: **{who}**")
        if st.button("Sign out", use_container_width=True):
            st.session_state.user = None
            st.session_state.guest = False
            st.session_state.calc_result = None
            st.rerun()

    if st.session_state.theme == "Dark":
        st.markdown("<style>.stApp{background:#101820;color:#f3f6fa;} [data-testid='stSidebar']{background:#172536;} .stMarkdown,.stText{color:inherit;}</style>", unsafe_allow_html=True)

    if page == "HOME":
        home_page()
    elif page == "INFO":
        info_page()
    else:
        history_page()

def home_page():
    st.markdown('<div class="hero"><h1>GST Rate Finder</h1><p>Find a supported classification, review its source, and calculate GST.</p></div>', unsafe_allow_html=True)
    st.warning(DISCLAIMER)
    st.subheader("1. Find a product")
    with st.form("search_form"):
        c1, c2 = st.columns([3,1])
        with c1:
            query = st.text_input("Product name or description", placeholder="e.g. product name", key="product_query")
        with c2:
            hsn = st.text_input("Optional HSN code")
        c3, c4 = st.columns(2)
        with c3:
            category = st.selectbox("Category", ["All categories"] + CATEGORIES)
        with c4:
            manufacturer = st.text_input("Manufacturer (optional)")
        search = st.form_submit_button("SEARCH GST RATE", use_container_width=True)
    if search:
        st.session_state.search_results = product_search(query, category, manufacturer, hsn)
        st.session_state.search_query = query
        if st.session_state.search_results:
            record_history("SEARCH", query, None, {"matched_ids":[x["id"] for x in st.session_state.search_results]})
        elif st.session_state.user:
            record_history("SEARCH", query, None, {"result":"NO_MATCH"})
    results = st.session_state.get("search_results", [])
    if search and not results:
        st.error("PRODUCT NOT FOUND. PLEASE VERIFY THE PRODUCT NAME OR HSN CODE.")
    if results:
        st.caption(f"{len(results)} matching record(s). Select the exact product/description.")
        labels = [f'{r["product_name"]} | HSN {r["hsn"] or "not supplied"} | {r["verification_status"]}' for r in results]
        choice = st.selectbox("Matching records", range(len(results)), format_func=lambda i: labels[i])
        product = results[choice]
        with st.container(border=True):
            st.markdown(f"### {product['product_name'].upper()}")
            st.write(f"**Category:** {product['category']}  |  **HSN:** {product['hsn'] or 'Not supplied'}")
            st.write(f"**Description:** {product['description'] or 'Not supplied'}")
            status = product["verification_status"]
            if status == "VERIFIED":
                st.success(f"{product['gst_status']} · {product['gst_rate'] or 'Rate not supplied'}%")
            elif product["gst_status"] in ("EXEMPT","NIL_RATED"):
                st.info(f"{product['gst_status']} · verification status: {status}")
            else:
                st.warning(f"Verification required: {status}. Do not rely on this record for compliance.")
            a,b,c = st.columns(3)
            a.metric("GST rate", (product["gst_rate"] + "%") if product["gst_rate"] else "Not established")
            b.metric("CGST / SGST", f'{product["cgst_rate"] or "—"}% / {product["sgst_rate"] or "—"}%')
            c.metric("IGST", f'{product["igst_rate"] or "—"}%')
            st.write(f"**Cess:** {product['cess_rate'] or 'Not supplied'}")
            st.write(f"**Source:** {product['source_name'] or 'Not supplied'}")
            if product["source_url"]:
                st.markdown(f"[Open source reference]({product['source_url']})")
            st.write(f"**Notification reference:** {product['notification_ref'] or 'Not supplied'}")
            st.write(f"**Effective date:** {product['effective_date'] or 'Not supplied'}")
            st.write(f"**Verified date:** {product['verified_date'] or 'Not supplied'}")
            st.write(f"**Status:** {status}")
            if st.button("Use this product in calculator", key=f"use_{product['id']}"):
                st.session_state.selected_product = product
                if product["gst_rate"] and product["verification_status"] == "VERIFIED":
                    st.session_state.prefill_rate = product["gst_rate"]
                st.success("Product selected for the calculator below.")
        st.markdown("**Popular searches**")
        with get_conn() as con:
            popular = con.execute("SELECT id,product_name,hsn,gst_rate FROM products WHERE verification_status='VERIFIED' ORDER BY product_name LIMIT 8").fetchall()
        if popular:
            cols = st.columns(min(4, len(popular)))
            for i, p in enumerate(popular):
                with cols[i % len(cols)]:
                    st.caption(f"{p['product_name']} · {p['gst_rate'] or 'Rate n/a'}%")
        else:
            st.caption("Popular searches will appear after verified product records are added.")
    elif not search:
        st.info("Searches use only records loaded into the reference database. No rate is assumed when no reliable record exists.")

    st.divider()
    st.subheader("2. GST amount calculator")
    selected = st.session_state.get("selected_product", {})
    with st.form("calculator_form"):
        c1,c2 = st.columns(2)
        with c1:
            item_name = st.text_input("Product / item", value=selected.get("product_name",""))
            quantity = st.number_input("Quantity", min_value=0.0, value=1.0, step=1.0)
            amount = st.text_input("Amount (₹)", value="1000.00")
        with c2:
            suggested = st.session_state.get("prefill_rate", selected.get("gst_rate",""))
            rate = st.text_input("GST rate (%)", value=str(suggested))
            amount_type = st.radio("Amount type", ["GST Exclusive","GST Inclusive"], horizontal=True)
            tax_type = st.radio("Tax type", ["CGST + SGST","IGST"], horizontal=True)
        manual_override = st.checkbox("I understand this is a manual rate entry/override if not populated from a verified record.")
        calc = st.form_submit_button("CALCULATE GST AMOUNT", use_container_width=True)
    if calc:
        try:
            if not rate.strip():
                raise ValueError("Enter a GST rate. No rate will be assumed.")
            selected_rate_is_verified = (
                selected.get("verification_status") == "VERIFIED"
                and bool(selected.get("gst_rate"))
                and str(rate).strip() == str(selected.get("gst_rate")).strip()
            )
            if not selected_rate_is_verified and not manual_override:
                raise ValueError("Tick the manual rate acknowledgement before calculating with a manually entered or unverified rate.")
            result = gst_calculation(amount, rate, amount_type, tax_type)
            st.session_state.calc_result = result
            st.session_state.bill = bill_text(item_name, quantity, selected.get("hsn",""), result)
            record_history("CALCULATION", item_name, selected.get("id"), result)
        except Exception as e:
            st.error(str(e))
    if st.session_state.calc_result:
        r = st.session_state.calc_result
        st.markdown("#### Calculation result")
        x1,x2,x3 = st.columns(3)
        x1.metric("Taxable value", fmt(r["taxable"]))
        x2.metric("Total GST", fmt(r["tax"]))
        x3.metric("Final payable", fmt(r["total"]))
        st.write(f"CGST: **{fmt(r['cgst'])}** · SGST: **{fmt(r['sgst'])}** · IGST: **{fmt(r['igst'])}**")
        st.caption("The selected tax type is an arithmetic choice only. Confirm place of supply and applicable tax treatment before use.")
        st.subheader("Bill preview")
        st.code(st.session_state.bill)
        txt = st.session_state.bill.encode("utf-8")
        d1,d2,d3 = st.columns(3)
        d1.download_button("Download TXT", txt, "gst_calculation.txt", "text/plain", use_container_width=True)
        d2.download_button("Download PDF", make_pdf(st.session_state.bill), "gst_calculation.pdf", "application/pdf", use_container_width=True)
        d3.download_button("Download DOCX", make_docx(st.session_state.bill), "gst_calculation.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
        st.info("This exported calculation preview is not necessarily a legally compliant GST tax invoice.")

    st.divider()
    with st.expander("3. Taxable value / valuation reference workflow"):
        st.markdown("""
        **Start with Section 15 (value of supply) before calculating GST.** Review transaction value, non-monetary consideration, related/distinct persons, agent transactions, statutory inclusions/exclusions, discounts, subsidies and reimbursements.

        | Rule | Subject | Application control |
        |---|---|---|
        | 27 | Consideration not wholly in money | Record monetary and non-monetary consideration; obtain legal review of the prescribed sequence |
        | 28 | Related/distinct persons (excluding agents) | Check open market value, like kind/quality, cost/residual methods and full-ITC proviso |
        | 29 | Principal-agent goods transactions | Keep separate from pure-agent expenditure |
        | 30 | Cost-based valuation | Cost plus 10% is shown as a calculation aid only where Rule 30 is legally applicable |
        | 31 | Residual method | Requires reasoned application consistent with Section 15 and Rules 27–30 |
        | 33 | Pure-agent expenditure | Exclusion only if all statutory conditions are met; documentary review required |

        **Important:** This screen is a checklist, not a legal determination engine. Do not select a rule merely because it produces a preferred value. Confirm the facts, statutory sequence, applicable amendments and supporting documents with a GST professional.
        """)
        rule = st.selectbox("Valuation topic", ["Section 15 review","Rule 27","Rule 28","Rule 29","Rule 30","Rule 31","Rule 33"])
        if rule == "Rule 30":
            cost = st.text_input("Eligible cost of supply (₹)", key="r30_cost")
            if st.button("Calculate cost + 10% (Rule 30 arithmetic only)"):
                try:
                    base = dec(cost, "Cost")
                    if base < 0: raise ValueError("Cost cannot be negative.")
                    st.success(f"Arithmetic result: {fmt(base * Decimal('1.10'))}. Confirm Rule 30 applicability and cost components before relying on it.")
                except Exception as e: st.error(str(e))
        elif rule == "Rule 33":
            st.warning("Rule 33 pure-agent exclusion requires every prescribed condition to be met and evidenced. This application does not automatically approve an exclusion.")
        else:
            st.info("Capture the transaction facts and obtain a documented legal valuation review before finalising taxable value.")

    with st.expander("Update GST reference database (authorised administrator only)"):
        st.caption("Set GST_ADMIN_KEY as a deployment secret/environment variable. Import only reviewed CSV records with official references.")
        admin_key = st.text_input("Administrator key", type="password")
        upload = st.file_uploader("Reviewed GST product CSV", type=["csv"])
        if st.button("Validate and import CSV"):
            if not admin_key_valid(admin_key):
                st.error("Administrator authentication failed.")
            elif not upload:
                st.error("Choose a CSV file first.")
            else:
                try:
                    count = import_product_csv(upload)
                    st.success(f"Imported {count} product record(s).")
                except Exception as e:
                    st.error(f"Import rejected: {e}")
        st.download_button("Download CSV template", data=(
            "product_name,category,manufacturer,flavour,hsn,description,gst_status,gst_rate,cgst_rate,sgst_rate,igst_rate,cess_rate,source_name,source_url,notification_ref,effective_date,verified_date,verification_status,notes\n"
            "Example product,Other products,,,,,UNVERIFIED,,,,,,,,,,,UNVERIFIED,Do not use as a rate record\n"
        ), file_name="gst_products_template.csv", mime="text/csv")

def info_page():
    st.title("GST Finder — Information Centre")
    st.warning(DISCLAIMER)
    st.subheader("How to use GST Finder")
    steps = [
        "Enter a product name or HSN code and optionally narrow by category/manufacturer.",
        "If multiple matches appear, select the exact product description and conditions.",
        "Review the HSN, GST status, rate, source, notification reference and effective date.",
        "Use only VERIFIED records supported by an official source for reference; still check current law.",
        "Enter an amount and rate in the calculator. A selected verified product can prefill the rate.",
        "Choose GST Inclusive or GST Exclusive and the relevant tax split.",
        "Review the bill preview and download TXT, PDF or DOCX.",
        "Registered users can review and delete saved history. Guest history is not stored."
    ]
    for i, step in enumerate(steps, 1):
        st.write(f"**{i}.** {step}")
    st.divider()
    st.subheader("GST terminology")
    terms = {
        "GST":"Goods and Services Tax, an indirect tax on supplies of goods and services.",
        "CGST":"Central GST component generally levied by the Centre on intra-State supplies.",
        "SGST":"State GST component generally levied by the State on intra-State supplies.",
        "IGST":"Integrated GST generally applicable to inter-State supplies and imports, subject to law.",
        "HSN":"Harmonized System of Nomenclature used to classify goods for tax and trade purposes.",
        "Taxable supply":"A supply on which GST is leviable under the applicable law.",
        "Exempt supply":"A supply exempted from GST under the relevant statutory provisions/notifications.",
        "NIL-rated supply":"A taxable supply whose applicable rate is nil.",
        "Zero-rated supply":"Specified exports and supplies to SEZ units/developers under the IGST Act, subject to conditions.",
        "Non-GST supply":"A supply outside the levy of GST under the current GST framework; do not confuse with exempt or nil-rated."
    }
    for term, meaning in terms.items():
        with st.expander(term):
            st.write(meaning)
    st.subheader("Inclusive and exclusive pricing")
    st.write("Exclusive: GST is added to the taxable amount. Inclusive: GST is included in the entered amount and is extracted using the applicable rate.")
    st.subheader("DID YOU KNOW?")
    st.info("The same broad product name can map to different tariff entries or conditions. Packaging, processing, composition, intended use and notification wording may affect classification.")
    st.subheader("Privacy")
    st.write("Account records contain a username and a password hash. Registered-user search/calculation history is stored in the local SQLite database and can be deleted from History. Guest history is not written to the database. Before production use, configure secure hosting, encrypted storage/backups, access controls, retention and a reviewed privacy notice.")

def history_page():
    st.title("History")
    if not st.session_state.user:
        st.info("Guest history is not saved. Sign in to use persistent history.")
        return
    user_id = st.session_state.user["id"]
    with get_conn() as con:
        rows = con.execute("SELECT * FROM history WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    if not rows:
        st.info("No saved history yet.")
        return
    df = pd.DataFrame([dict(r) for r in rows])
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    col1,col2 = st.columns(2)
    with col1:
        term = st.text_input("Filter by product/query")
    with col2:
        start = st.date_input("From date", value=(date.today()-timedelta(days=365)))
    filtered = df[df["created_at"].dt.date >= start]
    if term:
        filtered = filtered[filtered["query_text"].str.contains(term, case=False, na=False)]
    st.dataframe(filtered[["id","record_type","query_text","created_at","payload"]], use_container_width=True, hide_index=True)
    selected_id = st.number_input("Record ID to delete", min_value=1, step=1)
    c1,c2 = st.columns(2)
    with c1:
        if st.button("Delete selected record"):
            with get_conn() as con:
                con.execute("DELETE FROM history WHERE id=? AND user_id=?", (int(selected_id), user_id))
            st.success("Record deleted.")
            st.rerun()
    with c2:
        if st.button("Clear all my history"):
            with get_conn() as con:
                con.execute("DELETE FROM history WHERE user_id=?", (user_id,))
            st.success("All history deleted.")
            st.rerun()
    st.caption("History deletion removes the records from this application's SQLite database. Backup copies and hosting-provider retention must be managed separately.")

if __name__ == "__main__":
    main()
