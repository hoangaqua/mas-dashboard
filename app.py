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

META_TOKEN = st.secrets.get("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT = st.secrets.get("META_AD_ACCOUNT_ID", "")
META_BASE = "https://graph.facebook.com/v19.0"

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
.section-meta { background: linear-gradient(135deg, #1877f2 0%, #0d5fb8 100%); border-radius: 12px; padding: 4px 16px; margin-bottom: 8px; }
.section-meta h3 { color: white; margin: 8px 0; }
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

@st.cache_data(ttl=900)
def fetch_meta_ads(days=30):
    """Fetch Meta Ads insights for the given period."""
    if not META_TOKEN or not META_AD_ACCOUNT:
        return None, None

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    url = f"{META_BASE}/{META_AD_ACCOUNT}/insights"
    params = {
        "fields": "spend,impressions,clicks,cpc,cpm,reach,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "access_token": META_TOKEN,
    }
    r = requests.get(url, params=params, timeout=30)
    data = r.json()
    if "error" in data or not data.get("data"):
        return None, None

    summary = data["data"][0] if data["data"] else {}

    camp_url = f"{META_BASE}/{META_AD_ACCOUNT}/campaigns"
    camp_params = {
        "fields": "name,status,insights.time_range(" + json.dumps({"since": since, "until": until}) + "){spend,impressions,clicks,cpc}",
        "access_token": META_TOKEN,
        "limit": 20,
    }
    cr = requests.get(camp_url, params=camp_params, timeout=30)
    camps_data = cr.json().get("data", [])

    campaigns = []
    for c in camps_data:
        ins = c.get("insights", {}).get("data", [{}])
        if ins:
            i = ins[0]
            campaigns.append({
                "campaign": c["name"],
                "status": c.get("status", "UNKNOWN"),
                "spend": float(i.get("spend", 0)),
                "impressions": int(i.get("impressions", 0)),
                "clicks": int(i.get("clicks", 0)),
                "cpc": float(i.get("cpc", 0)),
            })

    return summary, campaigns

@st.cache_data(ttl=900)
def fetch_meta_daily(days=30):
    if not META_TOKEN or not META_AD_ACCOUNT:
        return None

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    until = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    url = f"{META_BASE}/{META_AD_ACCOUNT}/insights"
    params = {
        "fields": "spend,impressions,clicks",
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": 1,
        "access_token": META_TOKEN,
    }
    r = requests.get(url, params=params, timeout=30)
    data = r.json().get("data", [])
    if not data:
        return None

    rows = []
    for d in data:
        rows.append({
            "date": pd.to_datetime(d["date_start"]).date(),
            "spend": float(d.get("spend", 0)),
            "impressions": int(d.get("impressions", 0)),
            "clicks": int(d.get("clicks", 0)),
        })
    return pd.DataFrame(rows)

# -- MAIN --
if not STORE or not TOKEN:
    st.error("Shopify credentials not configured.")
    st.stop()

with st.sidebar:
    st.title("Controls")
    days = st.selectbox("Period", [7, 14, 30, 60, 90], index=2, format_func=lambda x: f"Last {x} days")
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

with st.spinner("Loading data..."):
    try:
        raw_orders = fetch_orders(days)
        shop = fetch_shop_info()
        meta_summary, meta_campaigns = fetch_meta_ads(days)
        meta_daily = fetch_meta_daily(days)
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        st.stop()

df = pd.DataFrame(raw_orders) if raw_orders else pd.DataFrame()
st.title(f"MAS — {shop['name']} Dashboard")
st.caption(f"Currency: {shop['currencyCode']} · Last {days} days · {len(df)} orders · refreshes every 15 min")

if df.empty:
    st.info("No orders found for this period.")
    st.stop()

df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
prev_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days * 2)
df_cur = df[df["created_at"] >= cutoff]
df_prev = df[(df["created_at"] >= prev_cutoff) & (df["created_at"] < cutoff)]

def delta_pct(cur, prev):
    if prev == 0: return None
    return (cur - prev) / prev * 100

def fmt_delta(pct):
    if pct is None: return ""
    sign = "+" if pct >= 0 else ""
    cls = "delta-up" if pct >= 0 else "delta-down"
    return f'<span class="{cls}">{sign}{pct:.1f}%</span>'

revenue_cur = df_cur["total"].sum()
revenue_prev = df_prev["total"].sum()
orders_cur = len(df_cur)
orders_prev = len(df_prev)
aov_cur = revenue_cur / orders_cur if orders_cur else 0
aov_prev = revenue_prev / orders_prev if orders_prev else 0
discounts_cur = df_cur["discounts"].sum()
discounts_prev = df_prev["discounts"].sum()
cur = df_cur["currency"].iloc[0] if not df_cur.empty else "AUD"

st.markdown("### 🛒 Shopify Sales")
k1, k2, k3, k4 = st.columns(4)

def kpi_card(col, label, value, pct):
    col.markdown(f'<div class="card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-delta">{fmt_delta(pct)}</div></div>', unsafe_allow_html=True)

kpi_card(k1, "Revenue", f"{cur} {revenue_cur:,.2f}", delta_pct(revenue_cur, revenue_prev))
kpi_card(k2, "Orders", f"{orders_cur:,}", delta_pct(orders_cur, orders_prev))
kpi_card(k3, "Avg Order Value", f"{cur} {aov_cur:,.2f}", delta_pct(aov_cur, aov_prev))
kpi_card(k4, "Discounts Given", f"{cur} {discounts_cur:,.2f}", delta_pct(discounts_cur, discounts_prev))

st.markdown("### 📘 Meta Ads Performance")
if meta_summary:
    ad_spend = float(meta_summary.get("spend", 0))
    ad_impressions = int(meta_summary.get("impressions", 0))
    ad_clicks = int(meta_summary.get("clicks", 0))
    ad_cpc = float(meta_summary.get("cpc", 0))
    ad_cpm = float(meta_summary.get("cpm", 0))
    ad_reach = int(meta_summary.get("reach", 0))
    roas = revenue_cur / ad_spend if ad_spend > 0 else 0
    cac = ad_spend / orders_cur if orders_cur > 0 else 0
    ctr = (ad_clicks / ad_impressions * 100) if ad_impressions > 0 else 0

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    def meta_card(col, label, value, sub=""):
        col.markdown(f'<div class="card" style="border-left:4px solid #1877f2"><div class="metric-label">{label}</div><div class="metric-value" style="font-size:1.5rem">{value}</div><div class="metric-delta" style="color:#888">{sub}</div></div>', unsafe_allow_html=True)

    meta_card(m1, "Ad Spend", f"${ad_spend:,.0f}", f"{days}d total")
    meta_card(m2, "ROAS", f"{roas:.2f}x", "Revenue / Spend")
    meta_card(m3, "CAC", f"${cac:.2f}", "Cost per Order")
    meta_card(m4, "CPC", f"${ad_cpc:.3f}", f"{ad_clicks:,} clicks")
    meta_card(m5, "CPM", f"${ad_cpm:.2f}", f"{ad_impressions/1000:,.0f}K impr.")
    meta_card(m6, "CTR", f"{ctr:.2f}%", f"{ad_reach:,} reach")

    if meta_daily is not None and not meta_daily.empty:
        df_cur2 = df_cur.copy()
        df_cur2["date"] = df_cur2["created_at"].dt.date
        daily_rev2 = df_cur2.groupby("date")["total"].sum().reset_index()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=meta_daily["date"], y=meta_daily["spend"], name="Ad Spend ($)", marker_color="#1877f2", opacity=0.8))
        fig.add_trace(go.Scatter(x=daily_rev2["date"], y=daily_rev2["total"], name=f"Revenue ({cur})", yaxis="y2", line=dict(color="#22c55e", width=2), mode="lines+markers"))
        fig.update_layout(title="Daily Ad Spend vs Revenue", yaxis=dict(title="Ad Spend (USD)"), yaxis2=dict(title=f"Revenue ({cur})", overlaying="y", side="right"), legend=dict(orientation="h", y=1.1), height=300, margin=dict(t=50, b=20))
        st.plotly_chart(fig, use_container_width=True)

    if meta_campaigns:
        camp_df = pd.DataFrame(meta_campaigns).sort_values("spend", ascending=False)
        camp_df = camp_df[camp_df["spend"] > 0]
        if not camp_df.empty:
            fig = px.bar(camp_df, x="spend", y="campaign", orientation="h", color="cpc", color_continuous_scale="Blues", labels={"spend": "Spend (USD)", "campaign": "", "cpc": "CPC"}, title="Spend by Campaign", hover_data=["impressions", "clicks", "cpc"])
            fig.update_layout(height=max(200, len(camp_df)*45+60), margin=dict(t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Meta Ads data unavailable. Check META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in secrets.")

st.markdown("### 📈 Trends")
df_cur["date"] = df_cur["created_at"].dt.date
daily_rev = df_cur.groupby("date")["total"].sum().reset_index()
daily_ord = df_cur.groupby("date").size().reset_index(name="count")
c1, c2 = st.columns(2)
with c1:
    fig = px.area(daily_rev, x="date", y="total", title="Daily Revenue", labels={"date": "", "total": f"Revenue ({cur})"}, color_discrete_sequence=["#6366f1"])
    fig.update_layout(margin=dict(t=40, b=20), height=280)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    fig = px.bar(daily_ord, x="date", y="count", title="Daily Orders", labels={"date": "", "count": "Orders"}, color_discrete_sequence=["#22c55e"])
    fig.update_layout(margin=dict(t=40, b=20), height=280)
    st.plotly_chart(fig, use_container_width=True)

st.markdown("### 🐟 Top Products")
rows = []
for _, row in df_cur.iterrows():
    for li in row["line_items"]:
        rows.append({"product": li["title"], "qty": li["qty"], "revenue": li["qty"]*li["price"]})
if rows:
    prod_df = pd.DataFrame(rows).groupby("product").agg(qty=("qty","sum"), revenue=("revenue","sum")).reset_index()
    prod_df = prod_df.sort_values("revenue", ascending=False).head(15)
    fig = px.bar(prod_df, x="revenue", y="product", orientation="h", title="Top 15 Products by Revenue", labels={"revenue": f"Revenue ({cur})", "product": ""}, color="revenue", color_continuous_scale="Blues")
    fig.update_layout(margin=dict(t=40, b=20), height=420, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)

st.markdown("### 📋 Order Status")
s1, s2 = st.columns(2)
with s1:
    pc = df_cur["financial_status"].value_counts().reset_index(); pc.columns=["status","count"]
    fig = px.pie(pc, names="status", values="count", title="Payment Status", color_discrete_sequence=px.colors.qualitative.Set3)
    fig.update_layout(margin=dict(t=40, b=20), height=300); st.plotly_chart(fig, use_container_width=True)
with s2:
    fc = df_cur["fulfillment_status"].value_counts().reset_index(); fc.columns=["status","count"]
    fig = px.pie(fc, names="status", values="count", title="Fulfillment Status", color_discrete_sequence=px.colors.qualitative.Pastel)
    fig.update_layout(margin=dict(t=40, b=20), height=300); st.plotly_chart(fig, use_container_width=True)

st.markdown("### 🧾 Recent Orders")
recent = df_cur.sort_values("created_at", ascending=False).head(20).copy()
recent["created_at"] = recent["created_at"].dt.tz_convert("Australia/Sydney").dt.strftime("%d %b %Y %H:%M")
st.dataframe(recent[["name","created_at","total","financial_status","fulfillment_status"]].rename(columns={"name":"Order","created_at":"Date (AEST)","total":f"Total ({cur})","financial_status":"Payment","fulfillment_status":"Fulfillment"}), use_container_width=True, hide_index=True)
