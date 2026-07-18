from app.multitask.base import BaseProviderHandler
from app.multitask.common import VISIBLE_HELPER, js_string


class PerplexityHandler(BaseProviderHandler):
    composer_selector = 'textarea, div[contenteditable="true"], [role="textbox"]'

    def focus_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const boxes = Array.from(document.querySelectorAll({js_string(self.composer_selector)})).filter(portalVisible);
            const box = boxes[boxes.length - 1];
            if (!box) return "no_composer";
            box.scrollIntoView({{block:"center", inline:"center"}}); box.focus();
            try {{ document.execCommand('selectAll', false, null); document.execCommand('delete', false, null); }} catch(e) {{}}
            if (box.tagName === "TEXTAREA" || box.tagName === "INPUT") {{ box.value = ""; box.dispatchEvent(new InputEvent("input", {{bubbles:true}})); }}
            return "focused";
        }})();
        '''

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const buttons = Array.from(document.querySelectorAll('button')).filter(portalVisible).reverse();
            const btn = buttons.find(b => {{
                const label = (b.getAttribute('aria-label') || b.getAttribute('title') || b.innerText || '').toLowerCase();
                return !b.disabled && b.getAttribute('aria-disabled') !== 'true' && (label.includes('submit') || label.includes('send') || label.includes('ask'));
            }});
            if (btn) {{ btn.click(); return "sent"; }}
            const active = document.activeElement;
            if (active) {{ active.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}})); return "sent"; }}
            return "no_send";
        }})();
        '''
