"""Hunt: a stock-first workspace for researching cash-secured puts."""
from __future__ import annotations

import datetime as dt
import html
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scanner import market_data as MD, scoring as SC

st.set_page_config(page_title="Hunt · Stock & income finder", page_icon="◈", layout="wide")
st.markdown(f"<style>{(ROOT / 'dashboard/style.css').read_text()}</style>", unsafe_allow_html=True)
ACCENT, INK, SECONDARY = "#3569b0", "#292b30", "#cf893f"


def fmt(v, spec="{:,.2f}"):
    return spec.format(float(v)) if v is not None and pd.notna(v) else "—"


def heading(kicker, title, text=""):
    st.markdown(f'<div class="eyebrow">{html.escape(kicker)}</div><h2>{html.escape(title)}</h2>'
                f'<p class="muted">{html.escape(text)}</p>', unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_scan(path, stamp):
    p = Path(path)
    return (pd.read_csv(p / "metrics.csv"), json.loads((p / "config.json").read_text()),
            pd.read_parquet(p / "history.parquet") if (p / "history.parquet").exists() else pd.DataFrame())


@st.cache_data(ttl=90, show_spinner=False)
def expiries(ticker, today):
    return [d.isoformat() for d in MD.listed_expiries(ticker, dt.date.fromisoformat(today), max_dte=90)]


@st.cache_data(ttl=90, show_spinner=False)
def chain(ticker, expiry):
    raw = MD.fetch_option_chain(ticker, dt.date.fromisoformat(expiry))
    return raw, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@st.cache_data(ttl=3600, show_spinner=False)
def earnings(ticker, today):
    return MD.next_earnings_date(ticker, dt.date.fromisoformat(today))


def chart_style(fig, height=350):
    fig.update_layout(height=height, margin=dict(l=15, r=20, t=35, b=20),
                      font=dict(family="Arial, sans-serif", color=INK, size=12),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      colorway=[ACCENT, SECONDARY], legend=dict(orientation="h", y=1.12),
                      hoverlabel=dict(bgcolor="white", font_color=INK))
    fig.update_xaxes(gridcolor="#e7e8eb", zeroline=False)
    fig.update_yaxes(gridcolor="#e7e8eb", zeroline=False)
    return fig


def pick(ticker):
    st.session_state["idea_pick"] = ticker
    st.session_state["next_workspace"] = "Company"
    st.session_state["map_epoch"] = st.session_state.get("map_epoch", 0) + 1


def hunt_map(metrics, highlights):
    pts = metrics.dropna(subset=["quality_score", "residual"])
    fig = go.Figure()
    for member, color, name in [(False, "#c4c7cd", "Other businesses"), (True, ACCENT, "Shortlist")]:
        p = pts[pts.ticker.isin(highlights.ticker) == member]
        fig.add_trace(go.Scatter(x=-p.residual, y=p.quality_score,
            customdata=p[["ticker", "name"]].fillna("").values,
            text=p.ticker if member and len(p) <= 15 else None,
            mode="markers+text" if member and len(p) <= 15 else "markers",
            textposition="top center", name=name,
            marker=dict(size=12 if member else 8, color=color, line=dict(width=1.5, color="white")),
            hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}<br>Business quality: %{y:.0f}/100<extra></extra>"))
    if not pts.empty:
        right = max(0.5, float((-pts.residual).max()) + .15)
        fig.add_shape(type="rect", x0=0, x1=right, y0=60, y1=105,
                      fillcolor="rgba(53,105,176,.055)", line_width=0, layer="below")
        fig.add_annotation(x=right * .65, y=102, text="THE HUNT ZONE", showarrow=False,
                           font=dict(size=10, color=ACCENT))
    fig.add_hline(y=60, line_dash="dot", line_color="#c4c7cd")
    fig.add_vline(x=0, line_dash="dot", line_color="#c4c7cd")
    fig.update_layout(xaxis_title="More expensive  ←     Price for the business quality     →  Cheaper",
                      yaxis_title="Business quality", yaxis_range=[0, 110])
    event = st.plotly_chart(chart_style(fig, 410), width="stretch", key=f"hunt_map_{st.session_state.get('map_epoch', 0)}",
                            on_select="rerun", selection_mode="points")
    points = event.selection.points
    if points:
        ticker = points[0]["customdata"][0]
        pick(ticker)
        st.rerun()
    missing = len(metrics) - len(pts)
    st.caption(f"Select a dot to explore. Shading marks quality ≥ 60 and cheaper-than-model pricing; "
               f"shortlisted stocks also pass cash-flow and return-on-capital checks. "
               f"{missing} companies lack map inputs and remain searchable.")


def cards(rows, hist, growth=False, all_companies=False):
    for offset in range(0, min(len(rows), 6), 3):
        for col, (_, row) in zip(st.columns(3), rows.iloc[offset:offset+3].iterrows()):
            with col, st.container(border=True):
                badge = "COMPANY RESEARCH" if all_companies else "GROWTH RESEARCH" if growth else "QUALITY + VALUE"
                tone = "research" if all_companies else "growth" if growth else "value"
                st.markdown(f'<div class="card-top {tone}"><span class="stock-avatar">{html.escape(row.ticker[:2])}</span>'
                            f'<span class="eyebrow">{badge}</span></div>'
                            f'<h3>{html.escape(row.ticker)} <span class="stock-price">{fmt(row.get("price"), "${:,.2f}")}</span></h3>'
                            f'<div class="company-name">{html.escape(str(row.get("name", "")))}</div>', unsafe_allow_html=True)
                a, b = st.columns(2)
                a.metric("Revenue growth · 5y", fmt(row.get("rev_cagr5"), "{:.1%}"))
                b.metric("Reported cash yield", fmt(row.get("fcf_yield"), "{:.1%}"))
                st.caption("Investment spending needs a closer look." if growth else
                           f'Business quality {fmt(row.get("quality_score"), "{:.0f}")}/100 · {row.get("sector", "")}')
                st.button("Explore " + row.ticker + " →", key="card_"+row.ticker,
                          on_click=pick, args=(row.ticker,), width="stretch")


def discovery(metrics, cfg, hist):
    heading("STOCKS FIRST. INCOME SECOND.", "Good businesses. Better entry prices.",
            "Find something you’d be happy to own. Then see what you can earn while you wait.")
    lens, sector = st.columns([2, 1])
    with lens:
        mode = st.segmented_control("Research lens", ["Quality & value", "Investing for growth", "All companies"],
                                    default="Quality & value", key="lens") or "Quality & value"
    with sector:
        sec = st.selectbox("Sector", ["All sectors"] + sorted(metrics.sector.dropna().unique().tolist()))
    universe = metrics if sec == "All sectors" else metrics[metrics.sector == sec]
    hi = SC.highlighted(metrics, gs10=cfg.get("gs10"))
    hi = hi[hi.ticker.isin(universe.ticker)]
    growth = universe.iloc[0:0]
    if SC.owner_cash_available(metrics):
        # A research lane, deliberately independent of the conservative shortlist.
        growth = universe[(universe.rev_cagr5 > 0) & (universe.fcf_owner_ttm > 0)
                          & (universe.fcf_owner_ttm > universe.fcf_adj_ttm)].sort_values("rev_cagr5", ascending=False)
    a, b, c = st.columns(3)
    a.metric("Businesses to explore", len(universe))
    b.metric("Quality & value shortlist", len(hi))
    c.metric("Investing for growth", len(growth))
    with st.container(border=True):
        heading("01 / EXPLORE", "The hunt map", "Look toward the upper right: stronger businesses at more interesting prices.")
        hunt_map(universe, hi)
    heading("02 / YOUR NEXT LOOK", mode,
            "A starting point for your research. Open a company to check its business and put income.")
    if mode == "Investing for growth":
        st.info("Growing revenue, positive cash before estimated growth investment, and spending above the depreciation proxy. "
                "These are research candidates; investment spending alone does not establish business quality or cheapness.")
        rows = growth
    elif mode == "All companies":
        rows = universe.sort_values("ticker")
    else:
        rows = hi
    if rows.empty:
        st.info("No companies match this view. Try another sector or use the company search to explore any stock.")
    else:
        cards(rows, hist, mode == "Investing for growth", mode == "All companies")
        with st.expander(f"Compare all {len(rows)} companies"):
            cols = {"ticker": "Stock", "name": "Company", "price": "Share price", "quality_score": "Business quality",
                    "rev_cagr5": "Revenue growth · 5y", "fcf_yield": "Reported cash yield"}
            event = st.dataframe(rows[list(cols)].rename(columns=cols).reset_index(drop=True), hide_index=True,
                width="stretch", on_select="rerun", selection_mode="single-row", key=f"compare_stocks_{st.session_state.get('map_epoch', 0)}",
                column_config={"Share price": st.column_config.NumberColumn(format="$%.2f"),
                               "Revenue growth · 5y": st.column_config.NumberColumn(format="percent"),
                               "Reported cash yield": st.column_config.NumberColumn(format="percent")})
            if event.selection.rows:
                pick(rows.iloc[event.selection.rows[0]].ticker)
                st.rerun()
    income_comparison(rows, cfg)
    with st.expander("How the shortlist works"):
        st.write("Quality combines profitability, growth, debt and consistency. The shortlist requires quality ≥ 60, "
                 "adequate data, return on capital ≥ 10%, cheaper-than-model valuation, and a cash yield above "
                 "the higher of 4% and the scan’s 10-year Treasury yield. Reported cash deducts capital spending "
                 "and stock compensation. Option premiums do not change this business shortlist.")


def business(row, hist, cfg):
    a, b, c, d = st.columns(4)
    a.metric("Business quality", fmt(row.get("quality_score"), "{:.0f} / 100"))
    b.metric("Revenue growth · 5y", fmt(row.get("rev_cagr5"), "{:.1%}"))
    c.metric("Return on capital", fmt(row.get("roic_ttm"), "{:.1%}"))
    d.metric("Net debt / earnings", fmt(row.get("nd_ebitda"), "{:.1f}×"), help="Net debt divided by EBITDA. Negative means net cash.")
    heading("CASH & REINVESTMENT", "What’s left after funding the business?")
    a, b = st.columns(2)
    a.metric("Reported free cash flow", fmt(row.get("fcf_adj_ttm") / 1e9 if pd.notna(row.get("fcf_adj_ttm")) else None, "${:,.2f}B"),
             help="Operating cash minus all capital spending and stock compensation, trailing 12 months.")
    b.metric("Before estimated growth investment", fmt(MD._n(row.get("fcf_owner_ttm")) / 1e9 if MD._n(row.get("fcf_owner_ttm")) is not None else None, "${:,.2f}B"),
             help="Operating cash minus depreciation and stock compensation. Depreciation is only an estimate of upkeep spending.")
    st.caption("The second figure assumes depreciation approximates upkeep spending. It can overstate available cash; "
               "the gap is not proof that investment will pay off. Both figures expense stock compensation.")
    h = hist[hist.ticker == row.ticker].sort_values("week") if not hist.empty else pd.DataFrame()
    if not h.empty:
        a, b = st.columns(2)
        for col, title, keys, dollar in [(a, "Share price", [("price", "Weekly close")], False),
                (b, "Cash generation", [("fcf_adj_ttm", "After all investment"), ("fcf_owner_ttm", "Before estimated growth investment")], True)]:
            fig = go.Figure()
            for key, label in keys:
                if key in h and h[key].notna().any():
                    fig.add_trace(go.Scatter(x=h.week, y=h[key] / (1e9 if dollar else 1), name=label,
                                             line=dict(width=2.5)))
            fig.update_layout(title=title, yaxis_title="$ billions" if dollar else "$ per share")
            col.plotly_chart(chart_style(fig), width="stretch")
    else:
        st.info("No price or cash-flow history in this scan.")
    economic_charts(row, cfg)
    with st.expander("Valuation, margins & data details"):
        labels = {"fcf_yield": "Reported cash yield", "ev_fcf": "Business value / cash flow",
                  "pe_ttm": "Price / earnings", "gm_ttm": "Gross margin", "fcf_margin_ttm": "Cash-flow margin",
                  "residual": "Valuation model residual", "input_coverage": "Data coverage"}
        st.dataframe(pd.DataFrame([{"Measure": label, "Value": fmt(row.get(k), "{:.1%}" if k in
            ["fcf_yield", "gm_ttm", "fcf_margin_ttm", "input_coverage"] else "{:.2f}")} for k, label in labels.items()]), hide_index=True, width="stretch")
        st.caption(f"Scan eligibility notes: {row.get('gate_fail_reasons', 'Unavailable')}")


def options(row, cfg):
    ticker, spot = row.ticker, MD._n(row.get("price"))
    today = dt.date.today()
    if spot is None or spot <= 0:
        st.info("A share price is needed to compare options. Refresh the scan in Data & refresh.")
        return
    heading("PUT INCOME / UP TO 90 DAYS", "Get paid to name your entry price.",
            "Compare the premium, the cash you commit, and your cost if you receive 100 shares.")
    with st.spinner("Checking available expirations…"):
        exps = expiries(ticker, today.isoformat())
    if not exps:
        st.info("No option expirations available in the next 90 days. Try again later or explore another company.")
        return
    a, b, c = st.columns([2, 1, 1])
    expiry = a.selectbox("Expiration", exps, index=min(range(len(exps)), key=lambda i: abs((dt.date.fromisoformat(exps[i])-today).days-30)),
                       format_func=lambda e: f"{dt.date.fromisoformat(e):%b %d, %Y} · {(dt.date.fromisoformat(e)-today).days} days", key=f"expiry_{ticker}")
    target = b.number_input("Entry price · % of share price", min_value=50., max_value=110., value=95., step=1., key=f"opt_target_{ticker}")
    side = c.selectbox("I’m looking to", ["Sell a put", "Sell a covered call"], key=f"side_{ticker}")
    raw, fetched = chain(ticker, expiry)
    st.caption(f"Chain fetched {fetched} · provider quotes may be delayed. Reference share price: ${spot:,.2f} "
               f"from the {cfg.get('asof', 'saved')} scan. Refresh runs on interaction after 90 seconds.")
    if st.button("Refresh option quotes", key="refresh_quotes"):
        chain.clear(); expiries.clear(); earnings.clear(); st.rerun()
    is_put = side == "Sell a put"
    frame = raw.get("puts" if is_put else "calls", pd.DataFrame()) if raw else pd.DataFrame()
    if frame.empty:
        st.info("No quotes available for this expiration. Choose another date.")
        return
    frame = frame.sort_values("strike").reset_index(drop=True)
    strikes = frame.strike.tolist()
    target_signature = f"{ticker}_{expiry}_{side}_{target}"
    strike = st.selectbox("Buy at this price" if is_put else "Sell shares at this price", strikes,
            index=min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot * target / 100)),
            format_func=lambda k: f"${k:,.2f}", key="strike_"+target_signature)
    q = frame[frame.strike == strike].iloc[0]
    bid, ask = MD._n(q.get("bid")), MD._n(q.get("ask"))
    # Never present a last trade or midpoint as income available to collect.
    premium = bid if bid is not None and bid > 0 else None
    days = (dt.date.fromisoformat(expiry)-today).days
    if premium is None:
        st.warning("No positive bid for this contract. Income and net cost are unavailable; choose another strike or refresh.")
    elif ask is not None and ask < premium:
        premium = None
        st.warning("The bid is above the ask. This quote is inconsistent; refresh before comparing income.")
    elif ask is not None and (ask - premium) / max((ask+premium)/2, .01) >= .5:
        st.warning("Wide bid–ask spread. The displayed income uses the bid; execution is uncertain.")
    a, b, c, d = st.columns(4)
    a.metric("Premium · 1 contract", fmt(premium * 100 if premium is not None else None, "${:,.0f}"))
    b.metric("Return on committed cash" if is_put else "Premium / share value", fmt(premium / (strike if is_put else spot) if premium is not None else None, "{:.2%}"))
    c.metric("Cash to set aside" if is_put else "Shares required", fmt(strike*100, "${:,.0f}") if is_put else "100")
    basis = strike-premium if premium is not None else None
    d.metric("Net cost if assigned" if is_put else "Sale price + premium", fmt(basis if is_put else strike+premium if premium is not None else None, "${:,.2f}"))
    st.caption(f"Premium / reference share price: {fmt(premium / spot if premium is not None else None, '{:.2%}')} · "
               f"{days}-day holding period · before fees and taxes.")
    ned = earnings(ticker, today.isoformat())
    if ned and today <= ned <= dt.date.fromisoformat(expiry):
        st.warning(f"Earnings fall within this contract: {ned:%b %d, %Y}. The stock may move sharply around the report.")
    elif ned is None:
        st.caption("Next earnings date unavailable. Check the company’s calendar before choosing an expiration.")
    if is_put:
        yld = MD.fcf_yield_at_price(row.get("fcf_adj_ttm"), row.get("shares_cover"), row.get("stack"), basis)
        st.markdown(f"**If assigned** · You buy 100 shares at {fmt(strike, '${:,.2f}')}. "
                    f"After the premium, your cost is {fmt(basis, '${:,.2f}')} per share. "
                    f"Reported business cash yield at that cost: **{fmt(yld, '{:.1%}')}**.")
        st.caption("Cash-flow yield describes the business, not cash distributed to you. Share losses can exceed the premium.")
        with st.container(border=True):
            st.markdown("**The next step: own the shares, then consider a call**")
            call = MD.same_strike_call(raw.get("calls", pd.DataFrame()), strike)
            cb = MD._n(call.get("bid")) if call is not None else None
            st.write(f"Today’s call at the same ${strike:,.2f} strike has a ${cb:,.2f} bid per share."
                     if cb is not None and cb > 0 else "No positive same-strike call bid available for this expiration.")
            st.caption("This is today’s comparison quote. A call sold after assignment will have a different expiration and premium. "
                       "Selling at or above your assignment price can avoid a share-price loss if called away; a buyer and a useful premium are not guaranteed.")
    else:
        cost = st.number_input("Your assignment price / original cost per share", min_value=0., value=float(spot), step=1., key=f"cost_{ticker}")
        net = (strike-cost+premium)*100 if premium is not None else None
        st.metric("Share gain / loss + this premium if called away", fmt(net, "${:,.2f}"))
        st.caption("Assumes you already own 100 shares. Excludes prior premiums, fees and taxes. A covered call limits gains above the strike.")
    with st.expander("Compare strikes & quote details"):
        show = frame.copy()
        show["Premium / share price"] = show.bid.where(show.bid > 0) / spot
        st.dataframe(show[[c for c in ["strike", "bid", "ask", "Premium / share price", "volume", "oi", "iv"] if c in show]],
                     hide_index=True, width="stretch", column_config={"Premium / share price": st.column_config.NumberColumn(format="percent")})
        stats = MD.contract_analytics("put" if is_put else "call", spot, strike, premium, days, MD._n(q.get("iv")), rate=cfg.get("gs10") or 0.)
        st.caption(f"Model delta: {fmt(stats.get('delta'), '{:.2f}')} · implied volatility: {fmt(q.get('iv'), '{:.1%}')} · "
                   "Delta is price sensitivity, not a reliable probability of assignment.")


def company(metrics, cfg, hist):
    sel = st.session_state.get("idea_pick")
    if sel not in set(metrics.ticker):
        st.info("Choose any company in the search above to start exploring.")
        return
    row = metrics[metrics.ticker == sel].iloc[0]
    heading(str(row.get("sector", "COMPANY RESEARCH")).upper(), f"{sel} · {row.get('name', '')}",
            f"{fmt(row.get('price'), '${:,.2f}')} per share · reference price from the saved scan")
    tab = st.segmented_control("Company workspace", ["The business", "Put & call income"], default="The business", key="company_tab")
    if tab == "Put & call income":
        options(row, cfg)
    else:
        business(row, hist, cfg)


def main():
    if "next_workspace" in st.session_state:
        st.session_state["workspace"] = st.session_state.pop("next_workspace")
        st.session_state.pop("company_search", None)
    with st.sidebar:
        st.markdown('<div class="brand">◈ hunt<span>THE STOCK & INCOME FINDER</span></div>', unsafe_allow_html=True)
        page = st.radio("Workspace", ["Discover", "Company", "Data & refresh"], key="workspace", label_visibility="collapsed")
        st.markdown('<div class="sidebar-note"><b>A stock-first approach</b><br><br>01 &nbsp; Find a business worth owning.<br><br>'
                    '02 &nbsp; Choose your price. Sell a put.<br><br>03 &nbsp; If assigned, hold or sell calls.</div>', unsafe_allow_html=True)
        st.caption("Research workspace · 0–90 day options")
    if page == "Data & refresh":
        import data_manager
        data_manager.render()
        return
    scans = sorted(p for p in (ROOT / "data/scans").glob("*") if (p / "config.json").exists() and (p / "metrics.csv").exists())
    if not scans:
        heading("WELCOME TO HUNT", "Your next idea starts with a scan.")
        st.info("Open Data & refresh to download the stock universe and run your first scan.")
        return
    p = scans[-1]
    metrics, cfg, hist = load_scan(str(p), (p / "config.json").stat().st_mtime_ns)
    a, b = st.columns([3, 2])
    a.markdown(f'<div class="topline">RESEARCH DESK <span> / {html.escape(page.upper())}</span></div>', unsafe_allow_html=True)
    b.caption(f"● Scan {cfg.get('asof', p.name)} · {len(metrics)} companies · options fetched on demand")
    names = metrics.set_index("ticker")["name"].to_dict()
    search = st.selectbox("Find a company", sorted(names), index=None,
                          placeholder="Search any company or ticker — try Oracle or ORCL", format_func=lambda t: f"{t} · {names[t]}", key="company_search")
    if search:
        pick(search)
        st.rerun()
    if page == "Company":
        company(metrics, cfg, hist)
    else:
        discovery(metrics, cfg, hist)


@st.cache_data(show_spinner=False)
def research(path, asof, stamp):
    from scanner.company_research import cached_research
    return cached_research(path, asof)


def economic_charts(row, cfg):
    cik = MD._n(row.get("cik"))
    if cik is None:
        return
    path = ROOT / "data/raw/sec" / f"CIK{int(cik):010d}.json"
    if not path.exists():
        st.caption("Detailed operating history is unavailable in the local SEC cache. Data & refresh can download it.")
        return
    with st.spinner("Reading the company’s financial history…"):
        flows, balance = research(str(path), cfg.get("asof", dt.date.today().isoformat()), path.stat().st_mtime_ns)
    if flows.empty and balance.empty:
        st.caption("No detailed financial history available for this company.")
        return
    heading("THE ECONOMICS", "Is growth becoming a stronger business?",
            "Trailing-year operating figures and reported balance sheets, shown when their filings became available.")
    a, b = st.columns(2)
    panels = [(a, flows, "Revenue & operating profit", [("revenue", "Revenue"), ("ebit", "Operating profit")]),
              (b, flows, "Can operations fund investment?", [("cfo", "Operating cash"), ("capex", "Capital spending")])]
    c, d = st.columns(2)
    panels += [(c, balance, "Debt & cash reserves", [("debt", "Borrowings, excluding leases"), ("cash", "Cash & equivalents")]),
               (d, flows, "What does growth leave for owners?", [("fcf_adj", "Cash after capex & stock pay"), ("sbc", "Stock compensation")])]
    for col, frame, title, fields in panels:
        fig = go.Figure()
        for field, label in fields:
            if field in frame and frame[field].notna().any():
                fig.add_trace(go.Scatter(x=frame.effective, y=frame[field]/1e9, name=label,
                                        line=dict(width=2.5, shape="hv"), connectgaps=False))
        if fig.data:
            fig.update_layout(title=title, yaxis_title="$ billions")
            col.plotly_chart(chart_style(fig, 320), width="stretch")
        else:
            col.caption(f"{title}: history unavailable.")
    st.caption("Source: cached SEC filings, restricted to the scan date. Capital spending includes upkeep and expansion; "
               "the filings do not reliably separate them. Acquisition spending is not included in capex.")


def income_comparison(rows, cfg):
    heading("03 / GET PAID TO WAIT", "Which puts are worth a closer look?",
            "Compare actual bids at a similar duration and entry discount. Load only the companies you want to research.")
    if rows.empty:
        return
    choices = rows.ticker.tolist()
    chosen = st.multiselect("Companies to compare", choices, default=choices[:3], max_selections=6, key="income_companies")
    a, b, c = st.columns(3)
    days = a.select_slider("Target duration", [7, 14, 30, 45, 60, 90], value=30)
    discount = b.slider("Entry discount", 0, 20, 5, format="%d%%")
    budget = c.number_input("Cash available per contract", min_value=0, value=25000, step=1000)
    signature = (tuple(chosen), days, discount, cfg.get("asof"))
    if st.button("Compare put income", disabled=not chosen):
        results = []
        today = dt.date.today()
        progress = st.progress(0, text="Loading option quotes…")
        for i, ticker in enumerate(chosen):
            r = rows[rows.ticker == ticker].iloc[0]
            spot = MD._n(r.get("price"))
            exps = expiries(ticker, today.isoformat())
            item = {"Stock": ticker, "Status": "No quote available"}
            if exps and spot is not None and spot > 0:
                exp = min(exps, key=lambda e: abs((dt.date.fromisoformat(e)-today).days-days))
                raw, fetched = chain(ticker, exp)
                puts = raw.get("puts", pd.DataFrame()) if raw else pd.DataFrame()
                if not puts.empty:
                    q = MD.pick_nearest_strike(puts, spot, 1-discount/100)
                    if q:
                        bid, ask, strike = MD._n(q.get("bid")), MD._n(q.get("ask")), q["strike"]
                        valid = bid is not None and bid > 0 and (ask is None or ask >= bid)
                        item.update({"Expiration": exp, "Days": (dt.date.fromisoformat(exp)-today).days,
                            "Strike": strike, "Cash required": strike*100, "Fetched": fetched,
                            "Premium": bid*100 if valid else None,
                            "Return on cash": bid/strike if valid else None,
                            "Premium / stock": bid/spot if valid else None,
                            "Net entry": strike-bid if valid else None,
                            "Status": "Positive bid" if valid else "No usable bid"})
                        ned = earnings(ticker, today.isoformat())
                        item["Earnings"] = "Before expiry" if ned and today <= ned <= dt.date.fromisoformat(exp) else "After expiry" if ned else "Unknown"
                        if valid and ask is not None and (ask-bid)/max((ask+bid)/2, .01) >= .5:
                            item["Status"] = "Wide spread"
            results.append(item)
            progress.progress((i+1)/len(chosen), text=f"Checked {ticker}")
        progress.empty()
        st.session_state["income_results"] = (signature, results)
    saved = st.session_state.get("income_results")
    if saved and saved[0] == signature:
        result = pd.DataFrame(saved[1])
        if "Cash required" in result:
            result = result[result["Cash required"].isna() | (result["Cash required"] <= budget)]
        if "Return on cash" in result:
            result = result.sort_values("Return on cash", ascending=False, na_position="last")
        if result.empty:
            st.info("No quoted contracts fit this cash budget. Raise it or compare lower-priced stocks.")
        else:
            st.dataframe(result, hide_index=True, width="stretch", column_config={
                **{k: st.column_config.NumberColumn(format="percent") for k in ["Return on cash", "Premium / stock"]},
                **{k: st.column_config.NumberColumn(format="$%.2f") for k in ["Strike", "Cash required", "Premium", "Net entry"]}})
            st.caption("Sorted by bid premium / cash required. Actual expiration dates differ; compare the Days column. "
                       "Returns cover one contract period, before fees. Share-price comparisons use saved scan prices. "
                       "Click Compare put income again to update results; quotes are cached for 90 seconds.")


main()
