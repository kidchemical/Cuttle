"""Shared page helpers: the visible dropdown (real UI events) and a text contrast audit."""
def choose(page, select, value):
    select.locator('..').locator('.cuttle-combo-trigger').click()
    page.locator('.cuttle-combo-popup [role="option"][data-value="' + value + '"]').click()


_LOW_CONTRAST_JS = r"""(floor) => {
  const parse = (s) => {
    let m = s.match(/^rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = s.match(/^color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[ \/]+/).filter(Boolean).map(parseFloat); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return null;
  };
  const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat([1]);
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const stops = (getComputedStyle(document.body).backgroundImage.match(/rgba?\([^)]+\)/g) || []).map(parse).filter(Boolean);
  const bases = stops.length ? stops.map((c) => [c[0], c[1], c[2], 1]) : [[255, 255, 255, 1]];
  const low = [], seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!n.textContent.trim() || seen.has(el) || ['SCRIPT', 'STYLE', 'OPTION'].includes(el.tagName)) continue;
    seen.add(el);
    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) || el.closest(':disabled')) continue;
    const fg = parse(getComputedStyle(el).color), layers = [];
    let painted = false;
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== 'none') painted = true;
      layers.push(parse(cs.backgroundColor) || [0, 0, 0, 0]);
    }
    layers.push(parse(getComputedStyle(document.body).backgroundColor) || [0, 0, 0, 0]);
    if (!fg || painted) continue;
    let worst = 99;
    for (const base of bases) {
      let bg = base;
      for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
      const a = lum(over(fg, bg)), b = lum(bg);
      worst = Math.min(worst, (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05));
    }
    if (worst < floor) low.push(worst.toFixed(2) + ' ' + el.tagName + '.' + el.className + ' "' + n.textContent.trim().slice(0, 30) + '"');
  }
  return low;
}"""


def low_contrast_text(page, floor=4.5):
    """Visible enabled text whose WCAG contrast against what is painted behind it
    is under ``floor`` (worst stop of a gradient page background). Text over an
    element's own background image is not measured."""
    return page.evaluate(_LOW_CONTRAST_JS, floor)
