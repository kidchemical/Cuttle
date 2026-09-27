import { test, expect } from '@playwright/test';

test('pending attachment survives refresh', async ({ page }) => {
  await page.goto('http://127.0.0.1:58315/chat_page.html', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1000);
  
  // Simulate upload by directly setting pendingAttachments via localStorage + restore
  // First ensure a session exists
  await page.evaluate(async () => {
    localStorage.setItem('chatSessions', JSON.stringify({
      'test_sess': { id: 'test_sess', title: 'Test', updated: Date.now(), messages: [] }
    }));
    localStorage.setItem('lastChatSessionId', 'test_sess');
    if (window.loadChatSession) await window.loadChatSession('test_sess');
  });
  await page.waitForTimeout(500);

  // Push fake attachment via the same path upload uses
  await page.evaluate(() => {
    // Use the app's internal pendingAttachments via localStorage
    localStorage.setItem('cuttle.pendingAttachments.test_sess', JSON.stringify([
      { filename: 'test.png', path: '/tmp/test.png', mime: 'image/png', url: '/output/uploads/test_sess/test.png', size: 1234 }
    ]));
  });

  // Reload and verify restore
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  await page.evaluate(async () => {
    if (window.loadChatSession) await window.loadChatSession('test_sess');
  });
  await page.waitForTimeout(500);

  const chips = await page.evaluate(() => document.querySelectorAll('.attach-chip').length);
  const stored = await page.evaluate(() => localStorage.getItem('cuttle.pendingAttachments.test_sess'));
  expect(chips).toBeGreaterThanOrEqual(1);
  expect(stored).toBeTruthy();
  expect(JSON.parse(stored).length).toBe(1);

  // Remove chip should clear storage
  const removeBtn = page.locator('.attach-chip button').first();
  if (await removeBtn.count() > 0) {
    await removeBtn.click();
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => localStorage.getItem('cuttle.pendingAttachments.test_sess'));
    // Should be null or empty array
    const count = await page.evaluate(() => document.querySelectorAll('.attach-chip').length);
    expect(count).toBe(0);
  }
});
