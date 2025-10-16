import os, json, re, asyncio, requests
from pathlib import Path
from playwright.async_api import async_playwright

SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]
X_HANDLE = os.environ.get("X_HANDLE", "replicate")
STATE_FILE = Path("state.json")

def load_last_id():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text()).get("last_id")
    return None

def save_last_id(tid):
    STATE_FILE.write_text(json.dumps({"last_id": tid}))

async def get_latest_tweet_id(page, handle):
    url = f"https://x.com/{handle}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    anchors = await page.locator('a[href*="/status/"]').all()
    ids = []
    for a in anchors:
        href = await a.get_attribute("href")
        if href and "/status/" in href:
            m = re.search(r"/status/(\d+)", href)
            if m:
                ids.append(m.group(1))
    return max(ids) if ids else None

def post_to_slack(tweet_id, handle):
    link = f"https://x.com/{handle}/status/{tweet_id}"
    payload = {
        "text": f"🐦 Yeni tweet @{handle} hesabından!",
        "url": link
    }
    requests.post(SLACK_WEBHOOK, json=payload)

async def main():
    last = load_last_id()
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        latest = await get_latest_tweet_id(page, X_HANDLE)
        await browser.close()
    if latest and latest != last:
        post_to_slack(latest, X_HANDLE)
        save_last_id(latest)

if __name__ == "__main__":
    asyncio.run(main())
