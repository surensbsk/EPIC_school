import base64
import io
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv

load_dotenv()

import anthropic
import pdfplumber
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ── Constants ─────────────────────────────────────────────────────────────────

MENU_URL = (
    "https://docs.isitesoftware.com/snaf-assets/snaf-static/greenmenus/"
    "1596058691337/2026/4/859209-April_2026_FINAL_RIA_B_L.pdf"
)

BASE_URL = "https://www.riacademies.net"
SEED_URLS = [
    "https://www.riacademies.net/",
    "https://www.riacademies.net/epic.aspx",
    "https://www.riacademies.net/ritecha.aspx",
    "https://www.riacademies.net/spark.aspx",
    "https://www.riacademies.net/steam.aspx",
    "https://www.riacademies.net/rihs.aspx",
    "https://www.riacademies.net/ria_about.aspx",
    "https://www.riacademies.net/ria_enrollment.aspx",
    "https://www.riacademies.net/ria_calendars.aspx",
    "https://www.riacademies.net/employment.aspx",
]

# Known image-based PDF calendars — always read via vision
CALENDAR_PDFS = [
    "https://www.riacademies.net/Downloads/25-26_studentCalendar4.pdf",
    "https://www.riacademies.net/Downloads/26-27_studentCalendar.pdf",
]
MAX_PAGES = 100

MODEL = "claude-haiku-4-5"

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SKIP_PATTERNS = re.compile(
    r"(login|logout|payment|linq|sitemap|search|javascript)", re.IGNORECASE
)
MAX_PDFS = 20  # cap so we don't pull every file on the site


# ── School website scraper ────────────────────────────────────────────────────

def _is_internal_page(url: str) -> bool:
    p = urlparse(url)
    if p.netloc not in ("www.riacademies.net", "riacademies.net"):
        return False
    path = p.path.lower()
    return path.endswith(".aspx") or path in ("", "/")


def _is_pdf(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def _ocr_pdf_url(url: str) -> str:
    """Download a PDF, render each page as an image, OCR with Claude vision."""
    resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    client = anthropic.Anthropic()
    ocr_parts: list[str] = []

    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            # Try text extraction first
            t = page.extract_text()
            if t and t.strip():
                ocr_parts.append(t)
                continue

            # Image-based page — render and send to Claude vision
            img = page.to_image(resolution=120)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.standard_b64encode(buf.getvalue()).decode()

            vision_resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {
                            "type": "text",
                            "text": (
                                "This is a school calendar. Extract ALL information: "
                                "school name, year, start/end dates, every holiday, break, "
                                "minimum day, staff development day, and event with its date. "
                                "Format as structured plain text."
                            ),
                        },
                    ],
                }],
            )
            ocr_parts.append(vision_resp.content[0].text)

    label = url.split("/")[-1]
    combined = "\n\n".join(ocr_parts)
    return f"CALENDAR PDF: {label}\nURL: {url}\n\n{combined}"


def _fetch_pdf(url: str) -> str:
    """Download a text-based PDF and return extracted text (no vision fallback)."""
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=30, allow_redirects=True)
        resp.raise_for_status()
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(parts))
        label = url.split("/")[-1]
        return f"PDF: {label}\nURL: {url}\n\n{text}" if text.strip() else ""
    except Exception:
        return ""


@st.cache_data(ttl=3600, show_spinner=False)
def load_calendar_sections() -> tuple[list[str], list[str], list[str]]:
    """OCR all calendar PDFs. Returns (sections, loaded_urls, error_messages)."""
    sections: list[str] = []
    loaded: list[str] = []
    errors: list[str] = []
    for url in CALENDAR_PDFS:
        try:
            text = _ocr_pdf_url(url)
            sections.append(text)
            loaded.append(url)
        except Exception as exc:
            errors.append(f"{url.split('/')[-1]}: {exc}")
        time.sleep(1)
    return sections, loaded, errors


def _fetch_page(url: str) -> tuple[str, list[str], list[str]]:
    """Return (page_text, internal_aspx_links, pdf_links)."""
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return "", [], []

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else url

    content_area = (
        soup.find("div", {"id": re.compile(r"content|main|body", re.I)})
        or soup.find("main")
        or soup.body
        or soup
    )
    text = content_area.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    page_links: list[str] = []
    pdf_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:")):
            continue
        full = urljoin(url, href).split("#")[0]
        if SKIP_PATTERNS.search(full):
            continue
        if _is_pdf(full):
            pdf_links.append(full)
        elif _is_internal_page(full):
            page_links.append(full)

    return (
        f"PAGE TITLE: {page_title}\nURL: {url}\n\n{text}",
        list(set(page_links)),
        list(set(pdf_links)),
    )


@st.cache_data(ttl=3600, show_spinner=False)
def scrape_school_site() -> tuple[list[str], list[str]]:
    """Return (content_sections, visited_urls). Sections are kept separate for retrieval."""
    visited_pages: set[str] = set()
    visited_pdfs: set[str] = set()
    queue: list[str] = list(SEED_URLS)
    sections: list[str] = []
    visited_list: list[str] = []

    # Pre-mark calendar PDFs as visited so the crawler doesn't re-fetch them
    for pdf_url in CALENDAR_PDFS:
        visited_pdfs.add(pdf_url)

    while queue and len(visited_pages) < MAX_PAGES:
        url = queue.pop(0).rstrip("/") or BASE_URL
        if url in visited_pages:
            continue
        visited_pages.add(url)

        text, page_links, pdf_links = _fetch_page(url)
        if text:
            sections.append(text)
            visited_list.append(url)

        for link in page_links:
            link = link.rstrip("/") or BASE_URL
            if link not in visited_pages and link not in queue:
                queue.append(link)

        for pdf_url in pdf_links:
            if pdf_url in visited_pdfs or len(visited_pdfs) >= MAX_PDFS:
                continue
            visited_pdfs.add(pdf_url)
            pdf_text = _fetch_pdf(pdf_url)
            if pdf_text:
                sections.append(pdf_text)
                visited_list.append(pdf_url)
            time.sleep(0.2)

        time.sleep(0.2)

    return sections, visited_list


# ~10 K tokens of content per request — safe for 50 K token/min tier
# (leaves room for system prompt, history, and response)
_MAX_CONTEXT_CHARS = 36_000   # ~9 K tokens
_MAX_SECTIONS = 6
_STOP_WORDS = {
    "what", "when", "where", "who", "how", "is", "the", "a", "an", "of",
    "in", "at", "to", "for", "on", "are", "was", "were", "will", "be",
    "do", "does", "did", "can", "could", "would", "should", "this", "that",
}


def _retrieve_context(query: str, sections: list[str]) -> str:
    """Return the top-scoring sections that fit inside the token budget."""
    keywords = {
        w.lower() for w in re.findall(r"\w+", query)
        if w.lower() not in _STOP_WORDS and len(w) > 2
    }

    def score(section: str) -> int:
        low = section.lower()
        return sum(low.count(kw) for kw in keywords)

    ranked = sorted(sections, key=score, reverse=True) if keywords else sections

    selected: list[str] = []
    total = 0
    for section in ranked[:_MAX_SECTIONS * 3]:   # score top candidates, pick best fitting
        if len(selected) >= _MAX_SECTIONS:
            break
        if total + len(section) > _MAX_CONTEXT_CHARS:
            continue
        selected.append(section)
        total += len(section)

    return "\n\n---\n\n".join(selected)


def _group_pages(pages: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {
        "🏠 General": [],
        "🎓 EPIC Academy": [],
        "🔬 RITECHA": [],
        "⚡ SPARK Academy": [],
        "🔧 STEAM Academy": [],
        "🏫 River Islands High": [],
        "📄 PDFs": [],
        "📋 Other": [],
    }
    for url in pages:
        path = url.lower()
        if path.endswith(".pdf"):
            groups["📄 PDFs"].append(url)
        elif "epic" in path:
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


# ── Lunch menu ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def load_menu_text() -> str:
    response = requests.get(MENU_URL, timeout=15)
    response.raise_for_status()
    full_text = ""
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    return full_text


def get_todays_meal(menu_text: str) -> str:
    today = datetime.now()
    day = today.day
    month_name = today.strftime("%B")
    lines = menu_text.splitlines()
    results = []
    for i, line in enumerate(lines):
        if re.search(rf"(?<!\d){day}(?!\d)", line):
            block = lines[i : i + 4]
            results.append("\n".join(block).strip())
    if results:
        return f"Meal for {month_name} {day}:\n\n" + "\n\n---\n\n".join(results)
    return f"No meal entry found for {month_name} {day} in the menu."


# ── Claude helper ─────────────────────────────────────────────────────────────

def ask_claude(question: str, context: str, history: list[dict], system_prompt: str) -> str:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": f"{system_prompt}\n\nCONTENT:\n{context}",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=history + [{"role": "user", "content": question}],
    )
    return response.content[0].text


# ── App ───────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="RIA Assistant",
        page_icon="🏫",
        layout="centered",
    )

    # iPhone "Add to Home Screen" + mobile viewport meta tags
    st.markdown(
        """
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-title" content="RIA Assistant">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
        <style>
            /* Tighter padding on small screens */
            @media (max-width: 768px) {
                .block-container { padding: 0.5rem 0.75rem 5rem !important; }
                h1 { font-size: 1.4rem !important; }
                /* Keep chat input pinned above the browser nav bar */
                .stChatFloatingInputContainer { bottom: 1rem !important; }
                /* Sidebar hidden by default on mobile — user swipes to open */
                section[data-testid="stSidebar"] { min-width: 85vw !important; }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("🏫 RIA Assistant")

    tab_school, tab_menu = st.tabs(["🏫 School Information", "🍽️ Lunch Menu"])

    # ── Tab 1: School chatbot ──────────────────────────────────────────────────
    with tab_school:
        st.caption("Ask anything about EPIC · RITECHA · SPARK · STEAM · River Islands High")

        with st.spinner("Loading school website…"):
            try:
                sections, pages = scrape_school_site()
            except Exception as exc:
                st.error(f"Failed to load website: {exc}")
                sections, pages = [], []

        with st.spinner("Reading school calendars (OCR)…"):
            try:
                cal_sections, cal_urls, cal_errors = load_calendar_sections()
            except Exception as exc:
                cal_sections, cal_urls, cal_errors = [], [], [str(exc)]

        # Combine: calendars first so they score high for date-related queries
        all_sections = cal_sections + sections
        all_pages = cal_urls + pages

        with st.sidebar:
            st.header("📄 Content loaded")

            # Calendar status always shown prominently
            st.markdown("**📅 Calendars**")
            for url in cal_urls:
                st.markdown(f"✅ {url.split('/')[-1]}")
            for err in cal_errors:
                st.error(f"❌ {err}")

            st.divider()
            for group, urls in _group_pages(pages).items():
                with st.expander(f"{group} ({len(urls)})"):
                    for url in urls:
                        label = url.split("/")[-1].replace(".aspx", "") or "home"
                        st.markdown(f"- [{label}]({url})")
            st.divider()
            total_chars = sum(len(s) for s in all_sections)
            st.caption(f"**{len(all_pages)} sources** · {total_chars:,} chars")
            if st.button("🔄 Refresh all data"):
                scrape_school_site.clear()
                load_calendar_sections.clear()
                st.rerun()

        if "school_messages" not in st.session_state:
            st.session_state.school_messages: list[dict] = []

        for msg in st.session_state.school_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input(
            "e.g. What schools are part of RIA? How do I enroll? What is the EPIC uniform?",
            key="school_input",
        ):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.school_messages.append({"role": "user", "content": prompt})

            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    try:
                        system = (
                            "You are a helpful assistant for River Islands Academies (RIA) "
                            "in Lathrop, CA. RIA operates five schools: EPIC Academy, "
                            "RITECHA, SPARK Academy, STEAM Academy, and River Islands High School. "
                            "Answer questions using the website content. Be concise and friendly. "
                            "If the answer spans multiple schools, clarify which school each fact "
                            "applies to. If something isn't in the content, say so and direct the "
                            "user to call 209-717-6700 or visit riacademies.net."
                        )
                        history = st.session_state.school_messages[:-1]
                        reply = ask_claude(prompt, _retrieve_context(prompt, all_sections), history, system)
                        st.markdown(reply)
                        st.session_state.school_messages.append(
                            {"role": "assistant", "content": reply}
                        )
                    except anthropic.AuthenticationError:
                        st.error("Add ANTHROPIC_API_KEY to your .env file and restart.")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

    # ── Tab 2: Lunch menu ──────────────────────────────────────────────────────
    with tab_menu:
        st.caption("Powered by Claude · Ask anything about the school lunch menu")

        with st.spinner("Loading lunch menu…"):
            try:
                menu_text = load_menu_text()
            except Exception as exc:
                st.error(f"Could not load the menu PDF: {exc}")
                menu_text = ""

        if menu_text:
            st.subheader("📅 Today's Meal")
            st.info(get_todays_meal(menu_text))
            st.divider()
            st.subheader("💬 Ask About the Menu")

        if "menu_messages" not in st.session_state:
            st.session_state.menu_messages: list[dict] = []

        for msg in st.session_state.menu_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        if prompt := st.chat_input(
            "e.g. What's on Friday? Are there vegetarian options?",
            key="menu_input",
        ):
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.menu_messages.append({"role": "user", "content": prompt})

            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    try:
                        system = (
                            "You are a helpful school lunch assistant. Answer questions about "
                            "the school lunch menu clearly and concisely. If a question is "
                            "unrelated to the menu, politely redirect the conversation."
                        )
                        history = st.session_state.menu_messages[:-1]
                        reply = ask_claude(prompt, menu_text, history, system)
                        st.markdown(reply)
                        st.session_state.menu_messages.append(
                            {"role": "assistant", "content": reply}
                        )
                    except anthropic.AuthenticationError:
                        st.error("Add ANTHROPIC_API_KEY to your .env file and restart.")
                    except Exception as exc:
                        st.error(f"Error: {exc}")


if __name__ == "__main__":
    main()
