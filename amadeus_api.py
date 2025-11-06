import os
import requests
import json
import time
from dotenv import load_dotenv

load_dotenv()
AMADEUS_CLIENT_ID = os.getenv("AMADEUS_CLIENT_ID")
AMADEUS_CLIENT_SECRET = os.getenv("AMADEUS_CLIENT_SECRET")
BASE_URL = "https://test.api.amadeus.com"

# Global token cache
_token_cache = {"token": None, "expires_at": 0}

def get_access_token():
    """Fetch OAuth2 access token with caching."""
    global _token_cache
    current_time = time.time()
    # Reuse token if not expired (25min to be safe)
    if _token_cache["token"] and current_time < _token_cache["expires_at"]:
        return _token_cache["token"]

    url = f"{BASE_URL}/v1/security/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": AMADEUS_CLIENT_ID,
        "client_secret": AMADEUS_CLIENT_SECRET
    }
    try:
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Auth error: {e}")
        return None
    token_data = response.json()
    _token_cache["token"] = token_data["access_token"]
    _token_cache["expires_at"] = current_time + token_data["expires_in"] - 300  # 5min buffer
    return _token_cache["token"]

def search_hotels(params):
    """Search hotels with at least 2 params, sorted by price ascending."""
    print(f"Searching hotels with params: {params}")
    required_params = ["cityCode", "checkInDate"]
    if len(params) < 2 or not all(p in params for p in required_params):
        return {"error": "Need at least cityCode (IATA) and checkInDate parameters."}

    defaults = {
        "checkOutDate": params.get("checkOutDate"),
        "adults": params.get("adults", 1),
        "roomQuantity": params.get("roomQuantity", 1),
        "sort": "PRICE"
    }
    query_params = {**params, **defaults}

    token = get_access_token()
    if not token:
        return {"error": "Failed to authenticate with Amadeus."}

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{BASE_URL}/v3/shopping/hotel-offers"

    try:
        response = requests.get(url, headers=headers, params=query_params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"API error: {e}")
        return {"error": f"API error: {str(e)}"}

    data = response.json().get("data", [])
    print(f"Received {len(data)} hotel results")

    results = []
    for hotel in data[:5]:
        offer = hotel.get("offers", [{}])[0]
        price = offer.get("price", {}).get("total", "N/A")
        results.append({
            "hotelName": hotel.get("hotel", {}).get("name", "Unknown"),
            "price": price,
            "currency": offer.get("price", {}).get("currency", "USD"),
            "address": hotel.get("hotel", {}).get("address", {}).get("lines", ["N/A"])[0]
        })

    return {"hotels": results}