# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The in-page half of a redacted screenshot.

``CONTROLLER_JS`` builds a controller object that the Python side holds through its own
DevTools session (:mod:`octowright.macros.page_devtools`). Its state lives in that closure,
never on a page global, so page script cannot reach or neutralise the restore. The
controller:

- ``redact(...closedRoots)`` works through the document, every open shadow root, and the
  closed shadow roots DevTools hands it. It replaces the classified values in text nodes
  and attribute values, matching as :mod:`octowright.macros.redaction_text` defines
  (case, whitespace, invisible characters and the punctuation of a number are ignored,
  and a string that still holds a value after replacement is replaced whole). A text
  control holding a value keeps its value and caret and has its text masked with
  ``-webkit-text-security`` instead; other controls have their value replaced.
  Canvases, media, embeds and frames are hidden, and so is any element whose resource
  address (``src``, ``srcset``, ``srcdoc``, ``data``, ``poster``, or the link of an image,
  ``use`` or filter image) holds a value; for a ``<picture>`` source that is the picture's
  image. A resource address is never rewritten, because that would navigate or reload.
  Hiding also turns the element's transitions off; the page's animations are paused through
  DevTools for the whole capture.
- ``watch(latent)`` starts counting the page changes DevTools does not report: a change to
  the inline style of an element the redaction styled, or to a style that holds a value
  (every inline style change when ``latent``, that is when a stylesheet holds a value a
  style change could reveal); a change of checked, indeterminate, selected or custom
  validity state; a focus change; and a change of the location hash. DevTools counts
  every other DOM and stylesheet change.
- ``verify()`` reports how many such changes it counted, how many classified
  spellings the redacted roots still hold outside what it hid or masked, and whether a
  view transition is running on the document or on any element in the redacted roots
  (its raster of the old state cannot be redacted).
- ``restore()`` stops counting and undoes every change in reverse order: text,
  attributes, control values, and only the style properties it set. If the page itself
  changed an element's style meanwhile, that change is kept, and each transition
  longhand is put back as it was. Attributes are written and undone through their
  attribute nodes, and a hidden element's transitions stay off until its style has
  settled, so its own transition does not replay; a style attribute that was redacted
  before its element was hidden comes back with the hiding. It is idempotent.

The page's own mutation observers see the redaction: an application that saves what it
observes (an autosave, say) could save a redacted text or attribute.
"""

from __future__ import annotations

from typing import Any

from octowright.macros.redaction_text import JS_DIGIT_SEPARATOR_CLASS, JS_IGNORABLE_CLASS, digit_needles
from octowright.macros.rendered_surface import (
    HREF_DRAWN_ELEMENTS,
    HREF_LOADING_ELEMENTS,
    LOADING_ATTRIBUTES,
    OPAQUE_ELEMENTS,
    UNMASKED_INPUT_TYPES,
)

CONTROLLER_JS = r"""({values, digits, ignorable, separators, loading, hrefLoading, hrefDrawn, opaque, unmaskedTypes}) => {
  const invisible = new RegExp(`[\\s${ignorable}]+`, 'gu');
  const invisibleOrSeparator = new RegExp(`[\\s${ignorable}${separators}]+`, 'gu');
  const gap = `[\\s${ignorable}]*`;
  const digitGap = `[\\s${ignorable}${separators}]*`;
  const normalize = (text) => String(text ?? '').normalize('NFKC').replace(invisible, '').toLowerCase();
  const digitsForm = (text) => String(text ?? '').normalize('NFKC').replace(invisibleOrSeparator, '');
  const secrets = values.filter((value) => typeof value === 'string' && value.length > 0);
  const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const patterns = [
    ...secrets.map((secret) => new RegExp(Array.from(secret).map(escapeRegex).join(gap), 'giu')),
    ...digits.map((needle) => new RegExp(Array.from(needle).join(digitGap), 'gu')),
  ];
  const needles = [...new Set(secrets.map(normalize).filter((needle) => needle.length > 0))];
  const holds = (value) => {
    if (value === null || value === undefined) return false;
    const text = normalize(value);
    if (needles.some((needle) => text.includes(needle))) return true;
    const numbers = digitsForm(value);
    return digits.some((needle) => numbers.includes(needle));
  };
  const redact = (value) => {
    const safe = patterns.reduce((text, pattern) => text.replace(pattern, '<redacted>'), String(value ?? ''));
    return holds(safe) ? '<redacted>' : safe;
  };
  const OPAQUE = new Set(opaque);
  const LOADING = new Set(loading);
  const HREF_LOADS = new Set(hrefLoading);
  const HREF_DRAWN = new Set(hrefDrawn);
  const LINK_ANIMATIONS = new Set(['ANIMATE', 'SET']);
  const UNMASKED = new Set(unmaskedTypes);
  const EDITABLE = new Set(['INPUT', 'TEXTAREA']);
  // Opacity, because content a <use> draws can set its own visibility; no transition, because a
  // transition outranks even an !important declaration while it runs.
  const HIDDEN_STYLE = [['transition', 'none'], ['visibility', 'hidden'], ['opacity', '0']];
  const MASK_STYLE = [['-webkit-text-security', 'disc']];
  // The transition shorthand reads empty while only some of its longhands are set, so each longhand is recorded.
  const TRANSITION = ['transition-property', 'transition-duration', 'transition-timing-function', 'transition-delay', 'transition-behavior'];
  const recorded = (name) => (name === 'transition' ? TRANSITION : [name]);
  // Names are judged by their local part: a prefix (svg:image, x:canvas, q:href) changes nothing Chrome draws.
  const tag = (element) => {
    const name = String((element && (element.localName || element.tagName)) || '');
    return name.slice(name.indexOf(':') + 1).toUpperCase();
  };
  const localName = (name) => {
    const text = String(name ?? '');
    return text.slice(text.indexOf(':') + 1).toLowerCase();
  };
  const isLink = (name) => localName(name) === 'href';
  const loads = (element, name) => LOADING.has(String(name ?? '').toLowerCase()) || (isLink(name) && HREF_LOADS.has(tag(element)));
  // Chrome animates from the attributes with no namespace; a namespaced one of the same name is a decoy.
  const own = (element, name) => element.getAttributeNS(null, name);
  // A <use> whose link animation only names fragments of this document draws page content, which is redacted.
  // A link names this document only when it starts with '#'. Chrome reads to, from and by as written, so ' #t', ' '
  // and '' resolve against the base address; it strips ASCII whitespace from each values item and skips an empty one.
  const listItem = (value) => value.replace(/^[\t\n\f\r ]+|[\t\n\f\r ]+$/g, '');
  const fragmentsOnly = (animation) => {
    const links = ['to', 'from', 'by'].map((name) => own(animation, name)).filter((value) => value !== null);
    const values = own(animation, 'values');
    if (values !== null) links.push(...values.split(';').map(listItem).filter((value) => value.length > 0));
    return links.length > 0 && links.every((value) => value.startsWith('#'));
  };
  // An <animate> or <set> can change the link an SVG image draws without changing the page, and the
  // animated value is not the attribute, so the image an SVG link animation targets is judged unseen.
  const animatedLinkTarget = (element) => {
    if (!LINK_ANIMATIONS.has(tag(element)) || !isLink(own(element, 'attributeName'))) return null;
    const target = element.targetElement;
    if (!target || !HREF_DRAWN.has(tag(target))) return null;
    return tag(target) === 'USE' && fragmentsOnly(element) ? null : target;
  };
  const maskable = (element) => tag(element) === 'TEXTAREA'
    || (tag(element) === 'INPUT' && !UNMASKED.has(String(element.getAttribute('type') ?? '').toLowerCase()));
  const changes = [];
  const styled = new Map();
  const shielded = new Set();
  const masked = new Set();
  const unhooks = [];
  const listeners = [];
  let roots = [document];
  let observer = null;
  let latent = false;
  let changed = 0;
  let restored = false;
  const collectRoots = (closedRoots) => {
    const found = [document, ...closedRoots];
    for (let index = 0; index < found.length; index += 1) {
      for (const element of found[index].querySelectorAll('*')) {
        if (element.shadowRoot && !found.includes(element.shadowRoot)) found.push(element.shadowRoot);
      }
    }
    return found;
  };
  const scan = (visit) => {
    for (const root of roots) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) visit('text', walker.currentNode);
      for (const element of root.querySelectorAll('*')) visit('element', element);
    }
  };
  const style = (element, declarations) => {
    if (!element.style) return;
    const record = styled.get(element) || {before: element.getAttribute('style'), previous: [], at: changes.length};
    for (const [declared] of declarations) {
      for (const name of recorded(declared)) {
        if (record.previous.some(([known]) => known === name)) continue;
        record.previous.push([name, element.style.getPropertyValue(name), element.style.getPropertyPriority(name)]);
      }
    }
    for (const [name, value] of declarations) element.style.setProperty(name, value, 'important');
    record.after = element.getAttribute('style');
    styled.set(element, record);
  };
  const hide = (element) => {
    shielded.add(element);
    style(element, HIDDEN_STYLE);
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
  const mask = (element) => {
    masked.add(element);
    style(element, MASK_STYLE);
  };
  // Transitions come back last, once the element's style has settled, so a transition of its own
  // does not replay from the hidden state.
  // Reading the attribute first writes a style changed through CSSOM back to it; otherwise Chrome writes it back
  // later, as an empty style attribute, after the restore has removed the attribute.
  const settle = (element) => {
    element.getAttribute('style');
    return getComputedStyle(element).opacity;
  };
  const putBack = (element, entries) => {
    for (const [name, value, priority] of entries) {
      if (value) element.style.setProperty(name, value, priority);
      else element.style.removeProperty(name);
    }
  };
  const unstyle = (element, {before, after, previous}) => {
    const setBefore = () => (before === null ? element.removeAttribute('style') : element.setAttribute('style', before));
    if (element.getAttribute('style') === after) {
      setBefore();
      element.style.setProperty('transition', 'none', 'important');
      settle(element);
      setBefore();
      return;
    }
    putBack(element, previous.filter(([name]) => !TRANSITION.includes(name)));
    settle(element);
    putBack(element, previous.filter(([name]) => TRANSITION.includes(name)));
  };
  const undo = (change, index) => {
    const [kind, node] = change;
    if (kind === 'text') {
      node.nodeValue = change[2];
      return;
    }
    // A style attribute redacted before its element was hidden or masked comes back with that styling, so the
    // element's transitions stay off until its style has settled.
    const isStyle = kind === 'attribute' && node.namespaceURI === null && node.localName === 'style';
    const record = isStyle ? styled.get(node.ownerElement) : undefined;
    if (record && index < record.at) {
      // Redacted before its element was styled: the restore puts this value back whole with transitions held off, and a
      // restyle by the page meanwhile is lost. A value redacted after the styling rolls back into the styled attribute
      // below, which the restore then replaces with the style from before it.
      record.before = change[2];
      record.after = node.value;
      return;
    }
    // An attribute change holds its attribute node, and a control change its element: both undo through value.
    node.value = change[2];
  };
  const count = (records) => {
    for (const record of records) {
      if (latent || styled.has(record.target) || holds(record.target.getAttribute('style'))) changed += 1;
    }
  };
  const hookSetter = (prototype, name) => {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
    if (!descriptor || !descriptor.configurable || !descriptor.get || !descriptor.set) return;
    const counted = function counted(value) {
      const before = descriptor.get.call(this);
      descriptor.set.call(this, value);
      if (descriptor.get.call(this) !== before) changed += 1;
    };
    Object.defineProperty(prototype, name, {...descriptor, set: counted});
    unhooks.push(() => Object.defineProperty(prototype, name, descriptor));
  };
  const hookValidity = (prototype) => {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'setCustomValidity');
    if (!descriptor || !descriptor.configurable || typeof descriptor.value !== 'function') return;
    const original = descriptor.value;
    const counted = function setCustomValidity(message) {
      const before = this.validationMessage;
      const result = original.call(this, message);
      if (this.validationMessage !== before) changed += 1;
      return result;
    };
    Object.defineProperty(prototype, 'setCustomValidity', {...descriptor, value: counted});
    unhooks.push(() => Object.defineProperty(prototype, 'setCustomValidity', descriptor));
  };
  // A view transition draws a raster of its scope taken before it began, which no redaction reaches. It runs on the
  // document or on any element.
  const onElement = (root) => Array.from(root.querySelectorAll('*')).some((element) => element.activeViewTransition);
  const viewTransitionRunning = () => {
    if (typeof document.startViewTransition !== 'function') return 0;
    if ('activeViewTransition' in document) return document.activeViewTransition || roots.some(onElement) ? 1 : 0;
    try {
      return document.documentElement.matches(':active-view-transition') ? 1 : 0;
    } catch (error) {
      return 1;
    }
  };
  return {
    redact(...closedRoots) {
      roots = collectRoots(closedRoots);
      scan((kind, node) => {
        if (kind === 'text') {
          // A textarea's text is its default value; the textarea is masked instead.
          if (tag(node.parentNode) === 'TEXTAREA') return;
          const safe = redact(node.nodeValue);
          if (safe !== node.nodeValue) {
            changes.push(['text', node, node.nodeValue]);
            node.nodeValue = safe;
          }
          return;
        }
        if (OPAQUE.has(tag(node))) hide(node);
        const animatedLink = animatedLinkTarget(node);
        if (animatedLink) shield(animatedLink);
        const maskedKind = maskable(node);
        if (maskedKind && (holds(node.value) || holds(node.getAttribute('value')) || holds(node.textContent))) mask(node);
        for (const attribute of Array.from(node.attributes || [])) {
          if (!holds(attribute.value)) continue;
          if (loads(node, attribute.name)) {
            shield(node);
            continue;
          }
          if (maskedKind && attribute.name === 'value') continue;
          // Through the attribute node: setAttribute lowercases an HTML name and picks the first attribute of that name.
          changes.push(['attribute', attribute, attribute.value]);
          attribute.value = redact(attribute.value);
        }
        if (!maskedKind && EDITABLE.has(tag(node)) && node.type !== 'file' && holds(node.value)) {
          changes.push(['value', node, node.value]);
          node.value = redact(node.value);
        }
      });
    },
    watch(isLatent) {
      latent = Boolean(isLatent);
      observer = new MutationObserver(count);
      for (const root of roots) observer.observe(root, {subtree: true, attributes: true, attributeFilter: ['style']});
      for (const name of ['checked', 'indeterminate']) hookSetter(HTMLInputElement.prototype, name);
      hookSetter(HTMLOptionElement.prototype, 'selected');
      for (const type of [HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement, HTMLButtonElement]) {
        hookValidity(type.prototype);
      }
      for (const type of ['focusin', 'focusout', 'hashchange']) {
        const listener = () => { changed += 1; };
        window.addEventListener(type, listener, true);
        listeners.push([type, listener]);
      }
    },
    verify() {
      if (observer) count(observer.takeRecords());
      let remaining = 0;
      scan((kind, node) => {
        if (kind === 'text') {
          if (holds(node.nodeValue) && !masked.has(node.parentNode)) remaining += 1;
          return;
        }
        // A filter image has no box to hide.
        const animatedLink = animatedLinkTarget(node);
        if (animatedLink && (!shielded.has(animatedLink) || tag(animatedLink) === 'FEIMAGE')) remaining += 1;
        for (const attribute of Array.from(node.attributes || [])) {
          if (!holds(attribute.value)) continue;
          if (loads(node, attribute.name) && shielded.has(node)) continue;
          if (attribute.name === 'value' && masked.has(node)) continue;
          remaining += 1;
        }
        if (EDITABLE.has(tag(node)) && !masked.has(node) && holds(node.value)) remaining += 1;
      });
      return {changed, remaining, transitioning: viewTransitionRunning()};
    },
    restore() {
      if (restored) return;
      restored = true;
      if (observer) observer.disconnect();
      for (const [type, listener] of listeners) window.removeEventListener(type, listener, true);
      for (let index = unhooks.length - 1; index >= 0; index -= 1) unhooks[index]();
      for (let index = changes.length - 1; index >= 0; index -= 1) undo(changes[index], index);
      for (const [element, record] of styled) unstyle(element, record);
    },
  };
}"""


#: Ends every running view transition, whose raster of its scope's old state no redaction reaches, and waits
#: for each to finish; the caller then waits for their pseudo-elements to go, reading DevTools rather than the page. One runs on the document or on any element in it or in a shadow root; the caller passes
#: the closed shadow roots. Frames are hidden, so a transition inside one is never drawn.
END_VIEW_TRANSITIONS_JS = r"""async function endViewTransitions(...closedRoots) {
  const running = new Set();
  if (document.activeViewTransition) running.add(document.activeViewTransition);
  const roots = [document, ...closedRoots];
  for (let index = 0; index < roots.length; index += 1) {
    for (const element of roots[index].querySelectorAll('*')) {
      if (element.shadowRoot && !roots.includes(element.shadowRoot)) roots.push(element.shadowRoot);
      if (element.activeViewTransition) running.add(element.activeViewTransition);
    }
  }
  for (const transition of running) transition.skipTransition();
  await Promise.all(Array.from(running, (transition) => transition.finished.catch(() => undefined)));
  return running.size > 0;
}"""


def controller_argument(values: list[str]) -> dict[str, Any]:
    """The argument ``CONTROLLER_JS`` takes: the value spellings and every shared matching table."""
    digits = {needle for value in values if isinstance(value, str) for needle in digit_needles(value)}
    return {
        "values": list(values),
        "digits": sorted(digits, key=lambda needle: (-len(needle), needle)),
        "ignorable": JS_IGNORABLE_CLASS,
        "separators": JS_DIGIT_SEPARATOR_CLASS,
        "loading": sorted(LOADING_ATTRIBUTES),
        "hrefLoading": sorted(HREF_LOADING_ELEMENTS),
        "hrefDrawn": sorted(HREF_DRAWN_ELEMENTS),
        "opaque": sorted(OPAQUE_ELEMENTS),
        "unmaskedTypes": sorted(UNMASKED_INPUT_TYPES),
    }
