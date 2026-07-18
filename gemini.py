from app.multitask.base import BaseProviderHandler
from app.multitask.common import VISIBLE_HELPER


class GeminiHandler(BaseProviderHandler):
    composer_selector = 'rich-textarea div[contenteditable="true"], div[contenteditable="true"][aria-label], [role="textbox"], textarea'

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const selectors = ['button[aria-label*="Send"]','button[mattooltip*="Send"]','button.send-button','button[type="submit"]'];
            for (const selector of selectors) {{
                const btn = Array.from(document.querySelectorAll(selector)).find(portalVisible);
                if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {{ btn.click(); return "sent"; }}
            }}
            const btn = Array.from(document.querySelectorAll('button')).filter(portalVisible).reverse().find(b => !b.disabled && ((b.getAttribute('aria-label')||b.getAttribute('title')||b.innerText||'').toLowerCase().includes('send')));
            if (!btn) return "no_send";
            btn.click(); return "sent";
        }})();
        '''
