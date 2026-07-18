from app.multitask.base import BaseProviderHandler
from app.multitask.common import VISIBLE_HELPER


class ChatGPTHandler(BaseProviderHandler):
    composer_selector = '#prompt-textarea, div[contenteditable="true"][id="prompt-textarea"], textarea'

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}

            const composer = Array.from(document.querySelectorAll(
                '#prompt-textarea, div[contenteditable="true"][id="prompt-textarea"], textarea'
            )).find(portalVisible);

            if (!composer) {{
                return "no_composer";
            }}

            const text = (
                composer.value ||
                composer.innerText ||
                composer.textContent ||
                ""
            ).trim();

            if (!text) {{
                return "empty";
            }}

            const selectors = [
                'button[data-testid="send-button"]',
                'button[aria-label="Send prompt"]',
                'button[aria-label="Send message"]',
                'button[type="submit"]'
            ];

            for (const selector of selectors) {{
                const btn = Array.from(document.querySelectorAll(selector))
                    .filter(portalVisible)
                    .find(btn =>
                        !btn.disabled &&
                        btn.getAttribute("aria-disabled") !== "true"
                    );

                if (btn) {{
                    btn.click();
                    return "sent";
                }}
            }}

            return "no_send";
        }})();
        '''