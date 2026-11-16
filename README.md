# GST FINDER

A Streamlit-based Indian GST reference search and calculator starter application.

## Included
- HOME, INFO and HISTORY pages
- Sign-in, registration and guest access
- Scrypt password hashing
- SQLite users, product reference records and registered-user history
- Exact/partial/case-insensitive product and HSN matching
- Verified/provisional/unverified status display
- GST inclusive/exclusive calculator using `Decimal`
- CGST + SGST or IGST arithmetic
- TXT, PDF and DOCX calculation-preview downloads
- CSV import for authorised GST reference-data administrators
- Basic dark/light mode
- Guest history is not persisted

## Important legal/data limitation
The application starts with an **empty GST product database**. It does not ship invented or assumed GST rates/HSN codes. Populate it only with reviewed records supported by the applicable official notification/schedule and effective date. A `VERIFIED` CSV row must include `source_url` and `effective_date`.

The research material supplied for this project includes CBIC schedules labelled as on 01-04-2023 and secondary discussion of selected 2025 changes. These are not a complete current consolidated rate schedule. Verify the operative notification and classification before adding records.

The valuation panel is an educational checklist, not a legal valuation engine. Rule 29 (principal-agent goods) is distinct from Rule 33 (pure-agent expenditure). Rule 30 arithmetic is provided only as cost + 10% and must only be used after confirming applicability and the eligible cost base. Rule 27, 28, 29, 31 and 33 require transaction-specific legal review. Do not use this tool as a substitute for professional advice.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Admin CSV import
Set an administrator key in the environment before starting Streamlit:
```bash
# Linux/macOS
export GST_ADMIN_KEY='use-a-long-random-secret'
streamlit run app.py
```
For Streamlit Community Cloud, add `GST_ADMIN_KEY` under App settings > Secrets:
```toml
GST_ADMIN_KEY = "replace-with-a-long-random-secret"
```
Do not commit secrets to GitHub. Import CSV from the Home page administrator expander. Template fields:
`product_name, category, manufacturer, flavour, hsn, description, gst_status, gst_rate, cgst_rate, sgst_rate, igst_rate, cess_rate, source_name, source_url, notification_ref, effective_date, verified_date, verification_status, notes`

Allowed `gst_status`: `TAXABLE`, `EXEMPT`, `NIL_RATED`, `ZERO_RATED`, `NON_GST`, `UNVERIFIED`.
Allowed `verification_status`: `VERIFIED`, `PROVISIONAL`, `UNVERIFIED`.

## Google Colab
1. Open the generated `GST_FINDER_Colab_Builder.ipynb`.
2. Run the cells in order.
3. The notebook creates the four required project files and a ZIP.
4. For an interactive Colab preview, the notebook installs Streamlit and uses a tunnel. Do not expose private production data in a temporary public tunnel.

## GitHub + Streamlit Community Cloud
1. Create a private/public GitHub repository as appropriate.
2. Upload `app.py`, `requirements.txt`, `README.md`, `.gitignore`.
3. Open Streamlit Community Cloud and connect GitHub.
4. Select repository, branch and `app.py`, then deploy.
5. Add `GST_ADMIN_KEY` in Streamlit Secrets if controlled CSV imports are required.
6. Do not commit the SQLite database, secrets or personal data.

## Persistence warning
Streamlit Community Cloud local filesystems may be ephemeral across redeployments/restarts. SQLite is suitable for a prototype/single-instance trial, not a robust multi-user production database. For production, use managed PostgreSQL or another durable database, encrypted backups, tenant isolation, monitoring, access controls and a documented retention/deletion process.

## DPDP and security status
This code includes minimised account fields, password hashing, consent at registration, user-scoped history and deletion controls. It does **not** by itself establish DPDP Act/Rules compliance or production security. A legal and security review must determine applicable commencement provisions, notices, lawful basis/consent design, processor contracts, breach response, retention, rights handling, encryption, backup deletion and hosting controls. No document upload is enabled.

## Mandatory disclaimer
GST FINDER IS AN EDUCATIONAL TOOL, NOT AN OFFICIAL GOVERNMENT PLATFORM OR SUBSTITUTE FOR PROFESSIONAL TAX ADVICE. VERIFY ALL GST RATES, CLASSIFICATIONS AND VALUATION METHODS AGAINST CURRENT OFFICIAL NOTIFICATIONS BEFORE MAKING TAX OR BUSINESS DECISIONS.
