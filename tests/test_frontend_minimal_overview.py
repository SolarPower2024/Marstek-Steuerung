"""FORK: the panel honours the ``minimal_overview`` flag (only System status on Resumen)."""

import re
from pathlib import Path

PANEL = Path("custom_components/omnibattery/frontend/marstek-panel.js")


def _src() -> str:
    return PANEL.read_text(encoding="utf-8")


def test_flag_read_strictly_from_panel_config():
    src = _src()
    assert len(re.findall(r"this\._panelConfig\.minimal_overview === true", src)) >= 2
    assert "FORK: minimal overview" in src


def test_resumen_returns_only_soc_card_when_minimal():
    src = _src()
    body = src[src.index("_renderResumen() {"):]
    body = body[: body.index("_card(title, icon)")]
    assert body.index("minimal_overview === true") < body.index("c.flow")
    assert 'wrap("res-stack", [c.soc])' in body


def test_history_not_started_when_minimal():
    src = _src()
    body = src[src.index("_ensureHistoryStarted() {"):]
    body = body[: body.index("_refreshHistory() {")]
    assert body.index("minimal_overview === true") < body.index("this._startHistory()")
