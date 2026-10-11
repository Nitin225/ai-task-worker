from pathlib import Path
from playwright.sync_api import sync_playwright

DATA_DIR = Path("data").resolve()

SNAPSHOT_JS = """
() => {
  document.querySelectorAll('[data-agent-id]').forEach(e => e.removeAttribute('data-agent-id'));
  const els = [...document.querySelectorAll('a, button, input, select, textarea')]
    .filter(e => e.offsetParent !== null);
  els.forEach((e, i) => e.setAttribute('data-agent-id', i + 1));
  return els.map((e, i) => ({
    idx: i + 1,
    tag: e.tagName.toLowerCase(),
    type: e.getAttribute('type') || '',
    name: e.getAttribute('name') || '',
    text: (e.innerText || '').trim().slice(0, 60),
    value: (e.value || '').slice(0, 40),
    label: (e.labels && e.labels[0]) ? e.labels[0].innerText.trim().slice(0, 40) : '',
    href: e.getAttribute('href') || ''
  }));
}
"""


class Browser:
    def __init__(self, headless=False):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self.page = self._browser.new_page()

    def snapshot(self):
        items = self.page.evaluate(SNAPSHOT_JS)
        lines = [f"URL: {self.page.url}", f"TITLE: {self.page.title()}", "ELEMENTS:"]
        for it in items:
            desc = f"[{it['idx']}] {it['tag']}"
            if it["type"]:
                desc += f"({it['type']})"
            if it["name"]:
                desc += f" name={it['name']}"
            if it["label"]:
                desc += f' label="{it["label"]}"'
            if it["text"]:
                desc += f' text="{it["text"]}"'
            if it["value"]:
                desc += f' value="{it["value"]}"'
            if it["href"]:
                desc += f" href={it['href']}"
            lines.append(desc)
        body = self.page.inner_text("body")[:900]
        lines += ["PAGE TEXT:", body]
        return "\n".join(lines)
    
    def describe(self, idx):
        return self.page.evaluate(
            """(idx) => {
                const el = document.querySelector(`[data-agent-id="${idx}"]`);
                if (!el) return null;
                const form = el.closest('form');
                const els = form
                    ? [...form.querySelectorAll('input:not([type=password]), select, textarea')]
                    : [];
                return {
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || '').trim(),
                    has_password: form ? form.querySelector('input[type=password]') !== null : false,
                    fields: els.map(f => [f.name || f.type, f.value]),
                    empty_required: els.filter(f => f.required && !f.value).map(f => f.name || f.type)
                };
            }""",
            idx,
    )

    def click(self, idx):
        is_submit = self.page.evaluate(
            """(i) => {
                const e = document.querySelector(`[data-agent-id="${i}"]`);
                return !!e && (e.tagName === 'BUTTON' || e.type === 'submit');
            }""",
            idx,
        )
        before_url = self.page.url
        before_text = self.page.inner_text("body")
        self.page.click(f'[data-agent-id="{idx}"]', timeout=5000)
        self.page.wait_for_load_state("load")
        snap = self.snapshot()
        warn = ""
        if self.page.url == before_url:
            invalid = []
            if is_submit:
                invalid = self.page.evaluate(
                    "() => [...document.querySelectorAll(':invalid')].filter(e => e.name).map(e => e.name)"
                )
            if invalid:
                warn = f"WARNING: submit was blocked by form validation. Empty/invalid fields: {', '.join(invalid)}\n"
            elif self.page.inner_text("body") == before_text:
                warn = "WARNING: the page did not change after this click.\n"
        return warn + snap

    def open(self, url):
        self.page.goto(url, wait_until="load", timeout=10000)
        return self.snapshot()

    def type(self, idx, text):
        self.page.fill(f'[data-agent-id="{idx}"]', str(text), timeout=5000)
        return f"OK: typed into [{idx}]"
    
    def fill(self, fields):
        """fields: dict of input name/label -> value. Fills several inputs in one call."""
        done, missing = [], []
        for key, value in fields.items():
            loc = self.page.locator(f'[name="{key}"]')
            if loc.count() == 0:
                loc = self.page.get_by_label(key)
            if loc.count() == 0:
                missing.append(key)
                continue
            loc.first.fill(str(value), timeout=5000)
            done.append(key)
        msg = f"OK: filled {', '.join(done) or 'nothing'}"
        if missing:
            msg += f". ERROR: no input found for: {', '.join(missing)}"
        return msg

    def screenshot(self, path):
        self.page.screenshot(path=path)
        return path

    def close(self):
        self._browser.close()
        self._pw.stop()


def _safe_path(path):
    p = Path(path or "data").resolve()
    if DATA_DIR not in p.parents and p != DATA_DIR:
        raise PermissionError(
            "Only paths under 'data/' are allowed, e.g. 'data' or 'data/invoices'"
        )
    return p


def list_files(path):
    p = _safe_path(path)
    return "\n".join(sorted(f.name + ("/" if f.is_dir() else "") for f in p.iterdir()))


def read_file(path):
    return _safe_path(path).read_text(encoding="utf-8")

