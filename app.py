import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json

# —— PAGE CONFIG ————————————————————————————————
st.set_page_config(
    page_title="MAS Dashboard",
    page_icon="🐟",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# —— CUSTOM CSS ————————————————————————————————
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

# —— CREDENTIALS ————————————————————————————————
STORE = st.secrets.get("SHOPIFY_STORE", "")
TOKEN = st.secrets.get("SHOPIFY_ACCESS_TOKEN", "")
API_VER = "2024-10"
GQL_URL = f"https://{STORE}/admin/api/{API_VER}/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

if not TOKEN:
    st.error("⚠️ No SHOPIFY_ACCESS_TOKEN found in Streamlit secrets.")
    st.stop()

# —— DATA FETCHING ————————————————————————————————
def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        return None, data["errors"]
    return data.get("data"), None

@st.cache_data(ttl=900)
def fetch_orders(days=30):
    """Fetch orders from the last N days using cursor-based pagination."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    orders = []
    cursor = None
    
    QUERY = """
    query GetOrders($cursor: String, $since: String!) {
      orders(first: 250, after: $cursor, query: $since, sortKey: CREATED_AT) {
        edges {
          cursor
          node {
            id
            name
            createdAt
            totalPriceSet { shopMoney { amount currencyCode } }
            subtotalPriceSet { shopMoney { amount } }
            totalDiscountsSet { shopMoney { amount } }
            displayFinancialStatus
            displayFulfillmentStatus
            lineItems(first: 50) {
              edges {
                node {
                  name
                  quantity
                  originalUnitPriceSet { shopMoney { amount } }
                }
              }
            }
          }
        }
        pageInfo { hasNextPage }
      }
    }
    """
    
    while True:
        variables = {"cursor": cursor, "since": f"created_at:>={since}"}
        data, err = gql(QUERY, variables)
        if err or not data:
            break
        edges = data["orders"]["edges"]
        for edge in edges:
            node = edge["node"]
            orders.append({
                "id": node["id"],
                "name": node["name"],
                "created_at": node["createdAt"],
                "total": float(node["totalPriceSet"]["shopMoney"]["amount"]),
                "subtotal": float(node["subtotalPriceSet"]["shopMoney"]["amount"]),
                "discounts": float(node["totalDiscountsSet"]["shopMoney"]["amount"]),
                "currency": node["totalPriceSet"]["shopMoney"]["currencyCode"],
                "financial_status": node["displayFinancialStatus"],
                "fulfillment_status": node["displayFulfillmentStatus"],
                "line_items": [
                    {
                        "name": li["node"]["name"],
                        "qty": li["node"]["quantity"],
                        "price": float(li["node"]["originalUnitPriceSet"]["shopMoney"]["amount"])
                    }
                    for li in node["lineItems"]["edges"]
                ]
            })
        if not data["orders"]["pageInfo"]["hasNextPage"]:
            break
        cursor = edges[-1]["cursor"] if edges else None
        if not cursor:
            break
    
    return orders

@st.cache_data(ttl=900)
def fetch_shop_info():
    Q = """{ shop { name currencyCode email myshopifyDomain plan { displayName } } }"""
    data, _ = gql(Q)
    return data.get("shop", {}) if data else {}

# —— HEADER ————————————————————————————————
shop = fetch_shop_info()
shop_name = shop.get("name", "Micro Aquatic Shop")
currency = shop.get("currencyCode", "AUD")

col_title, col_period = st.columns([3, 1])
with col_title:
    st.title(f"🐟 {shop_name} — Performance Dashboard")
    st.caption(f"microaquaticshop.com.au · {currency} · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
with col_period:
    period = st.selectbox("", ["7 ngày qua", "30 ngày qua", "60 ngày qua", "90 ngày qua"],
                          index=1, label_visibility="collapsed")

days_map = {"7 ngày qua": 7, "30 ngày qua": 30, "60 ngày qua": 60, "90 ngày qua": 90}
days = days_map[period]

# —— LOAD DATA ————————————————————————————————
with st.spinner("Đang tải dữ liệu từ Shopify..."):
    orders = fetch_orders(days)
    compare_orders = fetch_orders(days * 2)  # double period for comparison

if not orders:
    st.warning("Không có đơn hàng nào trong khoảng thời gian này.")
    st.stop()

# —— PROCESS DATA ————————————————————————————————
df = pd.DataFrame(orders)
df["created_at"] = pd.to_datetime(df["created_at"])
df["date"] = df["created_at"].dt.date
df["date"] = pd.to_datetime(df["date"])

# Current period
cutoff = datetime.utcnow() - timedelta(days=days)
df_cur = df[df["created_at"] >= cutoff]

# Previous period
prev_cutoff = datetime.utcnow() - timedelta(days=days * 2)
df_all = pd.DataFrame(compare_orders)
if not df_all.empty:
    df_all["created_at"] = pd.to_datetime(df_all["created_at"])
    df_prev = df_all[(df_all["created_at"] >= prev_cutoff) & (df_all["created_at"] < cutoff)]
else:
    df_prev = pd.DataFrame()

def pct_delta(cur, prev):
    if prev == 0: return 0
    return ((cur - prev) / prev) * 100

# KPIs
total_rev = df_cur["total"].sum()
prev_rev = df_prev["total"].sum() if not df_prev.empty else 0
total_orders = len(df_cur)
prev_orders = len(df_prev)
aov = total_rev / total_orders if total_orders else 0
prev_aov = prev_rev / prev_orders if prev_orders else 0
total_discounts = df_cur["discounts"].sum()

# —— KPI CARDS ————————————————————————————————
st.markdown("---")
k1, k2, k3, k4 = st.columns(4)

def kpi_card(col, label, value, delta_pct, prefix="$", suffix=""):
    arrow = "▲" if delta_pct >= 0 else "▼"
    cls = "delta-up" if delta_pct >= 0 else "delta-down"
    col.markdown(f"""
    <div class="card">
      <div class="metric-label">{label}</div>
      <div class="metric-value">{prefix}{value:,.0f}{suffix}</div>
      <div class="metric-delta {cls}">{arrow} {abs(delta_pct):.1f}% vs kỳ trước</div>
    </div>
    """, unsafe_allow_html=True)

kpi_card(k1, "Doanh Thu", total_rev, pct_delta(total_rev, prev_rev), f"{currency} ")
kpi_card(k2, "Đơn Hàng", total_orders, pct_delta(total_orders, prev_orders), "")
kpi_card(k3, "AOV (Giá trị TB)", aov, pct_delta(aov, prev_aov), f"{currency} ")
kpi_card(k4, "Tổng Giảm Giá", total_discounts, 0, f"{currency} ")

# —— REVENUE CHART ————————————————————————————————
st.markdown("#### 📈 Doanh Thu Theo Ngày")
daily = df_cur.groupby("date")["total"].sum().reset_index()
daily.columns = ["Ngày", "Doanh thu"]
fig_rev = px.area(daily, x="Ngày", y="Doanh thu",
                  color_discrete_sequence=["#6366f1"],
                  labels={"Doanh thu": f"Doanh thu ({currency})"},
                  template="plotly_white")
fig_rev.update_traces(fill="tozeroy", fillcolor="rgba(99,102,241,0.1)")
fig_rev.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0))
st.plotly_chart(fig_rev, use_container_width=True)

# —— ORDERS & TOP PRODUCTS ————————————————————————————————
col_l, col_r = st.columns(2)

with col_l:
    st.markdown("#### 📦 Đơn Hàng Theo Ngày")
    daily_orders = df_cur.groupby("date")["id"].count().reset_index()
    daily_orders.columns = ["Ngày", "Số đơn"]
    fig_ord = px.bar(daily_orders, x="Ngày", y="Số đơn",
                     color_discrete_sequence=["#f59e0b"],
                     template="plotly_white")
    fig_ord.update_layout(height=280, margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig_ord, use_container_width=True)

with col_r:
    st.markdown("#### 🏆 Top Sản Phẩm")
    all_items = []
    for _, row in df_cur.iterrows():
        for item in row["line_items"]:
            all_items.append({
                "product": item["name"],
                "qty": item["qty"],
                "revenue": item["qty"] * item["price"]
            })
    if all_items:
        items_df = pd.DataFrame(all_items)
        top_products = items_df.groupby("product").agg(
            qty=("qty", "sum"), revenue=("revenue", "sum")
        ).sort_values("revenue", ascending=False).head(10).reset_index()
        fig_top = px.bar(top_products, x="revenue", y="product",
                         orientation="h",
                         color_discrete_sequence=["#10b981"],
                         labels={"revenue": f"Doanh thu ({currency})", "product": ""},
                         template="plotly_white")
        fig_top.update_layout(height=280, margin=dict(l=0, r=0, t=20, b=0),
                               yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_top, use_container_width=True)
    else:
        st.info("Không có dữ liệu sản phẩm")

# —— ORDER STATUS ————————————————————————————————
col_s1, col_s2 = st.columns(2)

with col_s1:
    st.markdown("#### 💳 Trạng Thái Thanh Toán")
    fin_status = df_cur["financial_status"].value_counts().reset_index()
    fin_status.columns = ["Trạng thái", "Số đơn"]
    fig_fin = px.pie(fin_status, names="Trạng thái", values="Số đơn",
                     color_discrete_sequence=px.colors.qualitative.Set2,
                     template="plotly_white")
    fig_fin.update_layout(height=260, margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig_fin, use_container_width=True)

with col_s2:
    st.markdown("#### 🚚 Trạng Thái Giao Hàng")
    ful_status = df_cur["fulfillment_status"].value_counts().reset_index()
    ful_status.columns = ["Trạng thái", "Số đơn"]
    fig_ful = px.pie(ful_status, names="Trạng thái", values="Số đơn",
                     color_discrete_sequence=px.colors.qualitative.Pastel,
                     template="plotly_white")
    fig_ful.update_layout(height=260, margin=dict(l=0, r=0, t=20, b=0))
    st.plotly_chart(fig_ful, use_container_width=True)

# —— RECENT ORDERS ————————————————————————————————
st.markdown("#### 🧾 Đơn Hàng Gần Nhất")
recent = df_cur.sort_values("created_at", ascending=False).head(20)[[
    "name", "created_at", "total", "financial_status", "fulfillment_status"
]].copy()
recent["created_at"] = recent["created_at"].dt.strftime("%d/%m/%Y %H:%M")
recent.columns = ["Mã đơn", "Ngày tạo", f"Tổng ({currency})", "Thanh toán", "Giao hàng"]
st.dataframe(recent, use_container_width=True, hide_index=True)

st.caption("🔄 Dữ liệu cache 15 phút · Nguồn: Shopify Admin API · MAS Dashboard v2")
