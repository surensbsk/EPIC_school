import io
import re
import requests
import pdfplumber
from datetime import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP()

MENU_URL = "https://docs.isitesoftware.com/snaf-assets/snaf-static/greenmenus/1596058691337/2026/4/859209-April_2026_FINAL_RIA_B_L.pdf"


@mcp.tool()
def get_todays_meal() -> str:
    """
    Fetches the current day's meal from the April 2026 school lunch menu PDF.
    Downloads the PDF, extracts text, and returns the meal listed for today's date.
    """
    today = datetime.now()
    day = today.day          # e.g. 10
    month_name = today.strftime("%B")  # e.g. "April"

    # Download PDF into memory
    response = requests.get(MENU_URL, timeout=15)
    response.raise_for_status()

    full_text = ""
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

    if not full_text.strip():
        return "Could not extract text from the menu PDF."

    # Find block of text near today's date number.
    # The menu typically has day numbers followed by meal items on the same/next lines.
    # We look for the day number as a standalone token and grab the surrounding lines.
    lines = full_text.splitlines()
    results = []
    for i, line in enumerate(lines):
        # Match the day number as a whole word/token (e.g. " 10 " or line starting with "10")
        if re.search(rf"(?<!\d){day}(?!\d)", line):
            # Collect this line and the next 3 lines as the meal block
            block = lines[i : i + 4]
            results.append("\n".join(block).strip())

    if results:
        meal_info = f"Meal for {month_name} {day}:\n\n" + "\n\n---\n\n".join(results)
    else:
        meal_info = (
            f"No meal entry found for {month_name} {day} in the menu.\n\n"
        )

    return meal_info


if __name__ == "__main__":
    mcp.run()
