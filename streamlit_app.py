import io
import re
from datetime import datetime

import anthropic
import pdfplumber
import requests
import streamlit as st

MENU_URL = (
    "https://docs.isitesoftware.com/snaf-assets/snaf-static/greenmenus/"
    "1596058691337/2026/4/859209-April_2026_FINAL_RIA_B_L.pdf"
)
MODEL = "claude-haiku-4-5"


@st.cache_data(ttl=3600)
def load_menu_text() -> str:
    """Download and extract full text from the lunch menu PDF (cached 1 hour)."""
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
    """Return the meal block for today's date."""
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


def ask_claude(question: str, menu_text: str, history: list[dict]) -> str:
    """Send a question to Claude with the full menu as cached context."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": (
                    "You are a helpful school lunch assistant. Answer questions about "
                    "the school lunch menu clearly and concisely. If a question is "
                    "unrelated to the menu, politely redirect the conversation.\n\n"
                    f"FULL MENU TEXT:\n{menu_text}"
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=history + [{"role": "user", "content": question}],
    )
    return response.content[0].text


def main() -> None:
    st.set_page_config(
        page_title="School Lunch Menu Assistant",
        page_icon="🍽️",
        layout="centered",
    )
    st.title("🍽️ School Lunch Menu Assistant")
    st.caption("Powered by Claude · Ask anything about the school lunch menu")

    # Load and cache the menu
    with st.spinner("Loading lunch menu…"):
        try:
            menu_text = load_menu_text()
        except Exception as exc:
            st.error(f"Could not load the menu PDF: {exc}")
            return

    # Today's meal card
    st.subheader("📅 Today's Meal")
    st.info(get_todays_meal(menu_text))

    st.divider()
    st.subheader("💬 Ask About the Menu")

    # Initialise chat history
    if "messages" not in st.session_state:
        st.session_state.messages: list[dict] = []

    # Render existing conversation
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # New user input
    if prompt := st.chat_input("e.g. What's on Friday? Are there vegetarian options?"):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    # Pass history *without* the just-appended user message
                    history = st.session_state.messages[:-1]
                    reply = ask_claude(prompt, menu_text, history)
                    st.markdown(reply)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": reply}
                    )
                except anthropic.AuthenticationError:
                    st.error(
                        "Missing or invalid API key. "
                        "Set the `ANTHROPIC_API_KEY` environment variable and restart."
                    )
                except Exception as exc:
                    st.error(f"Error: {exc}")


if __name__ == "__main__":
    main()
