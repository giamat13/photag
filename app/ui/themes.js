/* photag themes: a theme is a small description (background + accent colour, light or dark, three layout switches) from which the whole set
   of CSS variables of style.css is derived. Loaded in <head> of the main window and of the viewer, so it is applied before the first paint.
   The photo surfaces (grid background, big picture, edit canvas, histogram) are NOT touched by any theme: they stay neutral gray,
   so colour and exposure are always judged fairly.
   Layout switches (attributes on <html>): data-tabs = pills | words, data-cells = cards | flat, data-titles = caps | plain. */
(function () {
  'use strict';
  // name = the English text that is translated with t(); bg / acc = the colours the rest is derived from; light = light chrome;
  // tabs / cells / titles = the layout switches; extra = CSS variables set as they are (after the derived ones)
  const THEMES = [
    { id: 'slate', name: 'Slate', bg: '#212736', acc: '#ff9a3c' },
    { id: 'classic', name: 'Classic', classic: true },
    { id: 'midnight', name: 'Midnight', bg: '#10172a', acc: '#38d0f0' },
    { id: 'graphite', name: 'Graphite', bg: '#2f2f31', acc: '#f2f2f2', tabs: 'words', titles: 'plain' },
    { id: 'forest', name: 'Forest', bg: '#1f2d27', acc: '#7ed08a' },
    { id: 'plum', name: 'Plum', bg: '#2a2036', acc: '#e86fd0' },
    { id: 'sunset', name: 'Sunset', bg: '#2e2320', acc: '#ff6a45' },
    { id: 'ocean', name: 'Ocean', bg: '#16292e', acc: '#2fd9c4' },
    { id: 'rose', name: 'Rose', bg: '#30202a', acc: '#ff7a9c' },
    { id: 'hc-dark', name: 'High contrast (dark)', bg: '#000000', acc: '#ffe600', hc: true },
    { id: 'hc-light', name: 'High contrast (light)', bg: '#ffffff', acc: '#0b57d0', light: true, hc: true },
    { id: 'light', name: 'Light', light: true, base: true, acc: '#e0731a' },
    { id: 'paper', name: 'Paper', bg: '#f1ece1', acc: '#a4561d', light: true },
    { id: 'sepia', name: 'Sepia', bg: '#eadcc4', acc: '#9b2d20', light: true },
    { id: 'colorblind', name: 'Colour-blind friendly', bg: '#242a33', acc: '#56b4e9',
      extra: { '--red': '#d55e00', '--yellow': '#f0e442', '--green': '#009e73', '--blue': '#0072b2', '--purple': '#cc79a7' } },
    { id: 'auto', name: 'Follow Windows', auto: true },
  ];
  const CLASSIC = {   // exactly the colours of the look before 15.0.0
    '--mb': '#262626', '--top': '#1b1b1b', '--pnl': '#343434', '--pnl-h': '#3a3a3a', '--pnl-line': '#262626', '--well': '#262626',
    '--tool': '#373737', '--film': '#1f1f1f', '--film-h': '#2b2b2b', '--acc': '#4b98f0', '--star': '#dadada',
    '--raise1': '#2a2a2a', '--raise2': '#2e2e2e', '--deep': '#1b1b1b', '--deep2': '#1f1f1f', '--line': '#1e1e1e', '--line-strong': '#111111',
    '--hover': '#3b3b3b', '--chip': '#3a3a3a', '--btn1': '#4a4a4a', '--btn2': '#414141',
    '--t1': '#e6e6e6', '--t2': '#c2c2c2', '--t3': '#939393', '--t4': '#6b6b6b', '--knob': '#bdbdbd', '--knob-line': '#222222',
  };
  const KEY = 'pm.themeId', KEY_CUSTOM = 'pm.themeCustom';
  const VARS = Object.keys(CLASSIC).concat(['--red', '--yellow', '--green', '--blue', '--purple']);
  const clamp = (x, a = 0, b = 100) => Math.min(b, Math.max(a, x));

  function hexToHsl(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || ''); if (!m) return [220, 20, 20];
    const n = parseInt(m[1], 16), r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2; let h = 0, s = 0;
    if (mx !== mn) {
      const d = mx - mn; s = l > .5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60;
    }
    return [h, s * 100, l * 100];
  }
  const hsl = (h, s, l) => `hsl(${Math.round(h)} ${Math.round(clamp(s))}% ${Math.round(clamp(l))}%)`;

  // the whole variable set from a background colour and an accent
  function derive(th) {
    const [h, s0, l0] = hexToHsl(th.bg), s = Math.min(s0, th.light ? 40 : 45), light = !!th.light, hc = !!th.hc;
    const d = (dl) => hsl(h, s, light ? l0 - dl : l0 + dl);            // dl > 0 = away from the panel colour, towards the "raised" side
    const v = {};
    if (!light) {
      Object.assign(v, {
        '--pnl': hsl(h, s, l0), '--mb': d(-5), '--top': d(-8), '--pnl-h': d(4), '--pnl-line': d(-7), '--well': d(-6), '--tool': d(2), '--film': d(-9), '--film-h': d(-5),
        '--raise1': d(-4), '--raise2': d(-2), '--deep': d(-8), '--deep2': d(-6), '--line': d(-8), '--line-strong': d(-12), '--hover': d(7), '--chip': d(5),
        '--btn1': d(13), '--btn2': d(8),
        '--t1': hsl(h, 14, 94), '--t2': hsl(h, 12, 83), '--t3': hsl(h, 10, 65), '--t4': hsl(h, 8, 48),
      });
    } else {
      Object.assign(v, {
        '--pnl': hsl(h, s, l0), '--mb': d(4), '--top': d(2), '--pnl-h': d(5), '--pnl-line': d(10), '--tool': d(4),
        '--raise1': hsl(h, s, clamp(l0 + 3, 0, 99)), '--raise2': d(4), '--deep': d(7), '--deep2': d(4), '--line': d(10), '--line-strong': d(18), '--hover': d(7), '--chip': d(6),
        '--btn1': hsl(h, s, clamp(l0 + 2, 0, 99)), '--btn2': d(5),
        '--t1': hsl(h, 25, 9), '--t2': hsl(h, 20, 20), '--t3': hsl(h, 14, 36), '--t4': hsl(h, 10, 50),
      });
    }
    v['--acc'] = th.acc; v['--star'] = th.light ? th.acc : hsl(hexToHsl(th.acc)[0], 85, 68);
    if (hc) {
      Object.assign(v, light
        ? { '--t1': '#000', '--t2': '#000', '--t3': '#222', '--t4': '#444', '--line': '#000', '--line-strong': '#000', '--pnl-line': '#000', '--pnl': '#fff', '--mb': '#fff', '--top': '#fff', '--btn1': '#fff', '--btn2': '#fff', '--raise1': '#fff' }
        : { '--t1': '#fff', '--t2': '#fff', '--t3': '#e8e8e8', '--t4': '#bdbdbd', '--line': '#fff', '--line-strong': '#fff', '--pnl-line': '#fff', '--pnl': '#000', '--mb': '#000', '--top': '#000', '--btn1': '#000', '--btn2': '#000', '--raise1': '#000' });
    }
    return Object.assign(v, th.extra || {});
  }

  function systemLight() { try { return window.matchMedia('(prefers-color-scheme: light)').matches; } catch { return false; } }
  function byId(id) { return THEMES.find(x => x.id === id); }
  function readCustom() { try { return JSON.parse(localStorage.getItem(KEY_CUSTOM) || 'null'); } catch { return null; } }
  function resolve(id) {                       // the theme object to draw (follows the system for "auto", the saved custom theme for "custom")
    if (id === 'custom') { const c = readCustom(); if (c && /^#[0-9a-f]{6}$/i.test(c.bg) && /^#[0-9a-f]{6}$/i.test(c.acc)) return { ...c, id: 'custom', light: !!c.light }; id = 'slate'; }
    const th = byId(id) || byId('slate');
    return th.auto ? byId(systemLight() ? 'light' : 'slate') : th;
  }

  let current = 'slate', mq = null;
  function apply(id, opts) {
    opts = opts || {};
    const th = resolve(id), root = document.documentElement, st = root.style;
    current = id;
    VARS.forEach(k => st.removeProperty(k));
    if (th.light) root.dataset.theme = 'light'; else delete root.dataset.theme;
    root.dataset.tabs = th.tabs || (th.classic ? 'words' : 'pills');
    root.dataset.cells = th.cells || (th.classic ? 'flat' : 'cards');
    root.dataset.titles = th.titles || (th.classic ? 'plain' : 'caps');
    root.dataset.themeId = id;
    let vars = {};
    if (th.classic) vars = CLASSIC; else if (th.base) vars = { '--acc': th.acc, '--star': th.acc }; else vars = derive(th);
    Object.keys(vars).forEach(k => st.setProperty(k, vars[k]));
    if (opts.save !== false) {
      try { localStorage.setItem(KEY, JSON.stringify(id)); localStorage.setItem('pm.theme', JSON.stringify(th.light ? 'light' : 'dark')); } catch {}
    }
    if (opts.save !== false && opts.mirror !== false) {
      try { fetch('/api/ui-theme', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, custom: readCustom() }) }).catch(() => {}); } catch {}
    }
    try { window.dispatchEvent(new CustomEvent('photag-theme', { detail: { id, theme: th } })); } catch {}
  }
  function saved() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || 'null'); if (v) return v; } catch {}
    try { if (JSON.parse(localStorage.getItem('pm.theme') || '"dark"') === 'light') return 'light'; } catch {}   // the old light / dark switch
    return 'slate';
  }
  function watchSystem() {
    try {
      mq = window.matchMedia('(prefers-color-scheme: light)');
      mq.addEventListener('change', () => { if (current === 'auto') apply('auto', { save: false }); });
    } catch {}
  }
  function setCustom(c) { try { localStorage.setItem(KEY_CUSTOM, JSON.stringify(c)); } catch {} }
  // a theme file: { "photag-theme": 1, name, bg, acc, light } -- only these four fields are read, nothing else is trusted
  function parseThemeFile(text) {
    let o; try { o = JSON.parse(text); } catch { return null; }
    if (!o || o['photag-theme'] !== 1 || !/^#[0-9a-f]{6}$/i.test(o.bg) || !/^#[0-9a-f]{6}$/i.test(o.acc)) return null;
    return { name: String(o.name || 'Custom').slice(0, 40), bg: o.bg, acc: o.acc, light: !!o.light };
  }
  // contrast ratio of two #rrggbb / rgb() colours (WCAG), used by the tests and the picker's warning
  function lum(c) {
    const m = c.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number).map(x => { x /= 255; return x <= .03928 ? x / 12.92 : Math.pow((x + .055) / 1.055, 2.4); });
    return .2126 * m[0] + .7152 * m[1] + .0722 * m[2];
  }
  function contrast(a, b) { const la = lum(a), lb = lum(b); return (Math.max(la, lb) + .05) / (Math.min(la, lb) + .05); }

  window.PhotagTheme = { THEMES, apply, saved, current: () => current, resolve, derive, setCustom, readCustom, parseThemeFile, contrast, hexToHsl,
    hsl: (c) => c };
  apply(saved(), { save: false });
  watchSystem();
})();
