"""Try Stooq alternative URL paths that may bypass JS rendering."""
import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env
key = os.getenv("STOOQ_API_KEY", "")
h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

tests = [
    # Alternative Stooq path: /q/l/ with format parameters
    f"https://stooq.com/q/l/?s=nvda.us&f=d&h&e=csv",
    f"https://stooq.com/q/l/?s=nvda.us&f=d&h&e=csv&key={key}",
    # Another variant
    f"https://stooq.com/q/d/l/?s=nvda.us&i=d&d1=20260601&d2=20260617&key={key}&o=d",
    # From data dir — the sample file header format
    "https://stooq.com/q/d/l/?s=nvda.us&i=d",
]

for url in tests:
    try:
        r = requests.get(url, headers=h, timeout=20)
        first = r.text.strip().split("\n")[0]
        is_csv = first.startswith("Date") or first.startswith('"Date"') or "," in first[:20]
        print(f"  {r.status_code}  {'CSV' if is_csv else 'HTML'}")
        print(f"    URL: {url[:90]}")
        print(f"    first: {first[:80]}")
    except Exception as e:
        print(f"  ERR  {url[:60]}: {e}")
