from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebEngineCore import QWebEnginePage
from app.multitask.chatgpt import ChatGPTHandler
from app.multitask.claude import ClaudeHandler
from app.multitask.deepseek import DeepSeekHandler
from app.multitask.gemini import GeminiHandler
from app.multitask.perplexity import PerplexityHandler


class MultitaskSender(QObject):
    def __init__(self, browser, provider_name="", status_label=None, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.provider_name = provider_name or ""
        self.status_label = status_label
        self.prompt = ""
        self.handler = self._handler_for_provider()
        self.attempts_left = 5

    def _handler_for_provider(self):
        name = self.provider_name.lower()
        url = self.browser.url().toString().lower() if self.browser else ""
        if "chatgpt" in name or "chatgpt.com" in url: return ChatGPTHandler()
        if "gemini" in name or "gemini.google.com" in url: return GeminiHandler()
        if "deepseek" in name or "deepseek" in url: return DeepSeekHandler()
        if "perplexity" in name or "perplexity.ai" in url: return PerplexityHandler()
        if "claude" in name or "claude.ai" in url: return ClaudeHandler()
        return ChatGPTHandler()

    def set_status(self, text, kind="info"):
        if not self.status_label: return
        colors = {"info":"#a5b4fc", "wait":"#facc15", "ok":"#86efac", "bad":"#fca5a5"}
        color = colors.get(kind, colors["info"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"QLabel {{ color: {color}; font-size: 12px; background: transparent; }}")

    def send(self, prompt):
        if not prompt or self.browser.property("multitask_sent"): return
        self.prompt = prompt
        self.attempts_left = 5
        self.set_status("Waiting for composer...", "wait")
        self._focus_composer()

    def _focus_composer(self):
        if self.browser.property("multitask_sent"): return
        self.browser.page().runJavaScript(self.handler.focus_js(), self._after_focus)

    def _after_focus(self, result):
        if self.browser.property("multitask_sent"): return
        if result != "focused":
            self.attempts_left -= 1
            if self.attempts_left > 0:
                self.set_status("Waiting for composer...", "wait")
                QTimer.singleShot(1200, self._focus_composer)
            else:
                self.set_status("Could not find composer", "bad")
            return
        self.set_status("Pasting prompt...", "info")
        self.browser.setFocus()
        QTimer.singleShot(120, self._paste_prompt)

    def _paste_prompt(self):
        if self.browser.property("multitask_sent"): return
        clipboard = QGuiApplication.clipboard()
        self._old_clipboard = clipboard.text()
        clipboard.setText(self.prompt)
        self.browser.page().triggerAction(QWebEnginePage.WebAction.Paste)
        QTimer.singleShot(450, self._restore_clipboard_and_verify)

    def _restore_clipboard_and_verify(self):
        try: QGuiApplication.clipboard().setText(self._old_clipboard)
        except Exception: pass
        self.browser.page().runJavaScript(self.handler.verify_js(self.prompt), self._after_verify)

    def _after_verify(self, result):
        if self.browser.property("multitask_sent"): return
        if result != "ready":
            self.attempts_left -= 1
            if self.attempts_left > 0:
                self.set_status("Retrying composer...", "wait")
                self.browser.page().runJavaScript(self.handler.clear_js())
                QTimer.singleShot(1000, self._focus_composer)
            else:
                self.set_status("Prompt pasted, send failed", "bad")
            return
        self.set_status("Sending...", "info")
        QTimer.singleShot(350, self._click_send)

    def _click_send(self):
        if self.browser.property("multitask_sent"): return
        self.browser.page().runJavaScript(self.handler.send_js(), self._after_send)

    def _after_send(self, result):
        if result == "sent":
            self.browser.setProperty("multitask_sent", True)
            self.set_status("Sent", "ok")
            return
        self.attempts_left -= 1
        if self.attempts_left > 0:
            self.set_status("Retrying send...", "wait")
            QTimer.singleShot(1000, self._click_send)
        else:
            self.set_status("Could not send automatically", "bad")
