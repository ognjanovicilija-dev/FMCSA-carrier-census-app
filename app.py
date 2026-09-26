"""
FMCSA Carrier Census Explorer
=============================

Streamlit aplikacija koja povlači FMCSA Company Census podatke
(data.transportation.gov, dataset "az4n-8mr2") preko Socrata SODA API-ja,
filtrira ih po državi, statusu operatera i veličini flote
(owner-operatori sa jednim kamionom ili svi prevoznici), prikazuje tabelu
(Company, Phone, State, Power Units) i nudi preuzimanje u CSV formatu.

Pokretanje:
    pip install -r requirements.txt
    streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Konfiguracija
# ---------------------------------------------------------------------------

API_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"
PAGE_SIZE = 50_000  # maksimalan broj redova koji SODA vraća po jednom zahtevu
REQUEST_TIMEOUT = 90  # sekundi

# Kolone iz FMCSA dataseta koje povlačimo
SOURCE_COLUMNS = ["dot_number", "legal_name", "phone", "phy_state", "power_units"]

# status_code u datasetu: A = Active, I = Inactive, P = Pending
STATUS_OPTIONS = {
    "Active": "A",
    "Inactive": "I",
    "Pending": "P",
}

# Veličina flote: owner-operator = prevoznik sa tačno jednim kamionom (power_units = 1)
OWNER_OPERATORS = "Owner-operators (1 truck)"
ALL_CARRIERS = "All carriers"
FLEET_OPTIONS = [OWNER_OPERATORS, ALL_CARRIERS]

US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "PR": "Puerto Rico",
    "GU": "Guam",
    "VI": "U.S. Virgin Islands",
    "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}


# ---------------------------------------------------------------------------
# Pomoćne funkcije
# ---------------------------------------------------------------------------

def get_app_token() -> str | None:
    """
    Opcioni Socrata app token (veći limit API zahteva).
    Aplikacija radi i bez njega.
    """
    token = os.environ.get("SOCRATA_APP_TOKEN")
    if token:
        return token

    try:
        token = st.secrets.get("SOCRATA_APP_TOKEN")
    except Exception:
        # Nema secrets.toml fajla, što je sasvim u redu
        return None

    if token is None or str(token).strip() == "":
        return None
    return str(token)


def build_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = get_app_token()
    if token:
        headers["X-App-Token"] = token
    return headers


def build_where(
    state_codes: list[str], status_codes: list[str], owner_operators_only: bool
) -> str:
    """Pravi SoQL $where uslov. Vrednosti dolaze iz fiksnih lista, ne od korisnika."""
    clauses = ["phy_country = 'US'"]
    if owner_operators_only:
        # power_units je tekstualno polje u datasetu, zato poredimo sa '1'
        clauses.append("power_units = '1'")
    if state_codes:
        states_sql = ", ".join(f"'{code}'" for code in state_codes)
        clauses.append(f"phy_state in ({states_sql})")
    if status_codes:
        statuses_sql = ", ".join(f"'{code}'" for code in status_codes)
        clauses.append(f"status_code in ({statuses_sql})")
    return " AND ".join(clauses)


def format_phone(value) -> str:
    """Pretvara '2025551234' u '(202) 555-1234'; ostale vrednosti vraća kakve jesu."""
    if value is None or pd.isna(value):
        return ""
    raw = str(value).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw


def to_display_frame(records: list[dict]) -> pd.DataFrame:
    """Pretvara sirove API zapise u tabelu sa kolonama Company, Phone, State, Power Units."""
    raw = pd.DataFrame(records).reindex(columns=SOURCE_COLUMNS)

    table = pd.DataFrame(
        {
            "Company": raw["legal_name"].astype("object").fillna("").astype(str).str.strip(),
            "Phone": raw["phone"].map(format_phone).astype(str),
            "State": raw["phy_state"].astype("object").fillna("").astype(str),
            # power_units je u datasetu tekstualno polje, pa ga ovde pretvaramo u broj
            "Power Units": pd.to_numeric(raw["power_units"], errors="coerce").astype("Int64"),
        }
    )

    table = table.sort_values(
        by=["Power Units", "Company"],
        ascending=[False, True],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    return table


def build_file_name(
    state_codes: list[str], status_labels: list[str], owner_operators_only: bool
) -> str:
    fleet_part = "owner-operators" if owner_operators_only else "carriers"

    if not state_codes:
        states_part = "all-states"
    elif len(state_codes) <= 5:
        states_part = "-".join(state_codes)
    else:
        states_part = f"{len(state_codes)}-states"

    status_part = "-".join(label.lower() for label in status_labels) or "all-statuses"
    return f"fmcsa_{fleet_part}_{states_part}_{status_part}_{date.today():%Y%m%d}.csv"


# ---------------------------------------------------------------------------
# Povlačenje podataka (keširano 1h)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_total_count(where: str) -> int:
    """Ukupan broj kompanija koje odgovaraju filterima (bez limita)."""
    params = {"$select": "count(*) AS total", "$where": where}
    response = requests.get(
        API_URL, params=params, headers=build_headers(), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    data = response.json()
    if not data:
        return 0
    return int(data[0].get("total", 0))


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_carriers(where: str, max_rows: int) -> pd.DataFrame:
    """Povlači do max_rows zapisa, stranicu po stranicu, i vraća tabelu za prikaz."""
    records: list[dict] = []
    offset = 0

    with requests.Session() as session:
        session.headers.update(build_headers())

        while offset < max_rows:
            limit = min(PAGE_SIZE, max_rows - offset)
            params = {
                "$select": ", ".join(SOURCE_COLUMNS),
                "$where": where,
                "$order": "dot_number",  # stabilan redosled je bitan za stranično čitanje
                "$limit": limit,
                "$offset": offset,
            }
            response = session.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            batch = response.json()
            records.extend(batch)

            if len(batch) < limit:
                break
            offset += limit

    return to_display_frame(records)


def describe_request_error(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)

    if status == 429:
        return (
            "The FMCSA API is rate limiting requests. Wait a minute and try again, "
            "or add a SOCRATA_APP_TOKEN to raise the limit."
        )
    if status is not None and status >= 500:
        return f"The FMCSA API returned a server error ({status}). Try again in a few minutes."
    if status is not None:
        return f"The FMCSA API rejected the request ({status}). Check the filters and try again."
    if isinstance(exc, requests.Timeout):
        return "The FMCSA API took too long to respond. Lower 'Max rows to load' and try again."
    return "Couldn't reach the FMCSA API. Check your internet connection and try again."


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="FMCSA Carrier Census",
    page_icon="🚚",
    layout="wide",
)

with st.sidebar:
    st.header("Filters")

    with st.form("filters"):
        selected_states = st.multiselect(
            "State",
            options=list(US_STATES.keys()),
            format_func=lambda code: f"{code} – {US_STATES[code]}",
            placeholder="All states",
            help="Physical address state. Leave empty to include every US state.",
        )

        fleet_choice = st.radio(
            "Carrier type",
            options=FLEET_OPTIONS,
            index=0,
            help="Owner-operators run a single truck, usually one person driving under their own name or a small LLC.",
        )

        selected_status_labels = st.multiselect(
            "Operator status",
            options=list(STATUS_OPTIONS.keys()),
            default=["Active"],
            placeholder="All statuses",
            help="USDOT registration status: Active, Inactive or Pending.",
        )

        max_rows = st.number_input(
            "Max rows to load",
            min_value=100,
            max_value=500_000,
            value=10_000,
            step=1_000,
            help="Large states like CA or TX have hundreds of thousands of records.",
        )

        st.form_submit_button("Apply filters", type="primary", width="stretch")

    st.caption(
        "Data: FMCSA Company Census File, published on data.transportation.gov. "
        "Results are cached for one hour."
    )

owner_operators_only = fleet_choice == OWNER_OPERATORS

if owner_operators_only:
    st.title("FMCSA owner-operators")
    st.write("One-truck carriers registered with FMCSA, filtered by state and operator status.")
else:
    st.title("FMCSA carrier census")
    st.write("US motor carriers registered with FMCSA, filtered by state and operator status.")

status_codes = [STATUS_OPTIONS[label] for label in selected_status_labels]
where_clause = build_where(selected_states, status_codes, owner_operators_only)

try:
    with st.spinner("Loading carriers from FMCSA…"):
        total_matching = fetch_total_count(where_clause)
        carriers = fetch_carriers(where_clause, int(max_rows))
except requests.RequestException as error:
    st.error(describe_request_error(error))
    st.stop()

if carriers.empty:
    st.warning("No carriers match these filters. Pick another state or status in the sidebar.")
    st.stop()

loaded_rows = len(carriers)
states_in_results = carriers["State"].replace("", pd.NA).nunique()

col_total, col_loaded, col_states = st.columns(3)
col_total.metric("Matching carriers", f"{total_matching:,}")
col_loaded.metric("Loaded in table", f"{loaded_rows:,}")
col_states.metric("States in results", f"{states_in_results:,}")

if total_matching > loaded_rows:
    st.info(
        f"Showing {loaded_rows:,} of {total_matching:,} matching carriers. "
        "Raise 'Max rows to load' in the sidebar to load more."
    )

csv_bytes = carriers.to_csv(index=False).encode("utf-8-sig")  # BOM da Excel pravilno čita UTF-8
st.download_button(
    label="Download CSV",
    data=csv_bytes,
    file_name=build_file_name(selected_states, selected_status_labels, owner_operators_only),
    mime="text/csv",
    type="primary",
    icon=":material/download:",
    on_click="ignore",  # preuzimanje ne pokreće ponovno izvršavanje aplikacije
)

st.dataframe(
    carriers,
    width="stretch",
    height=620,
    hide_index=True,
    column_config={
        "Company": st.column_config.TextColumn("Company", width="large"),
        "Phone": st.column_config.TextColumn("Phone", width="medium"),
        "State": st.column_config.TextColumn("State", width="small"),
        "Power Units": st.column_config.NumberColumn("Power Units", format="%d", width="small"),
    },
)
