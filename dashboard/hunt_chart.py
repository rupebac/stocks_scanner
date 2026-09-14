"""Interactive Plotly hunt chart with drag events returned to Streamlit."""
from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path

import plotly
import streamlit as st
import streamlit.components.v1 as components

ASSETS = Path(__file__).parent / "components/hunt_map"


@st.cache_resource(show_spinner=False)
def _component():
    # Serve the installed Plotly bundle locally: no CDN or extra JS dependency.
    source = Path(plotly.__file__).parent / "package_data/plotly.min.js"
    target = ASSETS / "plotly.min.js"
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        with tempfile.NamedTemporaryFile(dir=ASSETS, suffix=".js", delete=False) as f:
            temp = Path(f.name)
        try:
            shutil.copyfile(source, temp)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    return components.declare_component("hunt_map_drag", path=str(ASSETS))


def zone_event(event, quality, valuation):
    """Validate browser input and ignore events from an older slider state."""
    if not isinstance(event, dict) or event.get("kind") != "zone":
        return None
    try:
        values = [float(event[k]) for k in ("quality", "valuation", "base_quality", "base_valuation")]
    except (KeyError, ValueError, TypeError):
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    q, v, bq, bv = values
    if bq != quality or not math.isclose(bv, valuation, abs_tol=1e-8):
        return None
    q = max(0, min(100, math.floor(q + .5)))
    v = round(max(-2., min(2., math.floor(v / .05 + .5)*.05)), 2)
    return {"quality": q, "valuation": v}


def render(fig, quality, valuation, key):
    fig.update_layout(margin=dict(l=60, r=20, t=20, b=125),
                      legend=dict(orientation="h", x=0, y=-.18),
                      xaxis_title="Valuation advantage → cheaper", yaxis_title="Business quality (%)")
    return _component()(figure=fig.to_json(), quality=quality, valuation=valuation,
                        height=615, key=key, default=None)
