# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The in-page half of a redacted screenshot.

``CONTROLLER_JS`` builds a controller object that the Python side holds as a
Playwright handle. Its state lives in that closure, never on a page global, so page
script cannot reach or neutralise the restore. The controller:

- ``redact()`` replaces every spelling of the classified values (case-insensitively)
  in text nodes, attribute values and form-control values across the document and
  open shadow roots. It hides canvases, media, embeds and frames, and any element
  whose resource address holds a value, instead of rewriting that address (a
  rewritten ``src`` would navigate or reload). Hiding also switches off transitions
  and animations on that element, so a page transition cannot keep it visible.
  It then starts watching the page.
- ``verify()`` reports whether the page changed since redaction (any DOM mutation
  other than an inline ``style`` change, any edited form value that drifted, any new
  shadow root) and how many classified spellings the open DOM still holds.
- ``restore()`` stops watching and undoes every change in reverse order. It is
  idempotent.
"""

from __future__ import annotations

CONTROLLER_JS = r"""(values) => {
  const secrets = values.filter((value) => typeof value === 'string' && value.length > 0);
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const patterns = secrets.map((secret) => new RegExp(escapeRegex(secret), 'gi'));
  const lowered = secrets.map((secret) => secret.toLowerCase());
  const redact = (value) => patterns.reduce((text, pattern) => text.replace(pattern, '<redacted>'), String(value ?? ''));
  const holds = (value) => {
    const text = String(value ?? '').toLowerCase();
    return lowered.some((secret) => text.includes(secret));
  };
  const OPAQUE = new Set(['CANVAS', 'EMBED', 'FRAME', 'IFRAME', 'OBJECT', 'VIDEO']);
  const LOADING = new Set(['src', 'srcset', 'data', 'poster']);
  const HREF_LOADS = new Set(['BASE', 'IMAGE', 'LINK', 'USE']);
  const EDITABLE = new Set(['INPUT', 'TEXTAREA']);
  const tag = (element) => String(element.tagName || '').toUpperCase();
  const loads = (element, name) => LOADING.has(name)
    || ((name === 'href' || name === 'xlink:href') && HREF_LOADS.has(tag(element)));
  const changes = [];
  const hidden = new Set();
  const edits = [];
  let rootCount = 0;
  let observer = null;
  let changed = 0;
  let restored = false;
  const countable = (records) => records.filter(
    (record) => !(record.type === 'attributes' && record.attributeName === 'style'),
  ).length;
  const collectRoots = () => {
    const found = [document];
    for (let index = 0; index < found.length; index += 1) {
      for (const element of found[index].querySelectorAll('*')) {
        if (element.shadowRoot) found.push(element.shadowRoot);
      }
    }
    return found;
  };
  const scan = (visit) => {
    for (const root of collectRoots()) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) visit('text', walker.currentNode);
      for (const element of root.querySelectorAll('*')) visit('element', element);
    }
  };
  const hide = (element) => {
    if (hidden.has(element) || !element.style) return;
    hidden.add(element);
    changes.push(['style', element, element.getAttribute('style')]);
    element.style.setProperty('transition', 'none', 'important');
    element.style.setProperty('animation', 'none', 'important');
    element.style.setProperty('visibility', 'hidden', 'important');
  };
  return {
    redact() {
      scan((kind, node) => {
        if (kind === 'text') {
          const safe = redact(node.nodeValue);
          if (safe !== node.nodeValue) {
            changes.push(['text', node, node.nodeValue]);
            node.nodeValue = safe;
          }
          return;
        }
        if (OPAQUE.has(tag(node))) hide(node);
        for (const attribute of Array.from(node.attributes || [])) {
          if (!holds(attribute.value)) continue;
          if (loads(node, attribute.name)) {
            hide(node);
            continue;
          }
          changes.push(['attribute', node, attribute.name, attribute.value]);
          node.setAttribute(attribute.name, redact(attribute.value));
        }
        if (EDITABLE.has(tag(node)) && node.type !== 'file' && holds(node.value)) {
          changes.push(['value', node, node.value]);
          node.value = redact(node.value);
        }
      });
      const roots = collectRoots();
      rootCount = roots.length;
      for (const root of roots) {
        for (const element of root.querySelectorAll('input, textarea')) edits.push([element, element.value]);
      }
      observer = new MutationObserver((records) => { changed += countable(records); });
      for (const root of roots) {
        observer.observe(root, {subtree: true, childList: true, characterData: true, attributes: true});
      }
    },
    verify() {
      if (observer) changed += countable(observer.takeRecords());
      let drift = edits.filter(([element, value]) => element.value !== value).length;
      if (collectRoots().length !== rootCount) drift += 1;
      let remaining = 0;
      scan((kind, node) => {
        if (kind === 'text') {
          if (holds(node.nodeValue)) remaining += 1;
          return;
        }
        for (const attribute of Array.from(node.attributes || [])) {
          if (holds(attribute.value) && !(loads(node, attribute.name) && hidden.has(node))) remaining += 1;
        }
        if (EDITABLE.has(tag(node)) && holds(node.value)) remaining += 1;
      });
      return {changed: changed + drift, remaining};
    },
    restore() {
      if (restored) return;
      restored = true;
      if (observer) observer.disconnect();
      for (let index = changes.length - 1; index >= 0; index -= 1) {
        const change = changes[index];
        if (change[0] === 'text') change[1].nodeValue = change[2];
        else if (change[0] === 'attribute') change[1].setAttribute(change[2], change[3]);
        else if (change[0] === 'value') change[1].value = change[2];
        else if (change[2] === null) change[1].removeAttribute('style');
        else change[1].setAttribute('style', change[2]);
      }
    },
  };
}"""

REDACT_CALL = "(controller) => controller.redact()"
VERIFY_CALL = "(controller) => controller.verify()"
RESTORE_CALL = "(controller) => controller.restore()"
