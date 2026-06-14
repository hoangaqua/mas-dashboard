import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
import json

# -- CONFIG --
STORE = st.secrets.get("SHOPIFY_STORE", "")
TOKEN = st.secrets.get("SHOPIFY_ACCESS_TOKEN", "")
API_VER = "2024-10"
GQL_URL = f"https://{STORE}/admin/api/{API_VER}/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

st.set_page_config(
    page_title="MAS Dashboard",
    page_icon="🐠",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -- CUSTOM CSS --
st.markdown("""
<style>
.block-container { padding: 1rem 2rem; max-width: 1400px; }
.card {
    background: white; border-radius: 12px; padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin-bottom: 12px;
}
.metric-value { font-size: 2rem; font-weight: 700; color: #1a1a2e; }
.metric-label { font-size: 0.8rem; color: #666; text-transform: uppercase; letter-spacing: .05em; }
.metric-delta { font-size: 0.85rem; margin-top: 2px; }
.delta-up { color: #22c55e; }
.delta-down { color: #ef4444; }
</style>
""", unsafe_allow_html=True)

# -- HELPERS --
def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]

@st.cache_data(ttl=900)
def fetch_orders(days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = """
    query($cursor: String, $query: String!) {
      orders(first: 250, after: $cursor, query: $query, sortKey: CREATED_AT) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id name createdAt
            totalPriceSet { shopMoney { amount currencyCode } }
            subtotalPriceSet { shopMoney { amount } }
            totalDiscountsSet { shopMoney { amount } }
            displayFinancialStatus
            displayFulfillmentStatus
            lineItems(first: 50) {
              edges {
                node {
                  title quantity
                  originalUnitPriceSet { shopMoney { amount } }
                }
              }
            }
          }
        }
      }
    }
    """
    orders = []
    cursor = None
    while True:
        data = gql(query, {"cursor": cursor, "query": f"created_at:>={since}"})
        edges = data["orders"]["edges"]
        for e in edges:
            n = e["node"]
            orders.append({
                "id": n["id"],
                "name": n["name"],
                "created_at": pd.Timestamp(n["createdAt"]),
                "total": float(n["totalPriceSet"]["shopMoney"]["amount"]),
                "subtotal": float(n["subtotalPriceSet"]["shopMoney"]["amount"]),
                "discounts": float(n["totalDiscountsSet"]["shopMoney"]["amount"]),
                "currency": n["totalPriceSet"]["shopMoney"]["currencyCode"],
                "financial_status": n["displayFinancialStatus"],
                "fulfillment_status": n["displayFulfillmentStatus"],
                "line_items": [
                    {
                        "title": li["node"]["title"],
                        "qty": li["node"]["quantity"],
                        "price": float(li["node"]["originalUnitPriceSet"]["shopMoney"]["amount"]),
                    }
                    for li in n["lineItems"]["edges"]
                ],
            })
        if not data["orders"]["pageInfo"]["hasNextPage"]:
            break
        cursor = data["orders"]["pageInfo"]["endCursor"]
    return orders

@st.cache_data(ttl=900)
def fetch_shop_info():
    data = gql("{ shop { name currencyCode } }")
    return data["shop"]

# -- MAIN --
if not STORE or not TOKEN:
    st.error("Shopify credentials not configured. Add SHOPIFY_STORE and SHOPIFY_ACCESS_TOKEN to Streamlit secrets.")
    st.stop()

with st.sidebar:
    st.title("Controls")
    days = st.selectbox("Period", [7, 14, 30, 60, 90], index=2, format_func=lambda x: f"Last {x} days")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

with st.spinner("Loading orders from Shopify..."):
    try:
        raw_orders = fetch_orders(days)
        shop = fetch_shop_info()
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        st.stop()

df = pd.DataFrame(raw_orders) if raw_orders else pd.DataFrame()

st.title(f"MAS - {shop['name']} Sales Dashboard")
st.caption(f"Currency: {shop['currencyCode']} - Last {days} days - {len(df)} orders - refreshes every 15 min")

if df.empty:
    st.info("No orders found for this period.")
    st.stop()

# Ensure timezone-aware timestamps
df["created_at"] = pd.to_datetime(df["created_at"], utc=True)

# Period comparison using timezone-aware timestamps
cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
prev_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days * 2)

df_cur = df[df["created_at"] >= cutoff]
df_prev = df[(df["created_at"] >= prev_cutoff) & (df["created_at"] < cutoff)]

def delta_pct(cur, prev):
    if prev == 0:
        return None
    return (cur - prev) / prev * 100

def fmt_delta(pct):
    if pct is None:
        return ""
    arrow = "up" if pct >= 0 else "down"
    sign = "+" if pct >= 0 else ""
    cls = "delta-up" if pct >= 0 else "delta-down"
    return f'<span class="{cls}">{sign}{pct:.1f}%</span>'

# KPI calculations
revenue_cur = df_cur["total"].sum()
revenue_prev = df_prev["total"].sum()
orders_cur = len(df_cur)
orders_prev = len(df_prev)
aov_cur = revenue_cur / orders_cur if orders_cur else 0
aov_prev = revenue_prev / orders_prev if orders_prev else 0
discounts_cur = df_cur["discounts"].sum()
discounts_prev = df_prev["discounts"].sum()
cur = df_cur["currency"].iloc[0] if not df_cur.empty else "AUD"

# -- KPI CARDS --
st.markdown("### Key Metrics")
k1, k2, k3, k4 = st.columns(4)

def kpi_card(col, label, value, pct):
    col.markdown(f"""
    <div class="card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-delta">{fmt_delta(pct)}</div>
    </div>
    """, unsafe_allow_html=True)

kpi_card(k1, "Revenue", f"{cur} {revenue_cur:,.2f}", delta_pct(revenue_cur, revenue_prev))
kpi_card(k2, "Orders", f"{orders_cur:,}", delta_pct(orders_cur, orders_prev))
kpi_card(k3, "Avg Order Value", f"{cur} {aov_cur:,.2f}", delta_pct(aov_cur, aov_prev))
kpi_card(k4, "Discounts Given", f"{cur} {discounts_cur:,.2f}", delta_pct(discounts_cur, discounts_prev))

# -- CHARTS --
st.markdown("### Trends")
df_cur["date"] = df_cur["created_at"].dt.date
daily_rev = df_cur.groupby("date")["total"].sum().reset_index()
daily_ord = df_cur.groupby("date").size().reset_index(name="count")

c1, c2 = st.columns(2)
with c1:
    fig = px.area(daily_rev, x="date", y="total",
                  title="Daily Revenue",
                  labels={"date": "", "total": f"Revenue ({cur})"},
                  color_discrete_sequence=["#6366f1"])
    fig.update_layout(margin=dict(t=40, b=20), height=280)
    st.plotly_chart(fig, use_container_width=True)

with c2:
    fig = px.bar(daily_ord, x="date", y="count",
                 title="Daily Orders",
                 labels={"date": "", "count": "Orders"},
                 color_discrete_sequence=["#22c55e"])
    fig.update_layout(margin=dict(t=40, b=20), height=280)
    st.plotly_chart(fig, use_container_width=True)

# -- TOP PRODUCTS --
st.markdown("### Top Products")
rows = []
for _, row in df_cur.iterrows():
    for li in row["line_items"]:
        rows.append({"product": li["title"], "qty": li["qty"], "revenue": li["qty"] * li["price"]})

if rows:
    prod_df = pd.DataFrame(rows).groupby("product").agg(qty=("qty","sum"), revenue=("revenue","sum")).reset_index()
    prod_df = prod_df.sort_values("revenue", ascending=False).head(15)
    fig = px.bar(prod_df, x="revenue", y="product", orientation="h",
                 title="Top 15 Products by Revenue",
                 labels={"revenue": f"Revenue ({cur})", "product": ""},
                 color="revenue", color_continuous_scale="Blues")
    fig.update_layout(margin=dict(t=40, b=20), height=420, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No line item data available.")

# -- STATUS CHARTS --
st.markdown("### Order Status")
s1, s2 = st.columns(2)

with s1:
    pay_counts = df_cur["financial_status"].value_counts().reset_index()
    pay_counts.columns = ["status", "count"]
    fig = px.pie(pay_counts, names="status", values="count", title="Payment Status",
                 color_discrete_sequence=px.colors.qualitative.Set3)
    fig.update_layout(margin=dict(t=40, b=20), height=300)
    st.plotly_chart(fig, use_container_width=True)

with s2:
    ful_counts = df_cur["fulfillment_status"].value_counts().reset_index()
    ful_counts.columns = ["status", "count"]
    fig = px.pie(ful_counts, names="status", values="count", title="Fulfillment Status",
                 color_discrete_sequence=px.colors.qualitative.Pastel)
    fig.update_layout(margin=dict(t=40, b=20), height=300)
    st.plotly_chart(fig, use_container_width=True)

# -- RECENT ORDERS --
st.markdown("### Recent Orders")
recent = df_cur.sort_values("created_at", ascending=False).head(20).copy()
recent["created_at"] = recent["created_at"].dt.tz_convert("Australia/Sydney").dt.strftime("%d %b %Y %H:%M")
st.dataframe(
    recent[["name","created_at","total","financial_status","fulfillment_status"]].rename(columns={
        "name": "Order",
        "created_at": "Date (AEST)",
        "total": f"Total ({cur})",
        "financial_status": "Payment",
        "fulfillment_status": "Fulfillment"
    }),
    use_container_width=True,
    hide_index=True
)
