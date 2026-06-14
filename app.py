import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import json

# ─── PAGE CONFIG ──────────────────────────────────────────────
st.set_page_config(
    page_title="MAS Dashboard",
    page_icon="🐟",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ─── CUSTOM CSS ───────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding: 1.5rem 2rem; }
  .metric-card {
    background: white; border-radius: 12px; padding: 16px 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-top: 3px solid #3182ce; margin-bottom: 4px;
  }
  .metric-label { font-size:11px; font-weight:700; color:#718096; text-transform:uppercase; letter-spacing:0.05em; }
  .metric-value { font-size:26px; font-weight:800; color:#1a202c; margin:4px 0; }
  .metric-sub   { font-size:12px; color:#718096; }
  .insight-good   { background:#f0fff4; border-left:4px solid #38a169; border-radius:8px; padding:10px 14px; margin:6px 0; }
  .insight-warn   { background:#fffff0; border-left:4px solid #d69e2e; border-radius:8px; padding:10px 14px; margin:6px 0; }
  .insight-danger { background:#fff5f5; border-left:4px solid #e53e3e; border-radius:8px; padding:10px 14px; margin:6px 0; }
  .insight-title  { font-size:13px; font-weight:700; margin-bottom:4px; }
  .insight-text   { font-size:12px; color:#4a5568; line-height:1.5; }
  .section-header { font-size:14px; font-weight:700; color:#2d3748; margin:16px 0 8px; border-bottom:2px solid #edf2f7; padding-bottom:6px; }
</style>
""", unsafe_allow_html=True)

# ─── SHOPIFY API ───────────────────────────────────────────────
STORE   = st.secrets.get("SHOPIFY_STORE", "helloofish.myshopify.com")
TOKEN   = st.secrets.get("SHOPIFY_ACCESS_TOKEN", "")
API_VER = "2024-10"
GQL_URL = f"https://{STORE}/admin/api/{API_VER}/graphql.json"
HEADERS = {"X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json"}

def gql(query: str, variables: dict = None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GQL_URL, headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        st.error(f"API error: {data['errors']}")
        return None
    return data.get("data")

@st.cache_data(ttl=900)
def fetch_analytics(shopify_query: str):
    q = """
    mutation analyticsQuery($query: String!) {
      analyticsQuery(query: $query) {
        data
        parseErrors { code message field }
      }
    }
    """
    data = gql(q, {"query": shopify_query})
    if not data:
        return None
    result = data.get("analyticsQuery", {})
    if result.get("parseErrors"):
        return None
    raw = result.get("data", "{}")
    return json.loads(raw) if isinstance(raw, str) else raw

def to_df(raw) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame()
    cols = [c["name"] for c in raw.get("schema", [])]
    rows = raw.get("data", [])
    if not cols or not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=cols)

def safe_sum(df, col):
    try: return pd.to_numeric(df[col], errors="coerce").sum()
    except: return 0

def safe_mean(df, col):
    try: return pd.to_numeric(df[col], errors="coerce").mean()
    except: return 0

# ─── GUARD ────────────────────────────────────────────────────
if not TOKEN:
    st.error("⚠️ Chưa có Shopify Access Token.")
    st.code("""# .streamlit/secrets.toml\nSHOPIFY_STORE = "helloofish.myshopify.com"\nSHOPIFY_ACCESS_TOKEN = "shpat_xxxx"
""", language="toml")
    st.stop()

# ─── HEADER ───────────────────────────────────────────────────
c_title, c_sel = st.columns([3, 1])
with c_title:
    st.markdown("## 🐟 Micro Aquatic Shop — Performance Dashboard")
    st.caption(f"microaquaticshop.com.au · AUD · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
with c_sel:
    days_opt = st.selectbox("Khoảng thời gian", [7, 14, 30, 60, 90], index=2,
                            format_func=lambda x: f"{x} ngày qua")

# ─── LOAD DATA ────────────────────────────────────────────────
since = f"-{days_opt}d"
with st.spinner("Đang kéo data từ Shopify..."):
    df_rev      = to_df(fetch_analytics(f"FROM sales SHOW gross_sales, net_sales, orders, total_sales TIMESERIES day SINCE {since} UNTIL today"))
    df_channel  = to_df(fetch_analytics(f"FROM sales SHOW orders, total_sales GROUP BY order_referrer_source, order_referrer_name SINCE {since} UNTIL today"))
    df_products = to_df(fetch_analytics("FROM sales SHOW gross_sales, net_sales, orders GROUP BY product_title ORDER BY gross_sales DESC LIMIT 10"))
    df_funnel   = to_df(fetch_analytics(f"FROM sessions SHOW sessions, sessions_with_cart_additions, sessions_that_reached_checkout, sessions_that_completed_checkout, conversion_rate TIMESERIES day SINCE {since} UNTIL today"))
    df_device   = to_df(fetch_analytics(f"FROM sessions SHOW sessions GROUP BY session_device_type SINCE {since} UNTIL today"))
    df_aov      = to_df(fetch_analytics(f"FROM sales SHOW average_order_value TIMESERIES day SINCE {since} UNTIL today"))
    df_customers= to_df(fetch_analytics(f"FROM sales SHOW returning_customers, customers, returning_customer_rate TIMESERIES week SINCE {since} UNTIL today"))

# ─── KPI ──────────────────────────────────────────────────────
gross_rev      = safe_sum(df_rev, "gross_sales")
net_rev        = safe_sum(df_rev, "net_sales")
total_orders   = int(safe_sum(df_rev, "orders"))
total_sessions = int(safe_sum(df_funnel, "sessions"))
avg_cvr        = safe_mean(df_funnel, "conversion_rate") * 100
aov            = gross_rev / total_orders if total_orders else 0
returning_rate = safe_mean(df_customers, "returning_customer_rate") * 100

st.markdown('<div class="section-header">📊 Key Metrics</div>', unsafe_allow_html=True)
cols = st.columns(6)
metrics = [
    ("Gross Revenue", f"${gross_rev:,.0f}", "AUD", "#38a169"),
    ("Net Revenue",   f"${net_rev:,.0f}",   "Sau discount", "#3182ce"),
    ("Total Orders",  f"{total_orders:,}",   f"~{total_orders//days_opt}/ngày", "#805ad5"),
    ("Avg Order Value", f"${aov:.2f}",       "Per order", "#dd6b20"),
    ("Sessions",      f"{total_sessions:,}", f"CVR {avg_cvr:.2f}%", "#319795"),
    ("Returning Cust", f"{returning_rate:.1f}%", "Khách quay lại", "#d53f8c"),
]
for col, (label, value, sub, color) in zip(cols, metrics):
    with col:
        st.markdown(f"""<div class="metric-card" style="border-top-color:{color}">
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-sub">{sub}</div>
        </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ─── ROW 1: Revenue + Insights ────────────────────────────────
c1, c2 = st.columns([2.5, 1])
with c1:
    st.markdown('<div class="section-header">📈 Revenue Trend</div>', unsafe_allow_html=True)
    if not df_rev.empty:
        d = df_rev.copy()
        dc = d.columns[0]
        d[dc] = pd.to_datetime(d[dc])
        d["gross_sales"] = pd.to_numeric(d["gross_sales"], errors="coerce")
        d["net_sales"]   = pd.to_numeric(d["net_sales"],   errors="coerce")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=d[dc], y=d["gross_sales"], name="Gross Sales",
            fill="tozeroy", line=dict(color="#3182ce", width=2), fillcolor="rgba(49,130,206,0.1)"))
        fig.add_trace(go.Scatter(x=d[dc], y=d["net_sales"], name="Net Sales",
            line=dict(color="#38a169", width=2, dash="dot")))
        fig.update_layout(height=260, margin=dict(l=0,r=0,t=10,b=0),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            yaxis=dict(tickprefix="$", tickformat=",.0f"),
            plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

with c2:
    st.markdown('<div class="section-header">💡 Insights</div>', unsafe_allow_html=True)
    fb_orders, fb_rev = 0, 0
    if not df_channel.empty and "order_referrer_name" in df_channel.columns:
        fb = df_channel[df_channel["order_referrer_name"].str.lower() == "facebook"]
        fb_orders = int(pd.to_numeric(fb["orders"], errors="coerce").sum())
        fb_rev    = pd.to_numeric(fb["total_sales"], errors="coerce").sum()

    cvr_class = "danger" if avg_cvr < 1.0 else "warn"
    fb_class  = "danger" if fb_orders < 50 else "warn"
    st.markdown(f"""
    <div class="insight-good"><div class="insight-title">✅ Google Search mạnh</div>
    <div class="insight-text">Google là kênh chủ lực. Protect SEO ranking, cân nhắc tăng Google Ads budget.</div></div>
    <div class="insight-{fb_class}"><div class="insight-title">{'🔴' if fb_orders<50 else '⚠️'} Facebook: {fb_orders} orders</div>
    <div class="insight-text">FB đem về ${fb_rev:,.0f} AUD / {days_opt} ngày. Nếu chi ads nhiều → review ROI ngay.</div></div>
    <div class="insight-{cvr_class}"><div class="insight-title">⚠️ CVR {avg_cvr:.2f}% — cần tối ưu</div>
    <div class="insight-text">Drop rate cao ở bước cart→checkout. Thử: free shipping threshold, trust badges, upsell.</div></div>
    """, unsafe_allow_html=True)

# ─── ROW 2: Channel + Funnel ──────────────────────────────────
c3, c4 = st.columns(2)
with c3:
    st.markdown('<div class="section-header">📡 Kênh Traffic → Revenue</div>', unsafe_allow_html=True)
    if not df_channel.empty:
        d = df_channel.copy()
        d["total_sales"] = pd.to_numeric(d["total_sales"], errors="coerce")
        d["orders"]      = pd.to_numeric(d["orders"],      errors="coerce")
        if "order_referrer_source" in d.columns and "order_referrer_name" in d.columns:
            d["Kenh"] = d.apply(
                lambda r: r["order_referrer_name"].title() if r["order_referrer_name"]
                else (r["order_referrer_source"].title() if r["order_referrer_source"] else "Direct/Unknown"),
                axis=1)
        else:
            d["Kenh"] = "Unknown"
        d = d.sort_values("total_sales", ascending=False).head(10)
        fig = px.bar(d, x="total_sales", y="Kenh", orientation="h",
            color="total_sales", color_continuous_scale="Blues",
            labels={"total_sales": "Revenue (AUD)", "Kenh": ""},
            text=d["total_sales"].apply(lambda x: f"${x:,.0f}"))
        fig.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0),
            coloraxis_showscale=False, plot_bgcolor="white", paper_bgcolor="white",
            yaxis=dict(autorange="reversed"))
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

with c4:
    st.markdown('<div class="section-header">🔻 Sales Funnel</div>', unsafe_allow_html=True)
    if not df_funnel.empty:
        vals = {
            "Sessions":           int(safe_sum(df_funnel, "sessions")),
            "Cart Additions":     int(safe_sum(df_funnel, "sessions_with_cart_additions")),
            "Reached Checkout":   int(safe_sum(df_funnel, "sessions_that_reached_checkout")),
            "Completed Purchase": int(safe_sum(df_funnel, "sessions_that_completed_checkout")),
        }
        fn_df = pd.DataFrame({"Stage": list(vals.keys()), "Count": list(vals.values())})
        fig = go.Figure(go.Funnel(
            y=fn_df["Stage"], x=fn_df["Count"],
            textinfo="value+percent initial",
            marker=dict(color=["#3182ce","#805ad5","#dd6b20","#38a169"]),
            connector=dict(line=dict(color="#e2e8f0", width=1))
        ))
        fig.update_layout(height=300, margin=dict(l=0,r=0,t=10,b=0), paper_bgcolor="white")
        st.plotly_chart(fig, use_container_width=True)

# ─── ROW 3: Products + Device ─────────────────────────────────
c5, c6 = st.columns([2, 1])
with c5:
    st.markdown('<div class="section-header">🏆 Top San Pham (Gross Sales)</div>', unsafe_allow_html=True)
    if not df_products.empty:
        d = df_products[df_products["product_title"].astype(str).str.strip() != ""].copy()
        d["gross_sales"] = pd.to_numeric(d["gross_sales"], errors="coerce")
        d = d.sort_values("gross_sales", ascending=False).head(9)
        d["Short"] = d["product_title"].apply(lambda x: x[:30]+"..." if len(x)>30 else x)
        fig = px.bar(d, x="Short", y="gross_sales",
            color="gross_sales", color_continuous_scale="Purples",
            labels={"gross_sales":"Gross Sales (AUD)","Short":""},
            text=d["gross_sales"].apply(lambda x: f"${x/1000:.1f}k"))
        fig.update_layout(height=280, margin=dict(l=0,r=0,t=10,b=40),
            coloraxis_showscale=False, plot_bgcolor="white", paper_bgcolor="white",
            xaxis_tickangle=-30)
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)

with c6:
    st.markdown('<div class="section-header">📱 Device Split</div>', unsafe_allow_html=True)
    if not df_device.empty:
        d = df_device.copy()
        d["sessions"] = pd.to_numeric(d["sessions"], errors="coerce")
        fig = px.pie(d, names=d.columns[0], values="sessions", hole=0.55,
            color_discrete_sequence=["#3182ce","#805ad5","#38a169","#a0aec0","#dd6b20"])
        fig.update_layout(height=220, margin=dict(l=0,r=0,t=10,b=0),
            legend=dict(font=dict(size=11)), paper_bgcolor="white")
        st.plotly_chart(fig, use_container_width=True)

    if not df_customers.empty:
        d = df_customers.copy()
        wc = d.columns[0]
        d[wc] = pd.to_datetime(d[wc])
        d["returning_customer_rate"] = pd.to_numeric(d["returning_customer_rate"], errors="coerce") * 100
        fig = go.Figure(go.Scatter(x=d[wc], y=d["returning_customer_rate"],
            fill="tozeroy", line=dict(color="#d53f8c", width=2),
            fillcolor="rgba(213,63,140,0.1)"))
        fig.update_layout(height=160, margin=dict(l=0,r=0,t=6,b=0),
            yaxis=dict(ticksuffix="%", range=[0,100]),
            plot_bgcolor="white", paper_bgcolor="white", showlegend=False)
        st.caption("Returning Customer Rate (weekly %)")
        st.plotly_chart(fig, use_container_width=True)

# ─── AOV Trend ────────────────────────────────────────────────
st.markdown('<div class="section-header">💰 Average Order Value (AUD)</div>', unsafe_allow_html=True)
if not df_aov.empty:
    d = df_aov.copy()
    dc = d.columns[0]
    d[dc] = pd.to_datetime(d[dc])
    d["average_order_value"] = pd.to_numeric(d["average_order_value"], errors="coerce")
    avg_line = d["average_order_value"].mean()
    fig = go.Figure()
    fig.add_hline(y=avg_line, line_dash="dash", line_color="#a0aec0",
        annotation_text=f"Avg ${avg_line:.2f}", annotation_position="top right")
    fig.add_trace(go.Scatter(x=d[dc], y=d["average_order_value"],
        fill="tozeroy", line=dict(color="#319795", width=2), fillcolor="rgba(49,151,149,0.1)"))
    fig.update_layout(height=160, margin=dict(l=0,r=0,t=10,b=0),
        yaxis=dict(tickprefix="$"),
        plot_bgcolor="white", paper_bgcolor="white", showlegend=False, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

# ─── Raw data ─────────────────────────────────────────────────
with st.expander("📋 Raw Data"):
    t1, t2, t3 = st.tabs(["Revenue", "Channels", "Products"])
    with t1: st.dataframe(df_rev, use_container_width=True)
    with t2: st.dataframe(df_channel, use_container_width=True)
    with t3: st.dataframe(df_products, use_container_width=True)

st.caption("Dashboard by Nature Marketing Agency · Data tu Shopify · Auto-refresh 15 phut")
