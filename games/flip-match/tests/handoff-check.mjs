import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';

const baseURL = process.env.GAME_URL ?? 'http://localhost:5173/games/flip-match/';
const artifacts = fileURLToPath(new URL('../../../artifacts/flip-match/', import.meta.url));
await mkdir(artifacts, { recursive: true });

function deckFor(seed, pairs) {
  let rng = seed >>> 0;
  const deck = Array.from({ length: pairs * 2 }, (_, i) => Math.floor(i / 2));
  for (let i = deck.length - 1; i > 0; i--) {
    rng = (Math.imul(rng, 1664525) + 1013904223) >>> 0;
    const j = Math.floor(rng / 0x100000000 * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
}

const seed = 900, deck = deckFor(seed, 12);
const pair = deck.flatMap((face, id) => face === 0 ? [id] : []);
const different = [deck.indexOf(1), deck.indexOf(2)];
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chrome' });
try {
  for (const [name, width, height] of [['desktop', 1440, 1000], ['ipad-landscape', 1180, 820], ['ipad-portrait', 820, 1180], ['phone', 390, 844], ['phone-landscape', 844, 390]]) {
    const context = await browser.newContext({ viewport: { width, height }, hasTouch: name !== 'desktop' });
    await context.addInitScript(({ seed }) => {
      if (!localStorage.getItem('flip-match-save-v1')) localStorage.setItem('flip-match-save-v1', JSON.stringify({ version: 2, settings: { count: 2, pairs: 12, bots: [false, false], difficulty: 'normal' }, seed, actions: [] }));
      if (!localStorage.getItem('flip-match-preferences-v1')) localStorage.setItem('flip-match-preferences-v1', JSON.stringify({ version: 2, settings: { count: 2, pairs: 12, bots: [false, false], difficulty: 'normal' }, sound: false }));
    }, { seed });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      await page.goto(baseURL);
      await expect(page.locator('#match-scene canvas')).toBeVisible();
      await page.getByRole('button', { name: '棋子列表' }).click();
      const flip = async id => { await page.locator(`[data-tile="${id}"]`).click(); };
      await flip(pair[0]); await flip(pair[1]);
      await expect(page.locator('#status-heading')).toHaveText('好记性！再找一对吧');
      await expect(page.locator('.turn-copy')).not.toHaveClass(/handoff/);
      await flip(different[0]); await flip(different[1]);
      await expect(page.locator('#status-heading')).toHaveText('从1 号薄荷队交给2 号蜜桃队');
      await expect(page.locator('[data-player="1"] .player-state')).toHaveText('接过回合');
      await expect(page.locator('.turn-copy')).toHaveClass(/handoff/);
      await expect(page.locator(`[data-tile="${deck.indexOf(3)}"]`)).toBeEnabled();
      await page.getByRole('button', { name: '收起棋子列表' }).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: `${artifacts}production-${name}-handoff.png`, fullPage: true });
      if (name === 'desktop') {
        await page.getByRole('button', { name: '暂停游戏' }).click();
        await expect(page.locator('.turn-copy')).not.toHaveClass(/handoff/);
        await page.getByRole('button', { name: '继续游戏' }).last().click();
        await expect(page.locator('.turn-copy')).not.toHaveClass(/handoff/);
      } else if (name === 'phone') {
        await page.waitForTimeout(2600); await expect(page.locator('.turn-copy')).not.toHaveClass(/handoff/);
        await page.reload(); await expect(page.locator('#match-scene canvas')).toBeVisible();
        await expect(page.locator('.turn-copy')).not.toHaveClass(/handoff/);
        await expect(page.locator('#turn-label')).toHaveText('2 号蜜桃队的回合');
      }
      assert.deepEqual(errors, []);
      console.log(`PASS ${name}: match continues, miss hands off, no overflow`);
    } finally { await context.close(); }
  }
} finally { await browser.close(); }
