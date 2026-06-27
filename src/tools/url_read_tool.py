"""
url_read_tool.py - Fast Webpage URL Content Reader for Omni-Dev

Fetches web pages via HTTP request and extracts clean readable text using BeautifulSoup.
Ideal for reading documentation, blog posts, and search result links fast.
"""
import requests
from bs4 import BeautifulSoup
from typing import Any, Dict
from src.tools.base_tool import BaseTool


class UrlReadTool(BaseTool):
    """Tool for fetching and reading full content from web URLs."""

    @property
    def name(self) -> str:
        return "read_url_content"

    @property
    def description(self) -> str:
        return (
            "Fetch and read the entire text content of a webpage URL via fast HTTP request. "
            "Automatically removes boilerplate navigation/scripts and returns clean text. "
            "Use this to inspect links from search results or read online documentation."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "url": {
                "type": "string",
                "description": "The web URL to fetch and read (e.g. 'https://example.com/docs').",
            },
        }

    @property
    def required_params(self):
        return ["url"]

    def is_read_only(self) -> bool:
        return True

    def needs_permissions(self, input_args: Dict[str, Any]) -> bool:
        return False

    async def call(self, url: str, **kwargs) -> str:
        """Fetch URL content and convert HTML to clean text."""
        if not url:
            return "Error: URL is required."
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OmniDev/2.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                return f"Failed to fetch URL {url}. HTTP Status Code: {response.status_code}"

            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                return f"JSON Content from {url}:\n{response.text[:8000]}"
            if "text/plain" in content_type:
                return f"Plain Text from {url}:\n{response.text[:8000]}"

            # Parse HTML with BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")

            # Remove unwanted tags
            for element in soup(["script", "style", "noscript", "header", "footer", "nav", "svg", "iframe"]):
                element.decompose()

            # Get title
            title = soup.title.string.strip() if soup.title and soup.title.string else url

            # Extract main readable content (prioritize <main>, <article>, or fallback to <body>)
            main_content = soup.find("main") or soup.find("article") or soup.body or soup

            # Get text with clean line breaks
            lines = [line.strip() for line in main_content.get_text(separator="\n").splitlines()]
            clean_text = "\n".join(line for line in lines if line)

            if not clean_text:
                return f"Title: {title}\n(No readable text extracted. Page might rely on dynamic JavaScript. Consider using `browser_action` with action='extract')."

            # Truncate if over 8000 characters (~2000 tokens) to keep prompt light
            if len(clean_text) > 8000:
                clean_text = clean_text[:8000] + "\n\n... (Content truncated to 8000 chars. Use browser_action or grep if searching for specific items)"

            return f"Webpage Title: {title}\nURL: {url}\n\nContent:\n{clean_text}"

        except requests.Timeout:
            return f"Timeout fetching {url}. The site took too long to respond."
        except Exception as e:
            return f"Error reading URL {url}: {str(e)}"
