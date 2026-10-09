/* photag i18n: UI strings are written in English (the base language) and
   looked up in locales/<code>.json, which maps each English source string to
   its translation (English itself needs no file; Hebrew has one like the rest).
   {name} / {0} placeholders are filled from `vars`.
   Loaded before app.js; the dictionary is fetched synchronously so every
   string app.js builds at load time is already translated. */
'use strict';
const LANGS = [
  // code, native name, text direction
  ['en', 'English', 'ltr'], ['he', 'עברית', 'rtl'], ['ar', 'العربية', 'rtl'], ['ru', 'Русский', 'ltr'],
  ['es', 'Español', 'ltr'], ['fr', 'Français', 'ltr'], ['de', 'Deutsch', 'ltr'], ['it', 'Italiano', 'ltr'],
  ['pt', 'Português', 'ltr'], ['nl', 'Nederlands', 'ltr'], ['pl', 'Polski', 'ltr'], ['uk', 'Українська', 'ltr'],
  ['tr', 'Türkçe', 'ltr'], ['zh', '中文（简体）', 'ltr'], ['ja', '日本語', 'ltr'], ['ko', '한국어', 'ltr'], ['hi', 'हिन्दी', 'ltr'],
];
const I18N = (() => {
  const codes = LANGS.map(l => l[0]);
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('pm.lang')); } catch {}
  const lang = codes.includes(saved) ? saved : 'en';        // English unless the user picked another language (View > Language)
  let dict = {};
  if (lang !== 'en') {
    try {
      const x = new XMLHttpRequest();
      x.open('GET', `locales/${lang}.json`, false);   // synchronous on purpose: tiny local file, needed before app.js runs
      x.send();
      if (x.status === 200) dict = JSON.parse(x.responseText);
    } catch {}
  }
  const dir = (LANGS.find(l => l[0] === lang) || [, , 'ltr'])[2];
  document.documentElement.lang = lang;
  document.documentElement.dir = dir;
  // Intl locale for numbers/dates ("zh" -> "zh-CN" etc. handled by the browser)
  return { lang, dir, dict, locale: lang === 'he' ? 'he-IL' : lang };
})();
const RTL = I18N.dir === 'rtl';

function t(src, vars) {
  let s = I18N.dict[src];
  if (typeof s !== 'string' || !s) s = src;
  if (vars != null) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined ? vars[k] : m));
  return s;
}
function setLanguage(code) {
  try { localStorage.setItem('pm.lang', JSON.stringify(code)); } catch {}
  location.reload();
}

// Static markup in index.html: translate text nodes and user-facing attributes once.
(function translateStatic() {
  if (I18N.lang === 'en') return;
  const tr = s => { const k = s.trim(); return k && I18N.dict[k] ? s.replace(k, I18N.dict[k]) : s; };
  const run = () => {
    document.title = tr(document.title);
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) if (n.nodeValue.trim()) n.nodeValue = tr(n.nodeValue);
    for (const el of document.querySelectorAll('[title],[placeholder],[aria-label]'))
      for (const a of ['title', 'placeholder', 'aria-label']) if (el.hasAttribute(a)) el.setAttribute(a, tr(el.getAttribute(a)));
    for (const o of document.querySelectorAll('option')) o.textContent = tr(o.textContent);
  };
  if (document.body) run(); else document.addEventListener('DOMContentLoaded', run);
})();
