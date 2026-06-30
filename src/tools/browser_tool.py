"""
browser_tool.py - Human-like Browser Automation Tool for Omni-Dev

Uses Playwright to let the agent open a real browser window, navigate URLs,
click buttons, type text, scroll pages, and extract data just like a real human.
"""
import asyncio
import os
from typing import Any, Dict, Optional
from src.tools.base_tool import BaseTool
from src import security

# Global browser state to maintain session across multiple tool calls
_pw_instance = None
_browser_instance = None
_page_instance = None
_console_logs = []
_page_errors = []


def _on_console(msg):
    try:
        _console_logs.append(f"[{msg.type}] {msg.text}")
        if len(_console_logs) > 500:
            _console_logs.pop(0)
    except Exception:
        pass


def _on_page_error(exc):
    try:
        _page_errors.append(f"[PAGE ERROR] {exc}")
        if len(_page_errors) > 100:
            _page_errors.pop(0)
    except Exception:
        pass


async def _get_page(headless: bool = False):
    global _pw_instance, _browser_instance, _page_instance, _console_logs, _page_errors
    from playwright.async_api import async_playwright

    if _page_instance is not None and not _page_instance.is_closed():
        return _page_instance

    if _pw_instance is None:
        _pw_instance = await async_playwright().start()

    if _browser_instance is None or not _browser_instance.is_connected():
        # Force headless=False so user ALWAYS sees the real browser window live on screen
        _browser_instance = await _pw_instance.chromium.launch(
            headless=False,
            slow_mo=100,
            args=["--start-maximized", "--disable-infobars", "--auto-open-devtools-for-tabs"]
        )

    # Use no_viewport=True to allow maximized full-screen native window
    context = await _browser_instance.new_context(no_viewport=True)
    _page_instance = await context.new_page()
    _console_logs.clear()
    _page_errors.clear()
    _page_instance.on("console", _on_console)
    _page_instance.on("pageerror", _on_page_error)
    return _page_instance


class BrowserTool(BaseTool):
    """Tool allowing the agent to control a web browser like a human."""

    @property
    def name(self) -> str:
        return "browser_action"

    @property
    def description(self) -> str:
        return (
            "Control a web browser like a real human. Can open visible browser windows, navigate URLs, "
            "click buttons/links, type text into input boxes, scroll pages, extract text content, take screenshots, "
            "and inspect browser developer console logs and page errors ('console'). "
            "The 'evaluate' action runs JavaScript on the page and requires explicit user approval each time."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "action": {
                "type": "string",
                "enum": ["goto", "click", "type", "scroll", "extract", "screenshot", "console", "evaluate", "close"],
                "description": "The action to perform in the browser."
            },
            "url": {
                "type": "string",
                "description": "URL to navigate to (for 'goto' action). e.g. 'https://google.com'."
            },
            "selector": {
                "type": "string",
                "description": "CSS selector or text matching element (for 'click', 'type', 'extract'). e.g. 'input[name=\"q\"]' or 'text=Search'."
            },
            "text": {
                "type": "string",
                "description": "Text to type into the input element (for 'type' action)."
            },
            "script": {
                "type": "string",
                "description": "JavaScript code to evaluate in developer console (for 'evaluate' action). e.g. 'window.localStorage.getItem(\"token\")'."
            },
            "direction": {
                "type": "string",
                "enum": ["down", "up"],
                "description": "Scroll direction (for 'scroll' action). Defaults to 'down'."
            },
            "path": {
                "type": "string",
                "description": "File path to save screenshot (for 'screenshot' action). Defaults to 'browser_shot.png'."
            },
            "headless": {
                "type": "boolean",
                "description": "Whether to run browser invisibly in background. Set to False to show real browser window to user. Defaults to False."
            }
        }

    @property
    def required_params(self):
        return ["action"]

    def is_read_only(self) -> bool:
        return False

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        # Browser actions can navigate to arbitrary sites, interact with
        # authenticated sessions, write files (screenshot), and run JavaScript.
        # Always route through the Permission_Check.
        return True

    async def _confirm_evaluate(self, script: str) -> bool:
        """Prompt the user to approve running arbitrary JavaScript on the page."""
        def _get_approval():
            from rich.console import Console
            from rich.prompt import Prompt
            con = Console(highlight=False)
            preview = script if len(script) <= 500 else script[:500] + " ..."
            con.print(
                "\n[bold red][SECURITY] Agent wants to run JavaScript in the browser:[/bold red]"
            )
            con.print(f"[yellow]{preview}[/yellow]")
            try:
                return Prompt.ask(" [bold yellow]Allow? (y/n)[/bold yellow]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return "n"

        answer = await asyncio.get_event_loop().run_in_executor(None, _get_approval)
        return answer == "y"

    async def call(self, action: str, url: str = "", selector: str = "", text: str = "", script: str = "", direction: str = "down", path: str = "browser_shot.png", headless: bool = False, **kwargs) -> str:
        global _pw_instance, _browser_instance, _page_instance, _console_logs, _page_errors

        try:
            if action == "close":
                if _browser_instance is not None:
                    await _browser_instance.close()
                    _browser_instance = None
                    _page_instance = None
                if _pw_instance is not None:
                    await _pw_instance.stop()
                    _pw_instance = None
                return "Browser closed successfully."

            page = await _get_page(headless=headless)

            if action == "goto":
                if not url:
                    return "Error: URL is required for 'goto' action."
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "https://" + url
                ok, reason = security.validate_outbound_url(url)
                if not ok:
                    return f"Blocked navigation: {reason}"
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                title = await page.title()
                return f"Navigated to {url}. Page Title: '{title}'."

            elif action == "click":
                if not selector:
                    return "Error: Selector is required for 'click' action."
                await page.click(selector, timeout=10000)
                return f"Clicked element matching '{selector}'."

            elif action == "type":
                if not selector or not text:
                    return "Error: Selector and text are required for 'type' action."
                await page.fill(selector, text, timeout=10000)
                return f"Typed '{text}' into element '{selector}'."

            elif action == "scroll":
                pixels = 600 if direction == "down" else -600
                await page.evaluate(f"window.scrollBy(0, {pixels})")
                return f"Scrolled page {direction} by 600 pixels."

            elif action == "extract":
                if selector:
                    element = await page.query_selector(selector)
                    if element:
                        content = await element.inner_text()
                        return f"Extracted text from '{selector}':\n{content[:2000]}"
                    return f"Element '{selector}' not found."
                else:
                    # Extract main visible body text
                    content = await page.evaluate("document.body.innerText")
                    return f"Extracted page content:\n{content[:3000]}..."

            elif action == "screenshot":
                abs_path = os.path.abspath(path)
                ok, reason = security.validate_file_access(abs_path, write=True)
                if not ok:
                    return f"Blocked screenshot: {reason}"
                await page.screenshot(path=abs_path, full_page=False)
                return f"Screenshot saved to {abs_path}."

            elif action == "console":
                output = []
                if _page_errors:
                    output.append("=== Page Exceptions / Errors ===")
                    output.extend(_page_errors)
                if _console_logs:
                    output.append("=== Developer Console Logs ===")
                    output.extend(_console_logs[-100:])
                if not output:
                    return "Developer console is currently empty (no logs or errors recorded since navigation)."
                return "\n".join(output)

            elif action == "evaluate":
                script_to_run = script or text or selector or kwargs.get("script", "")
                if not script_to_run:
                    return "Error: 'script' parameter is required for 'evaluate' action."
                # Arbitrary JavaScript can read auth tokens/cookies from a logged-in
                # session and act on the user's behalf. Require an explicit, per-call
                # confirmation (unless running fully autonomous).
                if not security.is_autonomous():
                    approval = await self._confirm_evaluate(script_to_run)
                    if not approval:
                        return "JavaScript evaluation rejected by user."
                result = await page.evaluate(script_to_run)
                return f"JavaScript Evaluation Result:\n{result}"

            else:
                return f"Error: Unknown action '{action}'."

        except Exception as e:
            return f"Browser Automation Error: {str(e)}"
