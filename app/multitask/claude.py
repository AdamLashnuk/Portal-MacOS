from app.multitask.base import BaseProviderHandler
from app.multitask.common import VISIBLE_HELPER


class ClaudeHandler(BaseProviderHandler):
    composer_selector = 'div[contenteditable="true"], [role="textbox"]'

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const btn = Array.from(document.querySelectorAll('button')).filter(portalVisible).reverse().find(b => !b.disabled && ((b.getAttribute('aria-label')||b.getAttribute('title')||b.innerText||'').toLowerCase().includes('send')));
            if (!btn) return "no_send";
            btn.click(); return "sent";
        }})();
        '''
