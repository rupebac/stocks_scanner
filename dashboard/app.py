"""Hunt: a stock-first workspace for researching cash-secured puts."""
from __future__ import annotations

import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
_APP_VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "0.0.0"


@st.cache_resource(show_spinner=False)
def _git_commit() -> str:
    """Short commit hash rendered with the version, so the deployed app can be
    compared to origin/main at a glance. 'dev' when git isn't available."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "dev"
    except Exception:
        return "dev"
sys.path.insert(0, str(ROOT))
from scanner import market_data as MD, scoring as SC, opportunities as OP
from dashboard import hunt_chart, browser_ideas as WL
from scanner.research_scores import add_research_scores
from scanner.universe_selection import UNIVERSES, available_scans
from scanner import company_lookup as CL
from scanner import discovery_filters as DF
from scanner import config as SC_CONFIG

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
    return (add_research_scores(pd.read_csv(p / "metrics.csv")), json.loads((p / "config.json").read_text()),
            pd.read_parquet(p / "history.parquet") if (p / "history.parquet").exists() else pd.DataFrame())


@st.cache_data(ttl=90, show_spinner=False)
def expiries(ticker, today):
    return [d.isoformat() for d in MD.listed_expiries(ticker, dt.date.fromisoformat(today), max_dte=90)]


@st.cache_data(ttl=90, show_spinner=False)
def chain(ticker, expiry):
    raw = MD.fetch_option_chain(ticker, dt.date.fromisoformat(expiry))
    return raw, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@st.cache_data(ttl=3600, show_spinner=False)
def earnings(ticker, today):
    return MD.next_earnings_date(ticker, dt.date.fromisoformat(today))


def chart_style(fig, height=350):
    fig.update_layout(height=height, margin=dict(l=15, r=20, t=65 if fig.layout.title.text else 20, b=70),
                      font=dict(family="Arial, sans-serif", color=INK, size=12),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      colorway=[ACCENT, SECONDARY], legend=dict(orientation="h", y=-.25, x=0, font=dict(size=11)),
                      hoverlabel=dict(bgcolor="white", font_color=INK))
    fig.update_xaxes(gridcolor="#e7e8eb", zeroline=False)
    fig.update_yaxes(gridcolor="#e7e8eb", zeroline=False)
    return fig


def pick(ticker):
    st.session_state["idea_pick"] = ticker
    st.session_state["next_workspace"] = "Company"
    st.session_state["map_epoch"] = st.session_state.get("map_epoch", 0) + 1


def hunt_map(metrics, highlights, quality_min=60, valuation_min=0.):
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
        right = max(2.15, float((-pts.residual).max()) + .15)
        left = min(-2.15, float((-pts.residual).min()) - .15)
        fig.update_xaxes(range=[left, right])
        fig.add_shape(type="rect", x0=valuation_min, x1=right, y0=quality_min, y1=105,
                      fillcolor="rgba(53,105,176,.055)", line_width=0, layer="below", name="hunt-zone")
        fig.add_annotation(x=valuation_min+(right-valuation_min)*.65, y=106, text="THE HUNT ZONE", showarrow=False,
                           font=dict(size=10, color=ACCENT))
    fig.add_hline(y=quality_min, line_dash="dot", line_color="#8da8ca", name="quality-cut")
    fig.add_vline(x=valuation_min, line_dash="dot", line_color="#8da8ca", name="valuation-cut")
    fig.update_layout(xaxis_title="More expensive  ←     Price for the business quality     →  Cheaper",
                      yaxis_title="Business quality", yaxis_range=[0, 110])
    event = hunt_chart.render(chart_style(fig, 615), quality_min, valuation_min,
                              key=f"hunt_drag_{st.session_state.get('map_epoch', 0)}")
    if isinstance(event, dict) and event.get("id") and event["id"] != st.session_state.get("hunt_event_seen"):
        st.session_state["hunt_event_seen"] = event["id"]
        zone = hunt_chart.zone_event(event, quality_min, valuation_min)
        if zone is not None and zone != {"quality": quality_min, "valuation": valuation_min}:
            st.session_state["pending_hunt_zone"] = zone
            st.rerun()
        if event.get("kind") == "stock" and event.get("ticker") in set(metrics.ticker):
            pick(event["ticker"])
            st.rerun()
    missing = len(metrics) - len(pts)
    st.caption(f"Drag a dotted line or its handle to adjust the zone; select a dot to explore. Shading marks quality ≥ {quality_min:.0f}/100 and valuation advantage > {valuation_min:.2f}; "
               f"shortlisted stocks also pass cash-flow and return-on-capital checks. "
               f"{missing} companies lack map inputs and remain searchable.")


def card_visuals(row, hist):
    """Compact, local-only six-month price history and scanner scores."""
    parts = []
    if not hist.empty and {"ticker", "week", "price"} <= set(hist.columns):
        end = pd.to_datetime(hist.week).max()
        start = end - pd.DateOffset(months=6)
        h = hist.loc[hist.ticker == row.ticker, ["week", "price"]].copy()
        h["week"] = pd.to_datetime(h.week)
        h["price"] = pd.to_numeric(h.price, errors="coerce")
        h = h[(h.week >= start) & (h.week <= end) & h.price.notna() & (h.price > 0)].sort_values("week")
    else:
        h = pd.DataFrame()
    if len(h) >= 2:
        first, last = float(h.price.iloc[0]), float(h.price.iloc[-1])
        change = last/first - 1
        color = "#287650" if change >= 0 else "#be7733"
        low, high = float(h.price.min()), float(h.price.max())
        span = high-low or max(high*.02, 1)
        x = 4 + (h.week-start).dt.total_seconds()/(end-start).total_seconds()*292
        y = 70 - (h.price-low)/span*56
        points = " ".join(f"{a:.1f},{b:.1f}" for a,b in zip(x,y))
        description = f"{row.ticker}: weekly closes {h.week.iloc[0]:%b %d, %Y} to {h.week.iloc[-1]:%b %d, %Y}. Change over available period: {change:+.1%}."
        parts.append(f'<div class="spark-label"><span>PRICE · 6M</span><b style="color:{color}">{change:+.1%}</b></div>'
                     f'<svg class="price-spark" viewBox="0 0 300 80" role="img" aria-label="{html.escape(description, quote=True)}">'
                     f'<title>{html.escape(description)}</title>'
                     f'<path d="M {x.iloc[0]:.1f} 78 L {points.replace(" ", " L ")} L {x.iloc[-1]:.1f} 78 Z" fill="{color}" opacity=".055"/>'
                     f'<line x1="4" y1="{y.iloc[0]:.1f}" x2="296" y2="{y.iloc[0]:.1f}" stroke="#aeb8c4" stroke-width=".8" stroke-dasharray="3 4" opacity=".6"/>'
                     f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
                     f'<circle cx="{x.iloc[-1]:.1f}" cy="{y.iloc[-1]:.1f}" r="3" fill="{color}"/></svg>'
                     f'<div class="spark-range" title="Range of available weekly closes, not intraday highs and lows. Dashed line marks the first close in the period."><span>6M range</span><span>${low:,.2f} — ${high:,.2f}</span></div>')
    else:
        parts.append('<div class="spark-empty">6M price history unavailable</div>')
    parts.append('<div class="card-score-grid">')
    for label, key, color, help_text in [
        ("Quality", "quality_score", "#3569b0", "Business quality, scored out of 100. Not a probability or expected return."),
        ("Cheapness", "cheapness_score", "#287650", "Cheapness score out of 100: valuation versus history, peers and cash yield. Higher means cheaper. Not a percentage discount; shortlist order uses the separate valuation model.")]:
        value = OP.number(row.get("display_"+key, row.get(key)))
        estimated = row.get(key+"_status") == "Estimate"
        help_text += " " + str(row.get(key+"_note", ""))
        display = f"{value:.0f}%{'*' if estimated else ''}" if value is not None else "—"
        width = min(100, max(0, value)) if value is not None else 0
        parts.append(f'<div title="{html.escape(help_text, quote=True)}"><span>{label}</span><strong>{display}</strong>'
                     f'<div class="score-track"><i style="width:{width:.1f}%;background:{color}"></i></div></div>')
    parts.append('</div>')
    if any(row.get(k+"_score_status") == "Estimate" for k in ("quality", "cheapness")):
        parts.append('<div class="score-estimate-note">* Estimate · partial data</div>')
    parts.append('<div class="card-facts">')
    for label, key, tip in [
        ("Cash yield", "fcf_yield", "Trailing-year operating cash minus capital spending and stock compensation, divided by enterprise value (market cap plus debt and other claims, less cash). This is a business valuation measure, not a dividend or option premium."),
        ("Revenue · 5y", "rev_cagr5", "Compound annual revenue growth over the last five fiscal years. For example, 10% means revenue grew at an average compounded rate of 10% per year, not 10% in total.")]:
        if key == "fcf_yield" and "sbc_unreported" in str(row.get("dq_flags", "")):
            tip += " Stock compensation is unavailable for this company and was assumed to be zero."
        parts.append(f'<div tabindex="0" title="{html.escape(tip, quote=True)}" aria-label="{html.escape(label + ": " + tip, quote=True)}"><span>{label}</span><b>{fmt(row.get(key), "{:.1%}")}</b></div>')
    parts.append('</div>')
    flags = []
    if any(OP.number(row.get(k)) is not None and OP.number(row.get(k)) < floor
           for k, floor in [("brake_gm", .97), ("brake_fcf_margin", .95), ("brake_roic", .90)]):
        flags.append("Margins ↓")
    if OP.number(row.get("nd_ebitda")) is not None and row.get("nd_ebitda") >= 3:
        flags.append("High debt")
    if OP.number(row.get("fcf_adj_ttm")) is not None and row.get("fcf_adj_ttm") < 0:
        flags.append("Cash burn")
    if OP.number(row.get("input_coverage")) is not None and row.get("input_coverage") < .8:
        flags.append("Limited data")
    concern = html.escape(OP.brief(row)["Main concern"], quote=True)
    parts.append(f'<div class="card-flags" title="{concern if flags else ""}">' + ''.join(f'<span>{flag}</span>' for flag in flags) + '</div>')
    st.markdown(''.join(parts), unsafe_allow_html=True)


def cards(rows, hist, growth=False, all_companies=False):
    ideas = WL.all_ideas()
    for offset in range(0, min(len(rows), 6), 3):
        for col, (_, row) in zip(st.columns(3), rows.iloc[offset:offset+3].iterrows()):
            with col, st.container(border=True, key="stock_card_"+row.ticker):
                badge = "RESEARCH" if all_companies else "GROWTH" if growth else "VALUE"
                tone = "research" if all_companies else "growth" if growth else "value"
                cap = OP.number(row.get("mktcap"))
                cap_label = (f"${cap/1e12:,.2f}T" if cap >= 1e12 else f"${cap/1e9:,.1f}B" if cap >= 1e9 else f"${cap/1e6:,.0f}M") if cap is not None and cap > 0 else "—"
                st.markdown(f'<div class="card-top {tone}"><span class="stock-symbol">{html.escape(row.ticker)}</span>'
                            f'<span class="eyebrow">{badge}</span></div>'
                            f'<div class="card-company"><h3 title="{html.escape(str(row.get("name", "")), quote=True)}">{html.escape(str(row.get("name", "")))}</h3>'
                            f'<div class="card-price-block"><span class="stock-price">{fmt(row.get("price"), "${:,.2f}")}</span>'
                            f'<span class="stock-cap" title="Market capitalization: share price × shares outstanding, from the saved scan.">{cap_label} cap</span></div></div>', unsafe_allow_html=True)
                card_visuals(row, hist)
                saved = ideas.get(row.ticker, {})
                if saved.get("buy_price"):
                    st.caption(f"My buy price {fmt(saved['buy_price'], '${:,.2f}')} · scan price {fmt(row.get('price'), '${:,.2f}')}")
                quote = st.session_state.get("card_quotes", {}).get(row.ticker)
                if quote:
                    if OP.quote_is_stale(quote.get("Fetched")):
                        st.caption("Old quote snapshot · check the premium again before comparing income.")
                    if OP.number(quote.get("Premium")) is not None:
                        st.markdown(f"**{fmt(quote['Premium'], '${:,.0f}')} premium** · {quote['Days']} days · "
                                    f"{fmt(quote['Return on cash'], '{:.2%}')} on cash")
                        st.caption(f"Buy at {fmt(quote['Strike'], '${:,.2f}')} · set aside {fmt(quote['Cash required'], '${:,.0f}')} · "
                                   f"expires {quote['Expiration']} · {quote['Status']}")
                    else:
                        st.caption(quote["Status"])
                    st.caption(f"Quote snapshot: {quote.get('Fetched', 'unknown')} · earnings: {quote.get('Earnings', 'Unknown')}")
                a, b, c = st.columns([1.35, 1, 1])
                a.button("Open →", key="card_"+row.ticker, on_click=pick, args=(row.ticker,), width="stretch",
                         help=f"Explore {row.ticker}: the business, financial charts and options.")
                if b.button("↻ Put" if quote else "Put", key="preview_"+row.ticker, width="stretch",
                            help="Load a put near 30 days using your saved buy price, or a 5% entry discount. Cached for 90 seconds; refresh reloads the chain."):
                    with st.spinner("Loading put preview…"):
                        today = dt.date.today()
                        exps = expiries(row.ticker, today.isoformat())
                        selected, _ = OP.choose_expirations({row.ticker: exps}, today, 30)
                        exp = selected[row.ticker]
                        if exp and quote:
                            chain.clear(row.ticker, exp)
                        raw, fetched = chain(row.ticker, exp) if exp else (None, "")
                        result = OP.put_comparison(row.ticker, raw, exp, today, 5, saved.get("buy_price"), fetched,
                                                   earnings(row.ticker, today.isoformat()) if raw else None)
                        st.session_state.setdefault("card_quotes", {})[row.ticker] = result
                        st.rerun()
                if c.button("Saved ✓" if saved else "Save", disabled=bool(saved) or not st.session_state.get("_ideas_ready", False), key="save_"+row.ticker, width="stretch"):
                    WL.save(row.ticker)
                    st.rerun()



def reset_hunt_zone():
    st.session_state["hunt_zone"] = {"quality": 60, "valuation": 0.}
    st.session_state["hunt_quality_control"] = 60
    st.session_state["hunt_value_control"] = 0.


def discovery_filter_changed():
    st.session_state["map_epoch"] = st.session_state.get("map_epoch", 0) + 1


def reset_discovery_filters():
    for key, value in {"discovery_sector": "All sectors", "min_cap": 0., "max_cap": 0., "min_put_oi": 0, "min_put_volume": 0}.items():
        st.session_state[key] = value
    discovery_filter_changed()


def discovery(metrics, cfg, hist, controls):
    lens, sector, filters = controls
    with lens:
        mode = st.segmented_control("Research lens", ["Quality & value", "Investing for growth", "All companies"],
                                    default="Quality & value", key="lens") or "Quality & value"
    with sector:
        sec = st.selectbox("Sector", ["All sectors"] + sorted(metrics.sector.dropna().unique().tolist()), key="discovery_sector", on_change=discovery_filter_changed)
    with filters:
        candidates = metrics if sec == "All sectors" else metrics[metrics.sector == sec]
        a, b, c, d = st.columns(4)
        minimum = a.number_input("Min market cap · $B", min_value=0., value=0., step=5., key="min_cap", on_change=discovery_filter_changed, help="0 means no minimum. 10 means a $10 billion company.")
        maximum = b.number_input("Max market cap · $B", min_value=0., value=0., step=5., key="max_cap", on_change=discovery_filter_changed, help="0 means no maximum.")
        min_oi = c.number_input("Min put open interest", min_value=0, value=0, step=100, key="min_put_oi", on_change=discovery_filter_changed, help="Outstanding contracts at one strike. 0 disables this filter.")
        min_volume = d.number_input("Min put volume", min_value=0, value=0, step=10, key="min_put_volume", on_change=discovery_filter_changed, help="Reported trading volume at one strike. 0 disables this filter; missing volume is not treated as zero.")
        if maximum and maximum < minimum:
            st.warning("Maximum market cap must be at least the minimum, or 0 for no maximum.")
            return
        candidates = DF.size_filter(candidates, minimum, maximum)
        snapshots = st.session_state.setdefault("discovery_options", {})
        check, reset, summary = st.columns([1, 1, 2])
        if check.button("Check options activity", disabled=candidates.empty, help="Fetches one put expiry per company, closest to 30 days within the next 90 days."):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            progress = st.progress(0., text=f"Checking {len(candidates)} companies…")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(DF.fetch_activity, ticker): ticker for ticker in candidates.ticker}
                for n, future in enumerate(as_completed(futures), 1):
                    snapshots[futures[future]] = future.result()
                    progress.progress(n/len(futures), text=f"Checked {n}/{len(futures)} companies")
            progress.empty()
            discovery_filter_changed()
        reset.button("Reset filters", on_click=reset_discovery_filters)
        universe = DF.apply_activity(candidates, snapshots, min_oi, min_volume)
        summary.caption(f"{len(universe)} / {len(metrics)} companies match · search remains unrestricted")
        if min_oi or min_volume or snapshots:
            statuses = [DF.activity_status(snapshots.get(t), min_oi, min_volume) for t in candidates.ticker]
            unknown = sum(status in {"Not checked", "Stale", "Unavailable"} for status in statuses)
            st.caption(f"Options: one expiry nearest 30 days (within 90), strikes at or up to 20% below the share price. Both minimums must be met at the same strike. Checks expire after 15 minutes. {unknown} companies need fresh or available data." )
            if (min_oi or min_volume) and unknown:
                st.info("Options filters include only verified matches. Use Check options activity to check the current index, sector and size range.")
    zone = st.session_state.setdefault("hunt_zone", {"quality": 60, "valuation": 0.})
    if mode != "Investing for growth":
        with st.expander("Adjust hunt zone"):
            a, b = st.columns(2)
            quality_min = a.slider("Minimum business quality · Y", 0, 100, int(zone["quality"]), step=1,
                                   format="%d%%", key="hunt_quality_control", help="Move the horizontal line. Higher requires a stronger business-quality score.")
            valuation_min = b.slider("Minimum valuation advantage · X", -2., 2., float(zone["valuation"]), step=.05,
                                     key="hunt_value_control", help="Move the vertical line. Higher requires a cheaper valuation relative to quality. Zero is the model's fair-value line; negative values include pricier companies. Model units, not a percentage discount.")
            st.session_state["hunt_zone"] = {"quality": quality_min, "valuation": valuation_min}
            st.caption("Session-only exploration. Updates the zone, shortlist, cards and comparison; cash-flow, return-on-capital and data checks still apply.")
            st.button("Reset hunt zone", on_click=reset_hunt_zone)
    else:
        quality_min, valuation_min = zone["quality"], zone["valuation"]
    hi = SC.highlighted(metrics, gs10=cfg.get("gs10"), quality_min=quality_min, max_residual=-valuation_min)
    hi = hi[hi.ticker.isin(universe.ticker)].copy()
    hi["options_activity"] = hi.ticker.map(universe.set_index("ticker").options_activity)
    growth = OP.research_universe(universe)
    rows = growth if mode == "Investing for growth" else universe.sort_values("ticker") if mode == "All companies" else hi
    a, b, c = st.columns(3)
    a.metric("Businesses to explore", len(universe))
    b.metric("Quality & value shortlist", len(hi))
    c.metric("Investing for growth", len(growth))
    if mode == "Investing for growth":
        st.caption("Research filter: ≥5% annual revenue growth over five years, positive cash under the depreciation-as-upkeep proxy, "
                   "and a proxy cash gap ≥2% of revenue. Ranked by that gap. These are investment-heavy businesses to investigate, not quality endorsements.")
    with st.container(border=True):
        heading("01 / EXPLORE", "The hunt map")
        business_view = "Growth economics" if mode == "Investing for growth" else "Business value"
        view = st.segmented_control("Map view", [business_view, "Put income"], default=business_view, key="map_view")
        if view == "Put income":
            income_comparison(rows, cfg, show_chart=True)
        else:
            if mode == "Investing for growth":
                growth_map(growth)
            else:
                hunt_map(universe, hi, quality_min, valuation_min)
            with st.expander("Compare put income for these ideas"):
                income_comparison(rows, cfg, show_chart=False)
    heading("02 / YOUR NEXT LOOK", mode, "Six-month prices, quality and cheapness at a glance. Hover over scores and flags for details.")
    if rows.empty:
        st.info("No companies match this view. Adjust the filters, check options activity, or search any company above.")
    else:
        cards(rows, hist, mode == "Investing for growth", mode == "All companies")
        with st.expander(f"Compare all {len(rows)} companies", expanded=True):
            cols = {"ticker": "Stock", "name": "Company", "price": "Scan share price", "display_quality_score": "Business quality", "display_cheapness_score": "Cheapness",
                    "rev_cagr5": "Revenue growth · 5y", "fcf_yield": "Reported cash yield", "mktcap": "Market cap · $B"}
            comparison = rows[list(cols)].rename(columns=cols).reset_index(drop=True)
            comparison["Market cap · $B"] = comparison["Market cap · $B"] / 1e9
            comparison["Business quality"] = comparison["Business quality"] / 100
            comparison["Cheapness"] = comparison["Cheapness"] / 100
            event = st.dataframe(comparison, hide_index=True,
                width="stretch", on_select="rerun", selection_mode="single-row", key=f"compare_stocks_{mode}_{sec}_{quality_min}_{valuation_min}_{st.session_state.get('map_epoch', 0)}",
                column_config={"Market cap · $B": st.column_config.NumberColumn(format="$%.1fB"), "Scan share price": st.column_config.NumberColumn(format="$%.2f"),
                               "Business quality": st.column_config.NumberColumn(format="percent",
                                   help="Business quality score expressed as a percentage of 100. 76% means 76/100; this is not an expected return or probability."),
                               "Cheapness": st.column_config.NumberColumn(format="percent", help="Score out of 100. Estimates use partial data; open the company for the score basis."),
                               "Revenue growth · 5y": st.column_config.NumberColumn(format="percent"),
                               "Reported cash yield": st.column_config.NumberColumn(format="percent")})
            if event.selection.rows:
                pick(rows.iloc[event.selection.rows[0]].ticker)
                st.rerun()
    with st.expander("How the shortlist works"):
        if cfg.get("financial_version") != SC_CONFIG.FINANCIAL_VERSION:
            st.warning("Saved scores use older financial calculations. Refresh to apply the debt, share-count and leverage corrections.")
        st.caption("Maturity check: " + cfg.get("maturity_basis", "index tenure >=1y") + ". Financials, utilities and real estate are excluded from index scans.")
        st.write(f"Quality combines profitability, growth, debt and consistency. Your current shortlist requires quality ≥ {quality_min}/100, "
                 f"adequate data, return on capital ≥ 10%, valuation advantage > {valuation_min:.2f}, and a cash yield above "
                 "the higher of 4% and the scan’s 10-year Treasury yield. Reported cash deducts capital spending "
                 "and stock compensation. Option premiums do not change this business shortlist.")



def business(row, hist, cfg):
    flags = str(row.get("dq_flags", ""))
    if "sbc_unreported" in flags:
        st.caption("Stock-pay data is unavailable; the saved cash adjustment assumes zero stock compensation.")
    if "shares_was_proxy" in flags:
        st.caption("Valuation uses a proxy share count. Check it against the latest filing before setting an entry price.")
    if "facts_yf" in flags:
        st.caption("Company metrics use provider statement estimates; the financial-history charts below use SEC-reported figures.")
    if cfg.get("financial_version") != SC_CONFIG.FINANCIAL_VERSION:
        st.warning("This saved scan predates corrections to debt extraction, share-count fallbacks and leverage scoring. Refresh data & scores before relying on its valuation or quality scores.")
    a, b, c, d = st.columns(4)
    a.metric("Business quality", fmt(row.get("display_quality_score", row.get("quality_score")), "{:.0f} / 100") + (" *" if row.get("quality_score_status") == "Estimate" else ""),
             help=row.get("quality_score_note", "Business quality score."))
    b.metric("Revenue growth · 5y", fmt(row.get("rev_cagr5"), "{:.1%}"))
    c.metric("Return on capital", fmt(row.get("roic_ttm"), "{:.1%}"))
    ebitda = OP.number(row.get("ebitda_ttm"))
    leverage = row.get("nd_ebitda") if ebitda is None or ebitda > 0 else None
    d.metric("Net debt / EBITDA", fmt(leverage, "{:.1f}×"), help="Debt including leases, less cash and short-term investments, divided by positive EBITDA. The ratio is unavailable for zero or negative EBITDA.")
    if ebitda is not None and ebitda <= 0:
        st.caption("EBITDA is non-positive; a negative ratio would not establish a net-cash position.")
    focus = st.segmented_control("Research question", ["Price & value", "Growth & margins", "Cash & investment", "Debt & dilution"],
                                 default="Price & value", key="research_focus") or "Price & value"
    if focus == "Price & value":
        a, b = st.columns(2)
        a.metric("Cash after capex & stock pay", fmt(OP.number(row.get("fcf_adj_ttm"))/1e9 if OP.number(row.get("fcf_adj_ttm")) is not None else None, "${:,.2f}B"))
        b.metric("Cash after stock pay / EV", fmt(row.get("fcf_yield"), "{:.1%}"))
        st.caption("Trailing-year cash after capital spending and stock compensation. Cash yield describes the business, not a payout to shareholders.")
        h = hist[hist.ticker == row.ticker].sort_values("week") if not hist.empty else pd.DataFrame()
        if not h.empty:
            a, b = st.columns(2)
            for col, title, key, ytitle in [(a, "Share price & my entry", "price", "$ per share"),
                                           (b, "Valuation against its own history", "ev_fcf", "Business value / free cash flow")]:
                fig = go.Figure()
                if key in h and h[key].notna().any():
                    fig.add_trace(go.Scatter(x=h.week, y=h[key], name="Weekly close" if key == "price" else "EV / free cash flow", line=dict(width=2.5)))
                    saved = WL.all_ideas().get(row.ticker, {}).get("buy_price")
                    if key == "price" and saved:
                        fig.add_hline(y=saved, line_dash="dot", line_color="#287650", annotation_text=f"My buy price ${saved:,.2f}")
                    elif key == "ev_fcf":
                        fig.add_hline(y=float(h[key].median()), line_dash="dot", line_color=SECONDARY, annotation_text="5y median")
                    fig.update_layout(title=title, yaxis_title=ytitle)
                    col.plotly_chart(chart_style(fig), width="stretch")
                else:
                    col.caption(f"{title}: history unavailable.")
        else:
            st.info("No price or valuation history available in this scan.")
    else:
        economic_charts(row, cfg, focus)
    with st.container(border=True):
        st.subheader("Valuation, margins & data details")
        labels = {"fcf_yield": "Cash after stock pay / EV", "ev_fcf": "Business value / cash flow",
                  "pe_ttm": "Price / earnings", "gm_ttm": "Gross margin", "fcf_margin_ttm": "Cash-flow margin",
                  "residual": "Valuation model residual", "input_coverage": "Data coverage"}
        st.dataframe(pd.DataFrame([{"Measure": label, "Value": fmt(row.get(k), "{:.1%}" if k in
            ["fcf_yield", "gm_ttm", "fcf_margin_ttm", "input_coverage"] else "{:.2f}")} for k, label in labels.items()]), hide_index=True, width="stretch")
        q, c = st.columns(2)
        q.metric("Quality score", fmt(row.get("display_quality_score"), "{:.0f}%"), delta=row.get("quality_score_status", "Unavailable"), delta_color="off")
        c.metric("Cheapness score", fmt(row.get("display_cheapness_score"), "{:.0f}%"), delta=row.get("cheapness_score_status", "Unavailable"), delta_color="off")
        st.caption("Quality: " + str(row.get("quality_score_note", "Unavailable")))
        st.caption("Cheapness: " + str(row.get("cheapness_score_note", "Unavailable")))
        st.caption(f"Scan eligibility notes: {row.get('gate_fail_reasons', 'Unavailable')}")



def options(row, cfg):
    ticker = row.ticker
    today = dt.date.today()
    heading("PUT INCOME / UP TO 90 DAYS", "Get paid to name your entry price.",
            "Compare the premium, the cash you commit, and your cost if you receive 100 shares.")
    with st.spinner("Checking available expirations…"):
        exps = expiries(ticker, today.isoformat())
    if not exps:
        st.info("No option expirations available in the next 90 days. Try again later or explore another company.")
        return
    saved_buy = WL.all_ideas().get(ticker, {}).get("buy_price")
    a, b, c = st.columns([2, 1, 1])
    expiry = a.selectbox("Expiration", exps, index=min(range(len(exps)), key=lambda i: abs((dt.date.fromisoformat(exps[i])-today).days-30)),
                       format_func=lambda e: f"{dt.date.fromisoformat(e):%b %d, %Y} · {(dt.date.fromisoformat(e)-today).days} days", key=f"expiry_{ticker}")
    target = b.number_input("Entry price · % of share price", min_value=50., max_value=110., value=95., step=1., key=f"opt_target_{ticker}",
                            disabled=bool(saved_buy and st.session_state.get(f"ceiling_{ticker}", True) and st.session_state.get(f"side_{ticker}", "Sell a put") == "Sell a put"))
    side = c.selectbox("I’m looking to", ["Sell a put", "Sell a covered call"], key=f"side_{ticker}")
    raw, fetched = chain(ticker, expiry)
    spot = OP.number(raw.get("underlying_price")) if raw else None
    st.caption(f"Chain fetched {fetched} · share price {fmt(spot, '${:,.2f}')} from the same response · "
               f"share quote time: {raw.get('underlying_time') or 'unavailable' if raw else 'unavailable'}. Provider quotes may be delayed.")
    if st.button("Refresh option quotes", key="refresh_quotes"):
        chain.clear(); expiries.clear(); earnings.clear(); st.rerun()
    is_put = side == "Sell a put"
    frame = raw.get("puts" if is_put else "calls", pd.DataFrame()) if raw else pd.DataFrame()
    if frame.empty:
        st.info("No quotes available for this expiration. Choose another date.")
        return
    if spot is None or spot <= 0:
        st.info("The underlying share quote is unavailable. Refresh or try another expiration. Share-price comparisons are paused; the raw chain remains available below.")
        st.dataframe(frame, hide_index=True, width="stretch")
        return
    frame = frame[frame.strike > 0].sort_values("strike").reset_index(drop=True)
    if frame.empty:
        st.info("No valid strikes for this expiration.")
        return
    ceiling = saved_buy if is_put and saved_buy and st.toggle("Use my buy price as the strike ceiling", value=True, key=f"ceiling_{ticker}") else None
    if ceiling:
        frame = frame[frame.strike <= ceiling].reset_index(drop=True)
        st.caption(f"Entry selection is limited to my saved buy price of ${ceiling:,.2f}. The chain table can show strikes above this ceiling.")
        if frame.empty:
            st.info("No listed strike is at or below your buy price. Turn off the ceiling to inspect the full chain.")
            return
    strikes = frame.strike.tolist()
    target_price = ceiling or spot * target / 100
    target_signature = f"{ticker}_{expiry}_{side}_{target}_{ceiling}"
    strike = st.selectbox("Buy at this price" if is_put else "Sell shares at this price", strikes,
            index=min(range(len(strikes)), key=lambda i: abs(strikes[i] - target_price)),
            format_func=lambda k: f"${k:,.2f}", key="strike_"+target_signature)
    q = frame[frame.strike == strike].iloc[0]
    bid, ask = MD._n(q.get("bid")), MD._n(q.get("ask"))
    # Never present a last trade or midpoint as income available to collect.
    premium, quote_status = OP.bid_quote(q)
    days = (dt.date.fromisoformat(expiry)-today).days
    if premium is None:
        st.warning(f"{quote_status} for this contract. Income and net cost are unavailable; choose another strike or refresh.")
    elif quote_status == "Wide spread":
        st.warning("Wide bid–ask spread. The displayed income uses the bid; execution is uncertain.")
    elif quote_status == "Ask unavailable":
        st.caption("Ask unavailable; the bid is shown, but the spread cannot be checked.")
    a, b, c, d = st.columns(4)
    a.metric("Premium · 1 contract", fmt(premium * 100 if premium is not None else None, "${:,.0f}"))
    b.metric("Return on committed cash" if is_put else "Premium / share value", fmt(premium / (strike if is_put else spot) if premium is not None else None, "{:.2%}"))
    c.metric("Cash to set aside" if is_put else "Shares required", fmt(strike*100, "${:,.0f}") if is_put else "100")
    basis = strike-premium if premium is not None else None
    d.metric("Net cost if assigned" if is_put else "Sale price + premium", fmt(basis if is_put else strike+premium if premium is not None else None, "${:,.2f}"))
    st.caption(f"Premium / fetched share price: {fmt(premium / spot if premium is not None else None, '{:.2%}')} · "
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
            cb, call_status = OP.bid_quote(call) if call is not None else (None, "No call")
            st.write(f"Today’s call at the same ${strike:,.2f} strike has a ${cb:,.2f} bid per share ({call_status.lower()})."
                     if cb is not None and cb > 0 else "No positive same-strike call bid available for this expiration.")
            st.caption("This is today’s comparison quote. A call sold after assignment will have a different expiration and premium. "
                       "Selling at or above your assignment price can avoid a share-price loss if called away; a buyer and a useful premium are not guaranteed.")
    else:
        cost = st.number_input("Your assignment price / original cost per share", min_value=0., value=float(spot), step=1., key=f"cost_{ticker}")
        net = (strike-cost+premium)*100 if premium is not None else None
        st.metric("Share gain / loss + this premium if called away", fmt(net, "${:,.2f}"))
        st.caption("Assumes you already own 100 shares. Excludes prior premiums, fees and taxes. A covered call limits gains above the strike.")
    with st.container(border=True):
        st.subheader("Options chain & quote details")
        view = st.selectbox("Strike range", ["Near the money (±10%)", "Near the money (±20%)", "All strikes"], key=f"chain_range_{ticker}")
        show = raw.get("puts" if is_put else "calls", pd.DataFrame()).copy()
        show = show[show.strike > 0].sort_values("strike")
        band = {"Near the money (±10%)": .10, "Near the money (±20%)": .20}.get(view)
        if band is not None:
            show = show[show.strike.between(spot * (1-band), spot * (1+band))]
        validated = [OP.bid_quote(r) for _, r in show.iterrows()]
        show["Premium / share price"] = [bid/spot if bid is not None else None for bid, _ in validated]
        visible = show[[c for c in ["strike", "bid", "ask", "Premium / share price", "volume", "oi", "iv"] if c in show]]
        def shade_contract(r):
            k = r["strike"]
            background = "#edf3fc" if k == spot else "#fff0dc" if (k > spot if is_put else k < spot) else "#ffffff"
            return [f"background-color: {background}; color: #292b30" for _ in r]
        if visible.empty:
            st.info("No strikes in this range. Select All strikes to see the available chain.")
        # fit every row: header ~38px + ~35px per row (the page scrolls
        # instead of an inner table scrollbar)
        st.dataframe(visible.style.apply(shade_contract, axis=1),
                     hide_index=True, width="stretch",
                     height=max(120, 38 + 35 * len(visible)),
                     column_config={"Premium / share price": st.column_config.NumberColumn(format="percent")})
        st.caption("Orange = in the money · blue = at the money · white = out of the money, relative to the fetched share price.")
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
    for col, (label, evidence) in zip(st.columns(3), OP.brief(row).items()):
        with col, st.container(border=True):
            st.markdown(f"**{label}**")
            st.write(evidence)
    saved_editor(row)
    tab = st.segmented_control("Company workspace", ["The business", "Put & call income"], default="The business", key="company_tab")
    if tab == "Put & call income":
        options(row, cfg)
    else:
        business(row, hist, cfg)


def universe_changed():
    for key in ("company_search", "idea_pick", "hunt_event_seen"):
        st.session_state.pop(key, None)
    st.session_state["next_workspace"] = "Discover"
    st.session_state["map_epoch"] = st.session_state.get("map_epoch", 0) + 1


def main():
    WL.sync()
    if "pending_hunt_zone" in st.session_state:
        zone = st.session_state.pop("pending_hunt_zone")
        st.session_state["hunt_zone"] = zone
        st.session_state["hunt_quality_control"] = zone["quality"]
        st.session_state["hunt_value_control"] = zone["valuation"]
    if "next_workspace" in st.session_state:
        st.session_state["workspace"] = st.session_state.pop("next_workspace")
        st.session_state.pop("company_search", None)
    with st.sidebar:
        st.markdown('<div class="brand">◈ hunt<span>THE STOCK & INCOME FINDER</span></div>', unsafe_allow_html=True)
        page = st.radio("Workspace", ["Discover", "Saved ideas", "Company", "Data & refresh"], key="workspace", label_visibility="collapsed")
        st.markdown('<div class="sidebar-note"><b>A stock-first approach</b><br><br>01 &nbsp; Find a business worth owning.<br><br>'
                    '02 &nbsp; Choose your price. Sell a put.<br><br>03 &nbsp; If assigned, hold or sell calls.</div>', unsafe_allow_html=True)
        st.caption("Research workspace · 0–90 day options")
        st.markdown(f'<div class="sidebar-version">v{_APP_VERSION} · {_git_commit()}</div>',
                    unsafe_allow_html=True)
    if page == "Data & refresh":
        from scanner.refresh import SCOPES
        selected_universe = st.selectbox("What to refresh", list(SCOPES), format_func=SCOPES.get, key="refresh_universe")
        import data_manager
        data_manager.render(selected_universe)
        return
    # Search is independent of the discovery filter and includes exchange listings.
    sources = {}
    references = []
    for universe_id in UNIVERSES:
        saved = available_scans(ROOT / "data/scans", universe_id)
        if saved:
            path = saved[-1]
            frame, source_cfg, _ = load_scan(str(path), (path / "config.json").stat().st_mtime_ns)
            references.append(frame)
            for ticker in frame.ticker:
                if ticker not in sources or source_cfg.get("asof", "") > sources[ticker][1].get("asof", ""):
                    sources[ticker] = (path, source_cfg)
    reference = pd.concat(references, ignore_index=True).drop_duplicates("ticker") if references else pd.DataFrame()
    try:
        directory = listing_directory()
    except Exception:
        directory = pd.DataFrame(columns=["ticker", "name", "cik", "exchange"])
        st.caption("Exchange search is temporarily unavailable. Saved companies are still searchable.")
    names = directory.set_index("ticker")["name"].to_dict()
    if not reference.empty:
        names.update(reference.set_index("ticker")["name"].to_dict())
    st.markdown(f'<div class="topline">RESEARCH DESK <span> / {html.escape(page.upper())}</span></div>', unsafe_allow_html=True)
    search = st.selectbox("Find a company · NYSE & Nasdaq", sorted(names), index=None,
                          placeholder="Search any company or ticker — independent of the discovery universe",
                          format_func=lambda t: f"{t} · {names[t]}", key="company_search")
    if search and search != st.session_state.get("idea_pick"):
        pick(search)
        st.rerun()
    if page == "Company":
        ticker = st.session_state.get("idea_pick")
        manual_cfg = CL.snapshot_path(ticker) / "config.json" if ticker in names else None
        newer_manual = manual_cfg is not None and manual_cfg.exists() and (ticker not in sources or manual_cfg.stat().st_mtime_ns > (sources[ticker][0] / "config.json").stat().st_mtime_ns)
        if ticker in sources and not newer_manual:
            path, _ = sources[ticker]
            frame, cfg, hist = load_scan(str(path), (path / "config.json").stat().st_mtime_ns)
            company(frame, cfg, hist)
        elif ticker in names:
            path = CL.snapshot_path(ticker)
            if not (path / "config.json").exists():
                listing = directory[directory.ticker == ticker].iloc[0].to_dict()
                try:
                    with st.spinner(f"Loading {ticker} financials and price history…"):
                        CL.build_snapshot(listing)
                except Exception as exc:
                    st.error(f"Could not load {ticker}: {exc}")
                    if st.button("Try again"):
                        st.rerun()
                    return
            raw = pd.read_csv(path / "metrics.csv")
            frame = CL.score_snapshot(raw, reference)
            if frame.quality_score_status.iloc[0] == "Not supported":
                st.info(str(frame.quality_score_note.iloc[0]))
            cfg = json.loads((path / "config.json").read_text())
            hist = pd.read_parquet(path / "history.parquet")
            st.caption(f"On-demand research · {cfg['asof']} · company research")
            company(frame, cfg, hist)
        else:
            st.info("Search for a company above to start exploring.")
        return
    if page == "Saved ideas":
        manual_rows = []
        for ticker in WL.all_ideas():
            if ticker not in sources:
                snapshot = CL.snapshot_path(ticker)
                if (snapshot / "config.json").exists():
                    manual_rows.append(pd.read_csv(snapshot / "metrics.csv"))
        if manual_rows:
            reference = pd.concat([reference, *manual_rows], ignore_index=True)
        if reference.empty:
            st.info("Build a scan to populate your research desk.")
        else:
            cfg = next(iter(sources.values()))[1] if sources else {"asof": dt.date.today().isoformat()}
            saved_page(reference, cfg, pd.DataFrame())
        return
    heading("STOCKS FIRST. INCOME SECOND.", "Good businesses. Better entry prices.",
            "Find a business, save your buy price, and compare the income available while you wait.")
    lens_control = st.container()
    filters = st.expander("Filters", expanded=False)
    with filters:
        universe_control, sector_control = st.columns(2)
    selected_universe = universe_control.selectbox("Discovery universe", list(UNIVERSES), format_func=UNIVERSES.get,
                                      key="stock_universe", on_change=universe_changed,
                                      help="Applies to discovery, maps and comparisons. Company search covers NYSE and Nasdaq independently.")
    scans = available_scans(ROOT / "data/scans", selected_universe)
    if not scans:
        st.info(f"{UNIVERSES[selected_universe]} has no saved scan yet. Build it to load company scores, charts and option research.")
        if st.button("Set up this universe", type="primary"):
            st.session_state["refresh_universe"] = selected_universe
            st.session_state["next_workspace"] = "Data & refresh"
            st.rerun()
        return
    path = scans[-1]
    metrics, cfg, hist = load_scan(str(path), (path / "config.json").stat().st_mtime_ns)
    st.caption(f"Scan {cfg.get('asof', path.name)} · {UNIVERSES[selected_universe]} · {len(metrics)} companies")
    discovery(metrics, cfg, hist, (lens_control, sector_control, filters))


@st.cache_data(ttl=3600, show_spinner=False)
def listing_directory():
    return CL.company_directory()


@st.cache_data(show_spinner=False)
def research(path, asof, stamp):
    from scanner.company_research import cached_research
    return cached_research(path, asof)


def economic_charts(row, cfg, focus):
    cik = OP.number(row.get("cik"))
    path = ROOT / "data/raw/sec" / f"CIK{int(cik):010d}.json" if cik is not None else None
    if path is None or not path.exists():
        st.info("Detailed operating history is unavailable in the local SEC cache. Data & refresh can download it.")
        return
    with st.spinner("Reading the company’s financial history…"):
        flows, balance = research(str(path), cfg.get("asof", dt.date.today().isoformat()), path.stat().st_mtime_ns)
    flows, balance = flows.copy(), balance.copy()
    if flows.empty and balance.empty:
        st.info("No detailed financial history available for this company.")
        return
    if "revenue" in flows:
        for key, target in [("ebit", "Operating margin"), ("sbc", "Stock pay / revenue")]:
            if key in flows:
                flows[target] = flows[key]/flows.revenue.where(flows.revenue > 0)*100
    if focus == "Growth & margins":
        panels = [(flows, "Is the business growing?", [("revenue", "Revenue"), ("ebit", "Operating profit")], False),
                  (flows, "Is growth becoming more profitable?", [("Operating margin", "Operating margin")], True)]
    elif focus == "Cash & investment":
        panels = [(flows, "Can operations fund investment?", [("cfo", "Operating cash"), ("capex", "Capital spending")], False),
                  (flows, "Cash after investment", [("fcf_adj", "After all capex & stock pay"), ("fcf_owner", "Depreciation-as-upkeep proxy")], False)]
        st.caption("The proxy substitutes depreciation for upkeep spending. It can overstate available cash. Capital spending includes upkeep and expansion; acquisitions are excluded.")
    else:
        panels = [(balance, "Borrowings & cash reserves", [("debt", "Borrowings, excluding leases"), ("leases", "Lease liabilities"), ("cash", "Cash & equivalents")], False),
                  (flows, "How much revenue goes to stock pay?", [("Stock pay / revenue", "Stock compensation / revenue")], True)]
        st.caption("Stock compensation is an economic cost, not the same as net share dilution; buybacks and issuance also affect share count.")
    for col, (frame, title, fields, percent) in zip(st.columns(2), panels):
        fig = go.Figure()
        for field, label in fields:
            if field in frame and frame[field].notna().any():
                fig.add_trace(go.Scatter(x=frame.effective, y=frame[field]/(1 if percent else 1e9), name=label,
                                        line=dict(width=2.5, shape="hv"), connectgaps=False))
        if fig.data:
            fig.update_layout(title=title, yaxis_title="% of revenue" if percent else "$ billions")
            col.plotly_chart(chart_style(fig, 360), width="stretch")
        else:
            col.caption(f"{title}: history unavailable.")
    if focus == "Debt & dilution":
        dilution = go.Figure()
        for key, label in [("shares_basic_was", "Basic average shares"), ("shares_diluted_was", "Diluted average shares")]:
            if key in flows and flows[key].notna().any():
                dilution.add_trace(go.Scatter(x=flows.effective, y=flows[key]/1e6, name=label, line=dict(shape="hv")))
        if dilution.data:
            dilution.update_layout(title="Is the share count growing?", yaxis_title="Million shares")
            st.plotly_chart(chart_style(dilution, 340), width="stretch")
            st.caption("Latest reported period-average share counts, not a sum of quarters. Splits, issuance and buybacks can all affect this chart.")
    from scanner.company_research import financial_snapshot
    current = financial_snapshot(flows, cfg.get("asof", dt.date.today().isoformat()))
    if not current.empty:
        if focus == "Cash & investment":
            values = current.set_index("field")
            required = ["cfo", "capex", "sbc"]
            if all(k in values.index and pd.notna(values.at[k, "Value"]) for k in required) and values.loc[required, "Period ending"].nunique() == 1:
                amounts = [values.at[k, "Value"] / 1e9 for k in required]
                bridge = go.Figure(go.Waterfall(x=["Operating cash", "Capital spending", "Stock pay", "Cash remaining"],
                    measure=["absolute", "relative", "relative", "total"], y=[amounts[0], -amounts[1], -amounts[2], 0],
                    increasing=dict(marker_color="#3569b0"), decreasing=dict(marker_color="#cf893f"), totals=dict(marker_color="#287650")))
                bridge.update_layout(title="Where the operating cash goes", yaxis_title="$ billions", showlegend=False)
                st.plotly_chart(chart_style(bridge, 340), width="stretch")
                st.caption("Stock pay is a non-cash economic adjustment. Free cash flow before that adjustment is operating cash minus capital spending; the D&A proxy is not spendable cash.")
            else:
                st.caption("Cash breakdown needs operating cash, capex and stock pay for the same trailing-year period.")
        st.markdown("**Financial figures · trailing year**")
        table = current.drop(columns="field").copy()
        table["Value"] = table.Value / 1e9
        table = table.rename(columns={"Value": "$ billions"})
        st.dataframe(table, hide_index=True, width="stretch", column_config={"$ billions": st.column_config.NumberColumn(format="$%.2fB")})
    st.caption("Source: cached SEC filings, restricted to the scan date. Operating figures cover a trailing year; dates mark when filings became available.")



def income_comparison(rows, cfg, show_chart=True):
    st.markdown("**Put income at your entry price**")
    if rows.empty:
        st.info("Choose a research view with companies to compare, or save a company from search.")
        return
    ideas = WL.all_ideas()
    choices = rows.ticker.tolist()
    context = "saved" if st.session_state.get("workspace") == "Saved ideas" else str(st.session_state.get("lens"))
    chosen = st.multiselect("Companies to compare", choices, default=choices[:3], max_selections=6, key="income_companies_"+context)
    a, b, c = st.columns(3)
    days = a.select_slider("Target duration", [7, 14, 30, 45, 60, 90], value=30)
    discount = b.slider("Entry discount", 0, 20, 5, format="%d%%")
    budget = c.number_input("Cash available per contract", min_value=0, value=25000, step=1000)
    use_saved = st.toggle("Use my saved buy prices", value=True, help="Treats your saved price as a strike ceiling. Stocks without a saved price use the entry discount.")
    buy_prices = {t: ideas.get(t, {}).get("buy_price") if use_saved else None for t in chosen}
    signature = (tuple(chosen), days, discount, tuple(buy_prices.items()), cfg.get("asof"), dt.date.today().isoformat())
    if st.button("Compare put income", disabled=not chosen):
        results = []
        today = dt.date.today()
        progress = st.progress(0, text="Finding a shared expiration…")
        by_ticker = {t: expiries(t, today.isoformat()) for t in chosen}
        selected, shared = OP.choose_expirations(by_ticker, today, days)
        for i, ticker in enumerate(chosen):
            exp = selected[ticker]
            raw, fetched = chain(ticker, exp) if exp else (None, "")
            item = OP.put_comparison(ticker, raw, exp, today, discount, buy_prices[ticker], fetched,
                                     earnings(ticker, today.isoformat()) if raw else None)
            results.append(item)
            st.session_state.setdefault("card_quotes", {})[ticker] = item
            progress.progress((i+1)/len(chosen), text=f"Checked {ticker}")
        progress.empty()
        st.session_state["income_results"] = (signature, results, shared)
    saved = st.session_state.get("income_results")
    if saved and saved[0] == signature:
        result = pd.DataFrame(saved[1])
        if any(OP.quote_is_stale(item.get("Fetched")) for item in saved[1]):
            st.warning("These are old quote snapshots. Run Compare put income again for updated quotes.")
        st.caption("Using one shared expiration for every available chain." if saved[2] else
                   "No expiration is shared by all available chains. Each uses its nearest duration; compare the Days column.")
        if "Cash required" in result:
            result = result[result["Cash required"].isna() | (result["Cash required"] <= budget)]
        if "Return on cash" in result:
            result = result.sort_values("Return on cash", ascending=False, na_position="last")
        if result.empty:
            st.info("No quoted contracts fit this cash budget. Raise it or compare lower-priced stocks.")
        else:
            if show_chart:
                income_map(result)
            priority = ["Stock", "Return on cash", "Premium", "Cash required", "Days", "Expiration", "Strike", "Net entry", "Earnings", "Status"]
            order = [c for c in priority if c in result] + [c for c in result if c not in priority]
            st.dataframe(result[order], hide_index=True, width="stretch", column_config={
                **{k: st.column_config.NumberColumn(format="percent") for k in ["Premium / stock", "Entry discount"]},
                **{k: st.column_config.NumberColumn(format="$%.2f") for k in ["Share price", "My buy price", "Strike", "Net entry"]},
                "Return on cash": st.column_config.NumberColumn("Premium / cash committed", format="percent",
                    help="Bid premium ÷ strike. For one standard 100-share contract: total premium ÷ (strike × 100). This is the return for the displayed contract period, not an annual rate."),
                "Premium": st.column_config.NumberColumn("Premium received", format="$%.2f", help="Bid × 100 shares, before fees."),
                "Cash required": st.column_config.NumberColumn("Cash committed", format="$%.2f", help="Strike × 100 shares: the full cash reserved to buy the shares if assigned. Premium is not deducted from this denominator.")})
            st.caption("Premium / cash committed = premium received ÷ cash reserved for assignment (strike × 100). "
                       "For example, $200 premium on $10,000 committed = 2%, or $20 per $1,000, over the displayed number of days. Before fees; not annualized.")
            st.caption("Sorted by premium / cash committed. Share prices come from the same chain response; "
                       "provider quotes may be delayed. Fetch time and share quote time are separate. Click Compare again to refresh; responses are cached for 90 seconds.")
    else:
        st.caption("Choose companies and load quotes to compare entry discounts, income, and earnings dates.")



def growth_map(rows):
    pts = rows.dropna(subset=["rev_cagr5", "operating_margin"])
    if pts.empty:
        st.info("No growth candidates have enough data for this map. All candidates remain in the list below.")
        return
    fig = go.Figure()
    debt = pd.to_numeric(pts.nd_ebitda, errors="coerce")
    for mask, name, color in [(debt < 3, "Net debt below 3× EBITDA", ACCENT),
                               (debt >= 3, "Net debt at least 3× EBITDA", SECONDARY),
                               (debt.isna(), "Debt measure unavailable", "#aab0b8")]:
        p = pts[mask]
        fig.add_trace(go.Scatter(x=p.rev_cagr5*100, y=p.operating_margin*100, mode="markers", name=name,
            customdata=p[["ticker", "investment_gap", "nd_ebitda"]].values,
            marker=dict(size=10+(p.investment_gap.clip(0, .3)*60), color=color, opacity=.8, line=dict(width=1, color="white")),
            hovertemplate="<b>%{customdata[0]}</b><br>Revenue growth: %{x:.1f}%<br>Operating margin: %{y:.1f}%<br>Reinvestment proxy gap: %{customdata[1]:.1%} of revenue<br>Net debt / EBITDA: %{customdata[2]:.1f}×<extra></extra>"))
    fig.update_layout(xaxis_title="Annual revenue growth · 5 years (%)", yaxis_title="Operating margin (%)")
    fig.add_hline(y=0, line_dash="dot", line_color="#c4c7cd")
    event = st.plotly_chart(chart_style(fig, 615), width="stretch", on_select="rerun", selection_mode="points",
                            key=f"growth_map_{st.session_state.get('map_epoch', 0)}")
    if event.selection.points:
        pick(event.selection.points[0]["customdata"][0]); st.rerun()
    st.caption("Larger dots indicate a larger gap between proxy cash and reported cash, relative to revenue; size is capped at 30%. "
               "The gap estimates spending above depreciation, not proven growth investment. Color shows leverage, not a quality verdict.")


def income_map(result):
    if not {"Entry discount", "Return on cash"} <= set(result.columns):
        st.info("No usable quotes to plot yet. Availability details are shown below.")
        return
    pts = result.dropna(subset=["Entry discount", "Return on cash"])
    fig = go.Figure()
    for label, color in [("Before expiry", SECONDARY), ("After expiry", ACCENT), ("Unknown", "#9298a3")]:
        p = pts[pts.Earnings == label]
        fig.add_trace(go.Scatter(x=p["Entry discount"]*100, y=p["Return on cash"]*100,
            text=p.Stock, mode="markers+text", textposition="top center", name=f"Earnings: {label.lower()}",
            customdata=p[["Stock", "Expiration", "Days", "Strike", "Cash required", "Status"]].values,
            marker=dict(size=14, color=color, line=dict(width=1.5, color="white")),
            hovertemplate="<b>%{customdata[0]}</b><br>Expires %{customdata[1]} · %{customdata[2]} days<br>Strike $%{customdata[3]:.2f}<br>Cash $%{customdata[4]:,.0f}<br>%{customdata[5]}<br>Discount %{x:.1f}% · income %{y:.2f}%<extra></extra>"))
    if pts.empty:
        st.info("No usable bids for the income map. See quote availability below.")
        return
    fig.update_layout(xaxis_title="Entry discount to the fetched share price (%)", yaxis_title="Premium / cash committed (%)")
    event = st.plotly_chart(chart_style(fig, 360), width="stretch", on_select="rerun", selection_mode="points",
                            key=f"income_map_{st.session_state.get('map_epoch', 0)}")
    if event.selection.points:
        pick(event.selection.points[0]["customdata"][0])
        st.session_state["company_tab"] = "Put & call income"
        st.rerun()


def saved_editor(row):
    saved = WL.all_ideas().get(row.ticker, {})
    label = f"My buy price: {fmt(saved.get('buy_price'), '${:,.2f}')} · edit saved idea" if saved else "Save my buy price & ownership thesis"
    with st.expander(label):
        with st.form("idea_form_"+row.ticker):
            target = st.number_input("My buy price per share", min_value=0., value=float(saved.get("buy_price") or 0.), step=1., key="buy_"+row.ticker,
                                     help="Your maximum assignment strike. Zero leaves the price unset.")
            thesis = st.text_area("Why would I be happy to own this business?", value=saved.get("thesis", ""),
                                  placeholder="What I like, what must stay true, and what would change my mind…", key="thesis_"+row.ticker)
            submitted = st.form_submit_button("Save idea", type="primary", disabled=not st.session_state.get("_ideas_ready", False))
        if submitted:
            try:
                WL.save(row.ticker, target or None, thesis)
                st.session_state.pop("income_results", None)
                st.session_state.get("card_quotes", {}).pop(row.ticker, None)
                st.caption("Saving in this browser…")
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(f"Could not save this idea: {exc}")
        st.caption("Saved only in this browser profile. Other browsers have separate ideas. Clearing site data removes these saves. No trade or order is placed.")


def saved_page(metrics, cfg, hist):
    ideas = WL.all_ideas()
    heading("MY RESEARCH", "Saved ideas", "Keep your buy prices and ownership theses between visits.")
    if st.session_state.get("undo_idea"):
        if st.button("Undo last removal", disabled=not st.session_state.get("_ideas_ready", False)):
            idea = st.session_state.pop("undo_idea")
            WL.save(idea["ticker"], idea["buy_price"], idea["thesis"])
            st.rerun()
    if not ideas:
        st.info("Save a company from a card or the company workspace. Add your buy price and the reason you would own it.")
        return
    st.download_button("Export my saved ideas", json.dumps({"version":1,"ideas":ideas}, indent=2), file_name="hunt-saved-ideas.json", mime="application/json")
    st.caption("Stored in this browser profile. Export a backup before clearing site data or changing browsers.")
    for ticker, idea in ideas.items():
        r = metrics[metrics.ticker == ticker]
        with st.container(border=True):
            a, b, c = st.columns([3, 1, 1])
            a.markdown(f"**{ticker}** · my buy price **{fmt(idea['buy_price'], '${:,.2f}')}**")
            if not r.empty:
                spot = OP.number(r.iloc[0].get("price"))
                gap = 1-idea["buy_price"]/spot if spot and idea["buy_price"] else None
                a.caption(f"Scan price {fmt(spot, '${:,.2f}')} · my price is {fmt(gap, '{:.1%}')} below it · scan {cfg.get('asof', 'unknown')}")
            else:
                a.caption("Not present in this scan. Your saved thesis is retained.")
            a.text(idea["thesis"] or "No thesis yet. Open the company to add your reasoning.")
            b.button("Open & edit", key="open_saved_"+ticker, on_click=pick, args=(ticker,), disabled=r.empty, width="stretch")
            if c.button("Remove", key="remove_"+ticker, width="stretch", disabled=not st.session_state.get("_ideas_ready", False)):
                st.session_state["undo_idea"] = idea
                WL.remove(ticker)
                st.rerun()
    rows = metrics[metrics.ticker.isin(ideas)]
    if not rows.empty:
        with st.container(border=True):
            income_comparison(rows, cfg)


main()
if st.session_state.pop("_ideas_flush", False):
    st.rerun()
