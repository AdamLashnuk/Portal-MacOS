from app.multitask.common import VISIBLE_HELPER, js_string


class BaseProviderHandler:
    composer_selector = 'textarea, [contenteditable="true"], [role="textbox"]'
    send_selector = 'button[aria-label*="Send"], button[data-testid*="send"], button[type="submit"]'

    def focus_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const boxes = Array.from(document.querySelectorAll({js_string(self.composer_selector)})).filter(portalVisible);
            const box = boxes[boxes.length - 1];
            if (!box) return "no_composer";
            box.scrollIntoView({{block: "center", inline: "center"}});
            box.focus();
            return "focused";
        }})();
        '''

    def clear_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const boxes = Array.from(document.querySelectorAll({js_string(self.composer_selector)})).filter(portalVisible);
            const box = boxes[boxes.length - 1];
            if (!box) return "no_composer";
            box.focus();
            try {{ document.execCommand('selectAll', false, null); document.execCommand('delete', false, null); }} catch(e) {{}}
            if (box.tagName === "TEXTAREA" || box.tagName === "INPUT") {{ box.value = ""; }}
            box.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "deleteContentBackward" }}));
            return "cleared";
        }})();
        '''

    def verify_js(self, prompt):
        prompt_json = js_string(prompt.strip())
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const expected = {prompt_json};
            const boxes = Array.from(document.querySelectorAll({js_string(self.composer_selector)})).filter(portalVisible);
            const box = boxes[boxes.length - 1];
            if (!box) return "no_composer";
            const text = (box.value || box.innerText || box.textContent || "").trim();
            return text.includes(expected) ? "ready" : "not_ready";
        }})();
        '''

    def send_js(self):
        return f'''
        (() => {{
            {VISIBLE_HELPER}
            const buttons = Array.from(document.querySelectorAll({js_string(self.send_selector)})).filter(portalVisible).reverse();
            const btn = buttons.find(b => !b.disabled && b.getAttribute("aria-disabled") !== "true");
            if (!btn) return "no_send";
            btn.click();
            return "sent";
        }})();
        '''
