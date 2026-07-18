from app.multitask.base import BaseProviderHandler
from app.multitask.common import VISIBLE_HELPER


class DeepSeekHandler(BaseProviderHandler):
    composer_selector = 'textarea, div[contenteditable="true"], [role="textbox"]'

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const buttons = Array.from(document.querySelectorAll('button')).filter(portalVisible).reverse();
            const btn = buttons.find(b => {{
                const label = (b.getAttribute('aria-label') || b.getAttribute('title') || b.innerText || '').toLowerCase();
                if (b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
                if (label.includes('send')) return true;
                const r = b.getBoundingClientRect();
                return r.width >= 28 && r.width <= 70 && r.height >= 28 && r.height <= 70 && b.querySelector('svg');
            }});
            if (btn) {{ btn.click(); return "sent"; }}
            const active = document.activeElement;
            if (active) {{ active.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}})); return "sent"; }}
            return "no_send";
        }})();
        '''
