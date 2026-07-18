import json

VISIBLE_HELPER = """
function portalVisible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
}
"""


def js_string(value: str) -> str:
    return json.dumps(value)
