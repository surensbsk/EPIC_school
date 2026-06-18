import asyncio
import subprocess
import sys
from datetime import datetime, timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ── Default notification time ─────────────────────────────────────────────────
DEFAULT_HOUR = 8
DEFAULT_MINUTE = 0

# Path to the server script (same directory as this client)
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(_HERE, "EPIC_Server.py")


# ── MCP helpers ───────────────────────────────────────────────────────────────

async def fetch_todays_meal() -> str:
    """Connect to EPIC_Server via stdio and call get_todays_meal."""
    server_params = StdioServerParameters(
        command="uv",
        args=["run", SERVER_SCRIPT],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_todays_meal", {})
            # result.content is a list of TextContent/ImageContent etc.
            texts = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(texts) if texts else "No meal info returned."


# ── Notification helpers ──────────────────────────────────────────────────────

def send_mac_notification(title: str, message: str) -> None:
    """Send a native macOS notification via osascript."""
    # Escape double-quotes inside the message so the AppleScript string stays valid
    safe_msg = message.replace('"', '\\"').replace("\n", " ")[:250]
    safe_title = title.replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True)
    except Exception as exc:
        print(f"[WARN] Could not send macOS notification: {exc}")


# ── Scheduling helpers ────────────────────────────────────────────────────────

def seconds_until(hour: int, minute: int) -> float:
    """Return seconds until the next occurrence of HH:MM (today or tomorrow)."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run_once() -> None:
    """Fetch and notify immediately (useful for testing)."""
    print("Fetching today's meal from EPIC_Server …")
    meal = await fetch_todays_meal()
    print(f"\n{meal}\n")
    send_mac_notification("Today's School Lunch", meal)
    print("Notification sent.")


async def daily_loop(hour: int, minute: int) -> None:
    """Wait until HH:MM each day, fetch the meal, and send a notification."""
    print(
        f"Scheduler started — daily meal notification at {hour:02d}:{minute:02d} "
        f"(press Ctrl+C to stop)"
    )
    while True:
        wait = seconds_until(hour, minute)
        next_time = (datetime.now() + timedelta(seconds=wait)).strftime("%Y-%m-%d %H:%M:%S")
        print(f"Next notification scheduled at {next_time} ({wait / 3600:.2f} h from now) …")

        await asyncio.sleep(wait)

        print(f"[{datetime.now():%H:%M:%S}] Fetching today's meal …")
        try:
            meal = await fetch_todays_meal()
            print(meal)
            send_mac_notification("Today's School Lunch", meal)
            print("Notification sent.")
        except Exception as exc:
            print(f"[ERROR] Failed to fetch meal: {exc}")

        # Sleep 60 s to avoid double-firing on the same minute
        await asyncio.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """
    Usage:
        python EPIC_Client.py              # schedule at 08:00 every day
        python EPIC_Client.py 09:30        # schedule at 09:30 every day
        python EPIC_Client.py --now        # fetch & notify immediately, then exit
    """
    args = sys.argv[1:]

    if "--now" in args:
        asyncio.run(run_once())
        return

    hour, minute = DEFAULT_HOUR, DEFAULT_MINUTE
    for arg in args:
        if ":" in arg:
            try:
                h, m = arg.split(":")
                hour, minute = int(h), int(m)
            except ValueError:
                print(f"Invalid time format '{arg}'. Expected HH:MM.")
                sys.exit(1)

    asyncio.run(daily_loop(hour, minute))


if __name__ == "__main__":
    main()
