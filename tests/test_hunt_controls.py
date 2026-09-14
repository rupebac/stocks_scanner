"""Hunt controls update display criteria while preserving business/data checks."""
from pathlib import Path
import json

import pandas as pd
from streamlit.testing.v1 import AppTest
from scanner import scoring as SC


def test_custom_cuts_relax_defaults_but_keep_cash_roic_and_data_gates():
    frame = pd.DataFrame({
        "ticker": ["LOW", "PRICIER", "VALUE", "DEBT_ARTIFACT", "NO_CASH", "NO_DATA"],
        "quality_score": [50., 75., 80., 90., 90., 90.],
        "quality_floor_pass": [False, True, True, False, True, True],
        "gates_pass": True, "input_coverage": [1., 1., 1., 1., 1., .5],
        "roic": [.2, .2, .2, 1.5, .2, .2], "roic_ttm": [.2, .2, .2, 1.5, .2, .2],
        "roic_median_5y": [.2, .2, .2, 1.5, .2, .2],
        "residual": [-.2, .1, -.4, -.5, -.5, -.5], "fcf_yield": [.1,.1,.1,.1,.01,.1],
    })
    before = frame.copy(deep=True)
    assert SC.highlighted(frame).ticker.tolist() == ["VALUE"]
    assert SC.highlighted(frame, quality_min=40, max_residual=.2).ticker.tolist() == ["VALUE", "LOW", "PRICIER"]
    assert SC.highlighted(frame, quality_min=85, max_residual=-.3).empty
    pd.testing.assert_frame_equal(frame, before)


def test_controls_move_lines_resize_chart_and_reset():
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"dashboard/app.py"), default_timeout=30).run()
    initial = next(m.value for m in at.metric if m.label == "Quality & value shortlist")
    at.slider("hunt_quality_control").set_value(90)
    at.slider("hunt_value_control").set_value(.5).run()
    assert not at.exception
    args = json.loads(at.get("component_instance")[0].proto.json_args)
    spec = json.loads(args["figure"])
    assert spec["layout"]["height"] == 615
    shapes = spec["layout"]["shapes"]
    assert any(s.get("y0") == s.get("y1") == 90 for s in shapes)
    assert any(s.get("x0") == s.get("x1") == .5 for s in shapes)
    assert at.session_state["hunt_zone"] == {"quality":90, "valuation":.5}
    next(b for b in at.button if b.label == "Reset hunt zone").click().run()
    assert not at.exception
    assert next(m.value for m in at.metric if m.label == "Quality & value shortlist") == initial


def test_drag_event_validation_and_widget_sync():
    from dashboard.hunt_chart import zone_event
    base = {"kind":"zone", "quality":73.6, "valuation":.328, "base_quality":60, "base_valuation":0}
    assert zone_event(base, 60, 0) == {"quality":74, "valuation":.35}
    assert zone_event(base, 80, 0) is None
    assert zone_event({**base, "quality":float("nan")}, 60, 0) is None
    assert zone_event({**base, "valuation":99, "quality":200}, 60, 0) == {"quality":100, "valuation":2.}
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"dashboard/app.py"), default_timeout=30).run()
    at.session_state["pending_hunt_zone"] = {"quality":74, "valuation":.35}
    at.run()
    assert not at.exception
    assert at.slider("hunt_quality_control").value == 74
    assert at.slider("hunt_value_control").value == .35
