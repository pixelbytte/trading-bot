"""
Upstox access token generator.

Upstox tokens expire at midnight IST daily. Run this each morning before
market open to get a fresh token, then update UPSTOX_ACCESS_TOKEN in your
.env file and GitHub Secrets.

Usage:
    python -m scripts.upstox_auth

With TOTP automation (set UPSTOX_TOTP_SECRET in .env):
    Requires: pip install pyotp
    The script will auto-generate the 6-digit TOTP code so you don't need
    to open your authenticator app.

Manual flow (if no TOTP secret):
    The script prints the auth URL. Open it in your browser, log in,
    and paste the redirect URL back. The script extracts the code and
    fetches the token.
"""

import os
import sys
import json
import webbrowser
import requests
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID     = os.getenv("UPSTOX_CLIENT_ID")
CLIENT_SECRET = os.getenv("UPSTOX_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/callback")
TOTP_SECRET   = os.getenv("UPSTOX_TOTP_SECRET")   # optional — base32 TOTP key


def _get_totp() -> str | None:
    if not TOTP_SECRET:
        return None
    try:
        import pyotp
        return pyotp.TOTP(TOTP_SECRET).now()
    except ImportError:
        print("pyotp not installed — run: pip install pyotp")
        return None


def get_auth_code_automated(mobile: str, pin: str, totp: str) -> str | None:
    """
    Automated OAuth flow using Upstox's login API.
    Only works when you have mobile, PIN, and TOTP available programmatically.
    """
    session = requests.Session()

    # Step 1: Get CSRF token
    r = session.get(
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}",
        allow_redirects=False,
    )

    # Step 2: Login with mobile + PIN
    login_r = session.post("https://api.upstox.com/v2/login/authorization/users", json={
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "mobile_number": mobile,
        "pin": pin,
    }, headers={"Accept": "application/json"})

    if login_r.status_code != 200:
        print(f"Login failed: {login_r.text}")
        return None

    # Step 3: TOTP verification
    totp_r = session.post("https://api.upstox.com/v2/login/authorization/totp", json={
        "otp": totp,
    }, headers={"Accept": "application/json"})

    # Extract auth code from redirect
    final_url = totp_r.url or totp_r.headers.get("Location", "")
    params = parse_qs(urlparse(final_url).query)
    code = (params.get("code") or [""])[0]
    return code if code else None


def exchange_code_for_token(auth_code: str) -> str | None:
    """Exchange OAuth auth code for access token."""
    r = requests.post("https://api.upstox.com/v2/login/authorization/token", data={
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})

    if r.status_code == 200:
        return r.json().get("access_token")
    print(f"Token exchange failed: {r.text}")
    return None


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: UPSTOX_CLIENT_ID and UPSTOX_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    mobile = os.getenv("UPSTOX_MOBILE")
    pin    = os.getenv("UPSTOX_PIN")
    totp   = _get_totp()

    # Try automated flow
    if mobile and pin and totp:
        print(f"Attempting automated auth (TOTP: {totp})...")
        auth_code = get_auth_code_automated(mobile, pin, totp)
        if auth_code:
            token = exchange_code_for_token(auth_code)
            if token:
                print(f"\nAccess token generated successfully.")
                print(f"\nAdd this to your .env file:")
                print(f"UPSTOX_ACCESS_TOKEN={token}")
                print(f"\nUpdate GitHub Secret UPSTOX_ACCESS_TOKEN with the same value.")
                return

    # Manual flow fallback
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    )
    print(f"\nOpen this URL in your browser and log in:")
    print(f"\n  {auth_url}\n")
    webbrowser.open(auth_url)

    redirect = input("After login, paste the full redirect URL here: ").strip()
    params = parse_qs(urlparse(redirect).query)
    auth_code = (params.get("code") or [""])[0]

    if not auth_code:
        print("Could not extract auth code from URL.")
        sys.exit(1)

    token = exchange_code_for_token(auth_code)
    if token:
        print(f"\nAccess token:")
        print(f"UPSTOX_ACCESS_TOKEN={token}")
        print(f"\nAdd this to your .env and update the GitHub Secret UPSTOX_ACCESS_TOKEN.")
    else:
        print("Failed to get token.")
        sys.exit(1)


if __name__ == "__main__":
    main()
