"""Interact with the visible shared dropdown, preserving real UI events."""
def choose(page, select, value):
    select.locator('..').locator('.cuttle-combo-trigger').click()
    page.locator('.cuttle-combo-popup [role="option"][data-value="' + value + '"]').click()
