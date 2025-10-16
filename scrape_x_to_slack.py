import os, json, re, asyncio, requests, sys
from pathlib import Path
from playwright.async_api import async_playwright

# --- Ortam değişkenleri ---
X_HANDLE = os.environ.get("X_HANDLE", "replicate")
DEBUG = os.environ.get("DEBUG") == "1"
FORCE_POST = os.environ.get("FORCE_POST") == "1"
STATE_FILE = Path("state.json")

# Slack App token ve kanal ID'si (chat.postMessage için)
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]

# --- Yardımcı fonksiyonlar ---
def log(*a):
    if DEBUG:
        print(*a, file=sys.stderr)

def load_last_id():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("last_id")
        except Exception:
            return None
    return None

def save_last_id(tid):
    STATE_FILE.write_text(json.dumps({"last_id": tid}))

def extract_max_status_ids(text):
    ids = re.findall(r"/status/(\d+)", text)
    return max(ids) if ids else None

def fetch_via_rjina(handle):
    """X sayfasını r.jina.ai üzerinden düz metin gibi çeker (çok sağlam)."""
    url = f"https://r.jina.ai/http://x.com/{handle}"
    log("r.jina.ai GET:", url)
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code == 200 and r.text:
        tid = extract_max_status_ids(r.text)
        log("r.jina.ai latest id:", tid)
        return tid
    log("r.jina.ai status:", r.status_code)
    return None

async def fetch_via_playwright(handle):
    """Eğer r.jina.ai başarısız olursa X sayfasını Playwright ile çeker."""
    url = f"https://x.com/{handle}"
    log("Playwright GET:", url)
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0")
        try:
            await page.goto(url, wait_until="networkidle", timeout=90000)
            html = await page.content()
            tid = extract_max_status_ids(html)
            log("Playwright latest id:", tid)
            return tid
        finally:
            await browser.close()

# --- Yeni: Tweet metninden kısa özet (snippet) çek ---
def fetch_tweet_snippet(handle, tweet_id, max_len=240):
    try:
        url = f"https://r.jina.ai/http://x.com/{handle}/status/{tweet_id}"
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.text:
            text = r.text
            # Fazla boşlukları sadeleştir
            text = re.sub(r"\s+", " ", text).strip()
            # Çok uzunsa kısalt
            if len(text) > max_len:
                text = text[: max_len - 1] + "…"
            return text
    except Exception:
        pass
    return None

# --- Güncellendi: Block Kit ile detaylı mesaj gönder ---
def post_to_slack(tweet_id, handle):
    link = f"https://x.com/{handle}/status/{tweet_id}"
    snippet = fetch_tweet_snippet(handle, tweet_id) or f"@{handle} yeni bir paylaşım yaptı."

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📰 @{handle} — Yeni paylaşım", "emoji": True},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{snippet}\n\n🔗 *Link:* <{link}|X’te aç>"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"ID: `{tweet_id}`"},
                {"type": "mrkdwn", "text": "Kaynak: X"},
            ],
        },
        # İstersen buton ekleyebilirsin:
        # {
        #     "type": "actions",
        #     "elements": [
        #         {"type": "button", "text": {"type": "plain_text", "text": "X’te Aç"}, "url": link}
        #     ]
        # }
    ]

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    data = {
        "channel": SLACK_CHANNEL_ID,
        "text": f"@{handle} yeni paylaşım: {link}",  # fallback (bildirim/arama için)
        "blocks": blocks,
        # thread_ts GÖNDERME → her seferinde yeni mesaj olsun
        "unfurl_links": False,   # Link önizlemesini kapatıp blokları kullan
        "unfurl_media": False,
    }

    r = requests.post("https://slack.com/api/chat.postMessage", headers=headers, json=data, timeout=15)
    if DEBUG:
        print("Slack response:", r.status_code, r.text, file=sys.stderr)

# --- Ana akış ---
async def main():
    last = load_last_id()
    log("Handle:", X_HANDLE, "LastID:", last)

    # 1) Önce r.jina.ai dene (çok hızlı)
    latest = fetch_via_rjina(X_HANDLE)

    # 2) Olmazsa Playwright fallback
    if not latest:
        latest = await fetch_via_playwright(X_HANDLE)

    log("LatestID found:", latest)

    if latest and (FORCE_POST or latest != last):
        post_to_slack(latest, X_HANDLE)
        save_last_id(latest)
        log("Posted and saved:", latest)
    else:
        log("No new tweet (or FORCE_POST disabled).")

if __name__ == "__main__":
    asyncio.run(main())
