"""Block now applies immediately while unrelated draft edits remain pending."""
import pytest
from test_page_blocking_overview import open_page, option


def test_block_now_applies_with_autosave_off(page, harness, shell):
    harness.db.set_setting("ui.autosave", "0")
    iid = harness.db.add_item("Block now test", ["block-now.test"], rules=[
        {"rule_type": "time_limit", "daily_limit_min": 60}])
    harness.api.draft_set_notify(iid, "off")
    open_page(shell, page)
    option(page, "Block now test", "Block now").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Block duration").select_option("today")
    assert "2026-10-12 00:00" in dialog.locator("[data-preview]").inner_text()
    dialog.get_by_role("button", name="Block now", exact=True).click()
    page.wait_for_function("!document.querySelector('#dlg').open")
    saved = next(i for i in harness.db.list_items() if i["id"] == iid)
    assert saved["notify"] is None
    assert next(r for r in saved["rules"] if r["rule_type"] == "temporary")["temp_until"] == "2026-10-12 00:00:00"
    assert harness.api.draft_get_state()["data"]["dirty"]
    harness.api.draft_discard()
    saved = next(i for i in harness.db.list_items() if i["id"] == iid)
    assert any(r["rule_type"] == "temporary" for r in saved["rules"])


@pytest.mark.parametrize("group", [False, True])
def test_block_now_keeps_a_longer_unsaved_editor_rule(page, harness, shell, group):
    harness.db.set_setting("ui.autosave", "0")
    iid = harness.db.add_item("Long draft", ["long-draft.test"], rules=[])
    oid = harness.db.add_group("Long group", [{"rule_type": "permanent"}], {iid: {}}) if group else iid
    open_page(shell, page)
    if group:
        page.click('[data-tab="groups"]')
        page.click(f'.gp-card[data-id="{oid}"]')
        area = page.locator('[data-k="g-editor"]')
        block = area.locator('[data-k="g-block-now"]')
    else:
        option(page, "Long draft", "Edit").click()
        page.wait_for_selector('[data-k="ad-title"]:has-text("Edit")')
        area = page.locator('.ad')
        block = area.locator('[data-block-now]')
    area.locator('.rail-row[data-t="temporary"] .rail-main').click()
    area.locator('[data-type="temporary"] [data-choice="custom"]').click()
    area.locator('[data-type="temporary"] [data-f="amount"]').fill("5")
    block.click()
    page.get_by_role("dialog").get_by_role("button", name="Block now", exact=True).click()
    page.wait_for_function("!document.querySelector('#dlg').open")
    # The one-hour immediate save must leave the five-hour local form intact.
    assert area.locator('[data-type="temporary"] [data-f="amount"]').input_value() == "5"
    assert area.locator('[data-type="temporary"] [data-choice="custom"]').get_attribute("aria-pressed") == "true"
    saved = next(i for i in (harness.db.list_groups() if group else harness.db.list_items()) if i["id"] == oid)
    assert next(r for r in saved["rules"] if r["rule_type"] == "temporary")["temp_until"] == "2026-10-11 16:42:17"
