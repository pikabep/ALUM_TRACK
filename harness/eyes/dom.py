"""DOM eyes: number every visible interactive element so the model can act by `ref`.

Each element gets a `data-agent-id` attribute, so the browser hand finds it again
with a plain CSS selector and the two modules share no state. Refs go stale when
the page changes; the hand then says so and the loop observes again.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_SNAPSHOT_JS = r"""
({start, maxEls, maxText, viewportOnly}) => {
  for (const e of document.querySelectorAll('[data-agent-id]')) e.removeAttribute('data-agent-id');
  const W = window.innerWidth, H = window.innerHeight;
  const NATIVE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY']);
  const ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'option', 'combobox', 'textbox', 'searchbox', 'switch', 'slider', 'spinbutton',
    'treeitem', 'listbox', 'gridcell']);
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;

  const interactive = (el, st) => {
    if (NATIVE.has(el.tagName))
      return !(el.tagName === 'A' && !el.hasAttribute('href') && st.cursor !== 'pointer');
    const role = el.getAttribute('role');
    if (role && ROLES.has(role)) return true;
    if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) return true;
    if (el.hasAttribute('onclick')) return true;
    const ti = el.getAttribute('tabindex');
    if (ti !== null && Number(ti) >= 0) return true;
    if (st.cursor === 'pointer') {  // framework click handlers (Angular, React) leave only this trace
      const p = el.parentElement;
      return !p || getComputedStyle(p).cursor !== 'pointer';
    }
    return false;
  };

  const nameOf = el => {
    const bits = [];
    const push = s => { s = clean(s); if (s && !bits.includes(s)) bits.push(s); };
    push(el.getAttribute('aria-label'));
    if (el.labels && el.labels.length) push(el.labels[0].innerText);
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') push(el.placeholder);
    else if (tag === 'SELECT') push(el.options[el.selectedIndex] && el.options[el.selectedIndex].text);
    else push(el.innerText);
    if (!bits.length) {
      push(el.getAttribute('title'));
      const img = el.querySelector && el.querySelector('img[alt]');
      push(el.getAttribute('alt') || (img && img.alt));
      if (!bits.length) push(el.getAttribute('name') || el.id);
    }
    return clip(bits.join(' | '), 100);
  };

  const lines = [], boxes = [];
  let id = start, offscreen = 0, omitted = 0;
  const all = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || Number(st.opacity) === 0) continue;
    if (!interactive(el, st)) continue;
    const inView = r.bottom > 0 && r.right > 0 && r.top < H && r.left < W;
    if (!inView) {
      offscreen++;
      if (viewportOnly) continue;
    } else {
      // Skip elements hidden behind a modal or loader: a click would land on the overlay.
      const cx = Math.min(Math.max(r.left + r.width / 2, 0), W - 1);
      const cy = Math.min(Math.max(r.top + r.height / 2, 0), H - 1);
      const top = document.elementFromPoint(cx, cy);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) continue;
    }
    if (lines.length >= maxEls) { omitted++; continue; }

    el.setAttribute('data-agent-id', String(id));
    const tag = el.tagName.toLowerCase(), role = el.getAttribute('role');
    let kind = tag;
    if (tag === 'input') kind = `input[${el.type}]`;
    else if (role) kind = `${tag}[role=${role}]`;
    else if (!NATIVE.has(el.tagName)) kind = `${tag}(clickable)`;
    const parts = [`[${id}] ${kind}`];
    const name = nameOf(el);
    if (name) parts.push(JSON.stringify(name));
    if ((tag === 'input' || tag === 'textarea') && el.value && !['checkbox', 'radio'].includes(el.type))
      parts.push(el.type === 'password' ? 'value=<hidden>' : `value=${JSON.stringify(clip(el.value, 60))}`);
    if (tag === 'select')
      parts.push(`options=${JSON.stringify(Array.from(el.options).slice(0, 20).map(o => clean(o.text)))}`);
    if (el.checked || el.getAttribute('aria-checked') === 'true') parts.push('checked');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') parts.push('disabled');
    if (el.getAttribute('aria-expanded')) parts.push(`expanded=${el.getAttribute('aria-expanded')}`);
    if (el.getAttribute('aria-selected') === 'true') parts.push('selected');
    if (!inView) parts.push('(offscreen)');
    lines.push(parts.join(' '));
    boxes.push({id, x: r.left, y: r.top, w: r.width, h: r.height});
    id++;
  }
  const text = (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').trim();
  return {url: location.href, title: document.title, lines, boxes, offscreen, omitted, nextId: id,
          text: clip(text, maxText), textLen: text.length};
}
"""


@dataclass
class DomSnapshot:
    url: str
    title: str
    lines: list[str] = field(default_factory=list)
    boxes: list[dict] = field(default_factory=list)  # CSS px relative to the top-level viewport
    text: str = ""
    offscreen: int = 0
    omitted: int = 0
    notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        out = [f"URL: {self.url}", f"Title: {self.title}", "", "## Interactive elements (act on them by ref)"]
        out += self.lines or ["(none visible)"]
        if self.omitted:
            out.append(f"({self.omitted} more not listed: element cap reached)")
        if self.offscreen:
            out.append(f"({self.offscreen} more outside the viewport: scroll to reveal)")
        out += [*self.notes, "", "## Page text", self.text]
        return "\n".join(out)


def snapshot(page, max_elements: int = 150, max_text: int = 6000, viewport_only: bool = True) -> DomSnapshot:
    """Snapshot the page and all of its frames. `page` is a Playwright sync Page."""
    try:
        title = page.title()
    except Exception:
        title = ""
    snap = DomSnapshot(url=page.url, title=title)
    next_id = 1
    for frame in page.frames:
        if frame.is_detached():
            continue
        dx = dy = 0.0
        is_main = frame == page.main_frame
        if not is_main:
            try:
                box = frame.frame_element().bounding_box()
            except Exception:
                continue
            if not box or box["width"] < 2 or box["height"] < 2:
                continue  # hidden iframe (trackers, analytics)
            dx, dy = box["x"], box["y"]
        budget = max_elements - len(snap.lines)
        try:
            res = frame.evaluate(
                _SNAPSHOT_JS,
                {"start": next_id, "maxEls": max(budget, 0), "viewportOnly": viewport_only,
                 "maxText": max_text if is_main else max_text // 4},
            )
        except Exception as e:
            snap.notes.append(f"(frame {frame.url[:80]} could not be read: {str(e).splitlines()[0]})")
            continue
        next_id = res["nextId"]
        if not is_main and res["lines"]:
            snap.lines.append(f"-- inside iframe {frame.url[:100]} --")
        snap.lines += res["lines"]
        snap.boxes += [{**b, "x": b["x"] + dx, "y": b["y"] + dy} for b in res["boxes"]]
        snap.offscreen += res["offscreen"]
        snap.omitted += res["omitted"]
        if is_main:
            snap.text = res["text"]
            if res["textLen"] > len(res["text"]):
                snap.text += f"\n[page text truncated: {res['textLen']} chars total; use browser_extract for more]"
        elif res["text"]:
            snap.text += f"\n\n[iframe {frame.url[:100]}]\n{res['text']}"
    return snap
