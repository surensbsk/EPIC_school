import re
import time
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv

load_dotenv()

import anthropic
import requests
import streamlit as st
from bs4 import BeautifulSoup

BASE_URL = "https://www.riacademies.net"
MAX_PAGES = 100

# Seed every known top-level section so BFS reaches the whole site
SEED_URLS = [
    "https://www.riacademies.net/",
    "https://www.riacademies.net/epic.aspx",
    "https://www.riacademies.net/ritecha.aspx",
    "https://www.riacademies.net/spark.aspx",
    "https://www.riacademies.net/steam.aspx",
    "https://www.riacademies.net/rihs.aspx",
    "https://www.riacademies.net/about.aspx",
    "https://www.riacademies.net/enrollment.aspx",
    "https://www.riacademies.net/calendars.aspx",
    "https://www.riacademies.net/employment.aspx",
]

MODEL = "claude-haiku-4-5"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Pages that add no useful content (pure nav / login / external tools)
SKIP_PATTERNS = re.compile(
    r"(login|logout|payment|linq|sitemap|search|javascript)", re.IGNORECASE
)


def _is_internal(url: str) -> bool:
    p = urlparse(url)
    if p.netloc not in ("www.riacademies.net", "riacademies.net"):
        return False
    path = p.path.lower()
    return path.endswith(".aspx") or path in ("", "/")


def _fetch(url: str) -> tuple[str, list[str]]:
    """Return (clean_text, outgoing_internal_links) for a single page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return "", []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove navigation, scripts, footers — keep only body content
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    # Pull page title for labelling
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else url

    # Main content area (fall back to body)
    content_area = (
        soup.find("div", {"id": re.compile(r"content|main|body", re.I)})
        or soup.find("main")
        or soup.body
        or soup
    )

    text = content_area.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Collect internal links
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:") or href.startswith("tel:"):
            continue
        full = urljoin(BASE_URL, href).split("#")[0]
        if _is_internal(full) and not SKIP_PATTERNS.search(full):
            links.append(full)

    return f"PAGE TITLE: {page_title}\nURL: {url}\n\n{text}", list(set(links))


@st.cache_data(ttl=3600, show_spinner=False)
def scrape_site() -> tuple[str, list[str]]:
    """BFS crawl of the entire riacademies.net site."""
    visited: set[str] = set()
    queue: list[str] = list(SEED_URLS)
    sections: list[str] = []
    visited_list: list[str] = []

    while queue and len(visited) < MAX_PAGES:
        url = queue.pop(0)
        # Normalise trailing slash
        url = url.rstrip("/") or BASE_URL
        if url in visited:
            continue
        visited.add(url)

        text, links = _fetch(url)
        if text:
            sections.append(f"=== SECTION ===\n{text}")
            visited_list.append(url)

        for link in links:
            link = link.rstrip("/") or BASE_URL
            if link not in visited and link not in queue:
                queue.append(link)

        time.sleep(0.2)

    return "\n\n".join(sections), visited_list


def _group_pages(pages: list[str]) -> dict[str, list[str]]:
    """Group URLs by school / section for the sidebar."""
    groups: dict[str, list[str]] = {
        "🏠 General": [],
        "🎓 EPIC Academy": [],
        "🔬 RITECHA": [],
        "⚡ SPARK Academy": [],
        "🔧 STEAM Academy": [],
        "🏫 River Islands High": [],
        "📋 Other": [],
    }
    for url in pages:
        path = url.lower()
        if "epic" in path:
            groups["🎓 EPIC Academy"].append(url)
        elif "ritecha" in path:
            groups["🔬 RITECHA"].append(url)
        elif "spark" in path:
            groups["⚡ SPARK Academy"].append(url)
        elif "steam" in path:
            groups["🔧 STEAM Academy"].append(url)
        elif "rihs" in path or "highschool" in path:
            groups["🏫 River Islands High"].append(url)
        elif any(k in path for k in ("about", "enroll", "calendar", "employ", "contact", "govern")):
            groups["🏠 General"].append(url)
        else:
            groups["📋 Other"].append(url)
    return {k: v for k, v in groups.items() if v}


def _ask_claude(question: str, content: str, history: list[dict]) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": (
                    "You are a helpful assistant for River Islands Academies (RIA) in Lathrop, CA. "
                    "RIA operates five schools: EPIC Academy, RITECHA, SPARK Academy, "
                    "STEAM Academy, and River Islands High School.\n\n"
                    "Answer questions using the website content below. Be concise and friendly. "
                    "If the answer spans multiple schools, clarify which school each fact applies to. "
                    "If something isn't in the content, say so and direct the user to "
                    "contact RIA at 209-717-6700 or visit riacademies.net.\n\n"
                    f"WEBSITE CONTENT:\n{content}"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=history + [{"role": "user", "content": question}],
    )
    return response.content[0].text


def main() -> None:
    st.set_page_config(
        page_title="River Islands Academies Chatbot",
        page_icon="🏫",
        layout="centered",
    )
    st.title("🏫 River Islands Academies Chatbot")
    st.caption(
        "Ask anything about EPIC · RITECHA · SPARK · STEAM · River Islands High School"
    )

    with st.spinner("Loading school website — first load takes ~30 seconds…"):
        try:
            content, pages = scrape_site()
        except Exception as exc:
            st.error(f"Failed to load website: {exc}")
            return

    # Sidebar: grouped page list
    with st.sidebar:
        st.header("📄 Pages loaded")
        groups = _group_pages(pages)
        for group, urls in groups.items():
            with st.expander(f"{group} ({len(urls)})"):
                for url in urls:
                    label = url.split("/")[-1].replace(".aspx", "") or "home"
                    st.markdown(f"- [{label}]({url})")
        st.divider()
        st.caption(f"**{len(pages)} pages** · {len(content):,} characters")
        if st.button("🔄 Refresh website data"):
            scrape_site.clear()
            st.rerun()

    # Chat interface
    if "ria_messages" not in st.session_state:
        st.session_state.ria_messages: list[dict] = []

    for msg in st.session_state.ria_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input(
        "e.g. What schools are part of RIA? How do I enroll? What is the EPIC uniform?"
    ):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.ria_messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    history = st.session_state.ria_messages[:-1]
                    reply = _ask_claude(prompt, content, history)
                    st.markdown(reply)
                    st.session_state.ria_messages.append(
                        {"role": "assistant", "content": reply}
                    )
                except anthropic.AuthenticationError:
                    st.error(
                        "Missing or invalid API key. "
                        "Add ANTHROPIC_API_KEY to your .env file and restart."
                    )
                except Exception as exc:
                    st.error(f"Error: {exc}")


if __name__ == "__main__":
    main()
