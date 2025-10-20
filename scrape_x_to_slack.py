import os, json, re, asyncio, requests, sys, time
from pathlib import Path
from playwright.async_api import async_playwright

# --- Ortam değişkenleri ---
X_HANDLE = os.environ.get("X_HANDLE", "replicate")
DEBUG = os.environ.get("DEBUG") == "1"
FORCE_POST = os.environ.get("FORCE_POST") == "1"
IGNORE_FIRST_POST = os.environ.get("IGNORE_FIRST_POST") == "1"
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
        except Exception as e:
            log("State load error:", e)
            return None
    return None

def save_last_id(tid):
    try:
        STATE_FILE.write_text(json.dumps({"last_id": tid}))
    except Exception as e:
        print("ERROR saving state:", e, file=sys.stderr)

def extract_max_status_ids(text, handle=None):
    """
    Önce handle'a özgü linkleri ara (daha temiz), bulunamazsa genel fallback.
    max'ı sayısal olarak al (lexicographic bug fix).
    """
    ids = []
    if handle:
        pat = rf'https?://x\.com/{re.escape(handle)}/status/(\d+)'
        ids = re.findall(pat, text)

    if not ids:
        ids = re.findall(r"/status/(\d+)", text)

    if not ids:
        return None
    try:
        return str(max(int(x) for x in ids))
    except Exception:
        # Son çare: string max (çok düşük ihtimalle) — ama yine de dön.
        return max(ids)

def fetch_via_rjina(handle):
    """X sayfasını r.jina.ai üzerinden düz metin gibi çeker (çok sağlam)."""
    try:
        url = f"https://r.jina.ai/http://x.com/{handle}"
        log("r.jina.ai GET:", url)
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and r.text:
            tid = extract_max_status_ids(r.text, handle)
            log("r.jina.ai latest id:", tid)
            return tid
        log("r.jina.ai status:", r.status_code, r.text[:200])
    except Exception as e:
        log("r.jina.ai error:", e)
    return None

async def fetch_via_playwright(handle):
    """Eğer r.jina.ai başarısız olursa X sayfasını Playwright ile çeker."""
    browser = None
    try:
        url = f"https://x.com/{handle}"
        log("Playwright GET:", url)
        async with async_playwright() as p:
            # Firefox kullanmaya devam (repo kurulumuna uyumlu)
            browser = await p.firefox.launch(headless=True)
            page = await browser.new_page(user_agent="Mozilla/5.0")
            await page.goto(url, wait_until="networkidle", timeout=90_000)
            html = await page.content()
            tid = extract_max_status_ids(html, handle)
            log("Playwright latest id:", tid)
            return tid
    except Exception as e:
        log("Playwright error:", e)
        return None
    finally:
        try:
            if browser:
                await browser.close()
        except Exception:
            pass

# --- Slack gönderimi ---
def post_to_slack(tweet_id, handle):
    link = f"https://x.com/{handle}/status/{tweet_id}"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    data = {
        "channel": SLACK_CHANNEL_ID,
        "text": link,
        # klasik önizleme için açık kalsın
        "unfurl_links": True,
        "unfurl_media": True,
    }

    def _send():
        return requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=headers, json=data, timeout=20
        )

    r = _send()
    if r.status_code == 429:
        retry = int(r.headers.get("Retry-After", "5"))
        log(f"Slack 429 rate limited, retrying in {retry}s…")
        time.sleep(retry)
        r = _send()

    try:
        payload = r.json()
    except Exception:
        payload = {"ok": False, "error": f"non-json response", "status": r.status_code}

    if DEBUG:
        log("Slack response:", r.status_code, payload)

    if not payload.get("ok", False):
        raise RuntimeError(f"Slack post failed: {payload.get('error', 'unknown')}")

# --- Ana a
