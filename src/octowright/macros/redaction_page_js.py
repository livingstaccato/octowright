# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The in-page half of a redacted screenshot.

``CONTROLLER_JS`` builds a controller object that the Python side holds as a
Playwright handle. Its state lives in that closure, never on a page global, so page
script cannot reach or neutralise the restore. The controller:

- ``redact()`` replaces the classified values in text nodes, attribute values and
  form-control values across the document and open shadow roots. Matching follows
  :mod:`octowright.macros.redaction_text`: case, whitespace and invisible characters
  inside a value are ignored, and a string that still holds a value after replacement
  (a decomposed accent, say) is replaced whole. Canvases, media, embeds and frames are
  hidden, and so is any element whose resource address (``src``, ``srcset``,
  ``srcdoc``, ``data``, ``poster``) holds a value; for a ``<picture>`` source that is
  the picture's image. A resource address is never rewritten, because that would
  navigate or reload. Hiding also switches off transitions and animations on the
  element. Once the page is redacted the controller starts counting page changes.
- ``verify()`` reports how many page changes it counted since redaction and how many
  classified spellings the open DOM still holds. A change is any DOM mutation, any
  write to a form value or selection state, any stylesheet edit through the CSSOM
  methods, any change to the set of shadow roots, and any edited form value that
  drifted.
- ``restore()`` stops counting and undoes every change in reverse order: text,
  attributes, form values, the selection of every control it touched, and only the
  style properties it set.
  If the page itself changed an element's style meanwhile, that change is kept. It is
  idempotent.
"""

from __future__ import annotations

from typing import Any

from octowright.macros.redaction_text import JS_IGNORABLE_CLASS

CONTROLLER_JS = r"""({values, ignorable}) => {
  const invisible = new RegExp(`[\\s${ignorable}]+`, 'gu');
  const gap = `[\\s${ignorable}]*`;
  const normalize = (text) => String(text ?? '').normalize('NFKC').replace(invisible, '').toLowerCase();
  const secrets = values.filter((value) => typeof value === 'string' && value.length > 0);
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const patterns = secrets.map((secret) => new RegExp(Array.from(secret).map(escapeRegex).join(gap), 'giu'));
  const needles = [...new Set(secrets.map(normalize).filter((needle) => needle.length > 0))];
  const holds = (value) => {
    const text = normalize(value);
    return needles.some((needle) => text.includes(needle));
  };
  const redact = (value) => {
    const safe = patterns.reduce((text, pattern) => text.replace(pattern, '<redacted>'), String(value ?? ''));
    return holds(safe) ? '<redacted>' : safe;
  };
  const OPAQUE = new Set(['CANVAS', 'EMBED', 'FRAME', 'IFRAME', 'OBJECT', 'VIDEO']);
  const LOADING = new Set(['src', 'srcset', 'srcdoc', 'data', 'poster']);
  const HREF_LOADS = new Set(['BASE', 'IMAGE', 'LINK', 'USE']);
  const EDITABLE = new Set(['INPUT', 'TEXTAREA']);
  const HIDDEN_STYLE = [['transition', 'none'], ['animation', 'none'], ['visibility', 'hidden']];
  const tag = (element) => String((element && element.tagName) || '').toUpperCase();
  const loads = (element, name) => LOADING.has(name)
    || ((name === 'href' || name === 'xlink:href') && HREF_LOADS.has(tag(element)));
  const changes = [];
  const styled = new Map();
  const shielded = new Set();
  const edits = [];
  const unhooks = [];
  let rootCount = 0;
  let observer = null;
  let changed = 0;
  let restored = false;
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
    if (!element.style) return;
    shielded.add(element);
    if (styled.has(element)) return;
    const before = element.getAttribute('style');
    const previous = HIDDEN_STYLE.map(([name]) => [
      name, element.style.getPropertyValue(name), element.style.getPropertyPriority(name),
    ]);
    for (const [name, value] of HIDDEN_STYLE) element.style.setProperty(name, value, 'important');
    styled.set(element, {before, after: element.getAttribute('style'), previous});
  };
  const shield = (element) => {
    const parent = element.parentElement;
    if (tag(element) === 'SOURCE' && tag(parent) === 'PICTURE') {
      for (const image of parent.querySelectorAll(':scope > img')) hide(image);
      shielded.add(element);
      return;
    }
    hide(element);
  };
  const unhide = (element, {before, after, previous}) => {
    if (element.getAttribute('style') === after) {
      if (before === null) element.removeAttribute('style');
      else element.setAttribute('style', before);
      return;
    }
    for (const [name, value, priority] of previous) {
      if (value) element.style.setProperty(name, value, priority);
      else element.style.removeProperty(name);
    }
  };
  const selection = (element) => {
    try {
      return [element.selectionStart, element.selectionEnd, element.selectionDirection];
    } catch (error) {
      return [null, null, null];
    }
  };
  const undo = (change) => {
    const [kind, node] = change;
    if (kind === 'text') node.nodeValue = change[2];
    else if (kind === 'attribute') node.setAttribute(change[2], change[3]);
    else if (kind === 'value') node.value = change[2];
    else {
      const [start, end, direction] = change[2];
      if (start === null) return;
      try {
        node.setSelectionRange(start, end, direction || undefined);
      } catch (error) {
        // A control whose type has no selection keeps none.
      }
    }
  };
  const hook = (prototype, name, kind) => {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
    if (!descriptor || !descriptor.configurable) return;
    const original = kind === 'set' ? descriptor.set : descriptor.value;
    if (typeof original !== 'function') return;
    const counted = function counted(...args) {
      changed += 1;
      return original.apply(this, args);
    };
    Object.defineProperty(prototype, name, {...descriptor, [kind]: counted});
    unhooks.push(() => Object.defineProperty(prototype, name, descriptor));
  };
  const HOOKED = [
    [HTMLInputElement, 'set', ['value', 'checked']],
    [HTMLInputElement, 'value', ['setRangeText', 'setSelectionRange']],
    [HTMLTextAreaElement, 'set', ['value']],
    [HTMLTextAreaElement, 'value', ['setRangeText', 'setSelectionRange']],
    [HTMLSelectElement, 'set', ['value', 'selectedIndex']],
    [HTMLOptionElement, 'set', ['selected']],
    [CSSStyleDeclaration, 'set', ['cssText']],
    [CSSStyleDeclaration, 'value', ['setProperty', 'removeProperty']],
    [CSSStyleSheet, 'value', ['insertRule', 'deleteRule', 'addRule', 'removeRule', 'replace', 'replaceSync']],
    [Document, 'set', ['adoptedStyleSheets']],
    [ShadowRoot, 'set', ['adoptedStyleSheets']],
  ];
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
        const editable = EDITABLE.has(tag(node));
        const mark = changes.length;
        const selected = editable ? selection(node) : null;
        for (const attribute of Array.from(node.attributes || [])) {
          if (!holds(attribute.value)) continue;
          if (loads(node, attribute.name)) {
            shield(node);
            continue;
          }
          changes.push(['attribute', node, attribute.name, attribute.value]);
          node.setAttribute(attribute.name, redact(attribute.value));
        }
        if (editable && node.type !== 'file' && holds(node.value)) {
          changes.push(['value', node, node.value]);
          node.value = redact(node.value);
        }
        // Rewriting a control's value attribute or value resets its selection. Recording the
        // selection before those changes makes the reverse undo put it back after them.
        if (editable && changes.length > mark) changes.splice(mark, 0, ['selection', node, selected]);
      });
      const roots = collectRoots();
      rootCount = roots.length;
      for (const root of roots) {
        for (const element of root.querySelectorAll('input, textarea')) edits.push([element, element.value]);
      }
      observer = new MutationObserver((records) => { changed += records.length; });
      for (const root of roots) {
        observer.observe(root, {subtree: true, childList: true, characterData: true, attributes: true});
      }
      for (const [prototype, kind, names] of HOOKED) {
        for (const name of names) hook(prototype.prototype, name, kind);
      }
    },
    verify() {
      if (observer) changed += observer.takeRecords().length;
      let drift = edits.filter(([element, value]) => element.value !== value).length;
      if (collectRoots().length !== rootCount) drift += 1;
      let remaining = 0;
      scan((kind, node) => {
        if (kind === 'text') {
          if (holds(node.nodeValue)) remaining += 1;
          return;
        }
        for (const attribute of Array.from(node.attributes || [])) {
          if (holds(attribute.value) && !(loads(node, attribute.name) && shielded.has(node))) remaining += 1;
        }
        if (EDITABLE.has(tag(node)) && holds(node.value)) remaining += 1;
      });
      return {changed: changed + drift, remaining};
    },
    restore() {
      if (restored) return;
      restored = true;
      if (observer) observer.disconnect();
      for (let index = unhooks.length - 1; index >= 0; index -= 1) unhooks[index]();
      for (let index = changes.length - 1; index >= 0; index -= 1) undo(changes[index]);
      for (const [element, record] of styled) unhide(element, record);
    },
  };
}"""

REDACT_CALL = "(controller) => controller.redact()"
VERIFY_CALL = "(controller) => controller.verify()"
RESTORE_CALL = "(controller) => controller.restore()"


def controller_argument(values: list[str]) -> dict[str, Any]:
    """The argument ``CONTROLLER_JS`` takes: the value spellings and the shared ignorable class."""
    return {"values": list(values), "ignorable": JS_IGNORABLE_CLASS}
