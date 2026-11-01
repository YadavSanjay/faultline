"""Probe FINRA API to discover correct request format."""
import base64, os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env
client_id     = os.getenv("FINRA_API_KEY", "")
client_secret = os.getenv("FINRA_API_KEY_SECRET", "")

# Get token
cred = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
r = requests.post(
    "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token",
    params={"grant_type": "client_credentials"},
    headers={"Authorization": f"Basic {cred}", "Content-Type": "application/x-www-form-urlencoded"},
    timeout=30,
)
print("Token response:", r.status_code)
token = r.json().get("access_token", "")
print("Token length:", len(token))

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
base = "https://api.finra.org"

# Test 1: GET with no body (some FINRA APIs are GET)
for dataset in ["treasuryDailyAggregates", "corporateDebtMarketBreadth"]:
    r = requests.get(f"{base}/data/group/fixedIncomeMarket/name/{dataset}?limit=5", headers=headers, timeout=30)
    print(f"\nGET {dataset}: {r.status_code}  {r.text[:200]}")

# Test 2: POST with compareFilters format
for dataset in ["treasuryDailyAggregates", "corporateDebtMarketBreadth"]:
    body = {
        "limit": 5,
        "offset": 0,
        "compareFilters": [
            {"compareType": "GREATER_THAN_OR_EQUALS", "fieldName": "tradeDate", "fieldValue": "2026-06-01"},
            {"compareType": "LESS_THAN_OR_EQUALS", "fieldName": "tradeDate", "fieldValue": "2026-06-17"},
        ],
    }
    r = requests.post(f"{base}/data/group/fixedIncomeMarket/name/{dataset}", json=body, headers=headers, timeout=30)
    print(f"\nPOST compareFilters {dataset}: {r.status_code}  {r.text[:300]}")

# Test 3: POST with dateRangeFilters format (original)
for dataset in ["treasuryDailyAggregates"]:
    body = {
        "limit": 5,
        "offset": 0,
        "dateRangeFilters": [{"startDate": "2026-06-01", "endDate": "2026-06-17", "fieldName": "tradeDate"}],
    }
    r = requests.post(f"{base}/data/group/fixedIncomeMarket/name/{dataset}", json=body, headers=headers, timeout=30)
    print(f"\nPOST dateRangeFilters {dataset}: {r.status_code}  {r.text[:300]}")
