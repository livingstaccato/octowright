// Tiny DOM helpers shared by the panel renderers.
export function cell(child: Node): HTMLTableCellElement {
  const td = document.createElement("td");
  td.append(child);
  return td;
}

export function textCell(text: string): Text {
  return document.createTextNode(text);
}

export function linkCell(text: string, href: string): HTMLAnchorElement {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  return a;
}
