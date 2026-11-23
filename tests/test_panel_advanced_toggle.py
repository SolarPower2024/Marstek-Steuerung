"""Advanced-settings toggle on the Control tab (frontend only, no new entities).

Items marked `adv` in SYS_SECTIONS are hidden behind a per-browser "Advanced
settings" toggle (localStorage, no HA entity), reusing the same display:none
mechanism a `gate` switch already uses to hide its sibling rows. Static text
assertions on the raw panel source, same style as the other panel-surface tests.
"""

from pathlib import Path


COMPONENT = Path("custom_components/omnibattery")
PANEL = COMPONENT / "frontend" / "marstek-panel.js"


def test_toggle_label_exists_in_all_six_languages():
    panel = PANEL.read_text(encoding="utf-8")

    assert panel.count('ctlAdvanced: "') == 6
    # The button is icon-only, so the label is its title and its aria-label.
    assert panel.count('_t("ctlAdvanced")') == 2


def test_toggle_state_is_local_storage_not_an_entity():
    panel = PANEL.read_text(encoding="utf-8")

    # Per card: each section owning `adv` rows keeps its own persisted state.
    assert '_ctlAdvKey(tk) { return "omnibattery:control-advanced:" + tk; }' in panel
    assert "_loadCtlAdv(tk)" in panel
    assert "_saveCtlAdv(tk, on)" in panel


def test_render_sys_sections_hides_adv_items_when_toggle_is_off():
    panel = PANEL.read_text(encoding="utf-8")
    start = panel.index("_renderSysSections(defs, store, empty) {")
    end = panel.index("_loadCtlCols()", start)
    body = panel[start:end]

    # The rows stay in the DOM; the card class hides them via CSS, so the
    # button can toggle in place without rebuilding the whole tab.
    assert 'card.classList.toggle("adv-off", !this._loadCtlAdv(sec.tk));' in body
    assert 'if (rows.some((r) => r.item.adv)) this._addAdvBtn(card, head, sec.tk);' in body
    assert 'if (r.item.adv) for (const n of nodes) n.classList?.add("adv-row");' in body
    assert ".card.adv-off .adv-row { display: none; }" in panel
