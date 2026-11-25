"""
Run this once per site (naukri, linkedin) before running the apply scripts,
or whenever your session expires.

Usage:
    python login_capture.py naukri
    python login_capture.py linkedin
"""
import getpass
import sys
import time
from playwright.sync_api import sync_playwright

from common.auth import (
    SITES,
    get_credentials,
    save_credentials,
    fill_credentials_and_submit,
    launch_stealth_browser,
    new_stealth_context,
    create_linkedin_session_from_cookie,
    has_valid_linkedin_session,
)


def handle_linkedin_cookie_option(out_path: str) -> bool:
    print("\n" + "=" * 65)
    print("LINKEDIN AUTHENTICATION ASSISTANT")
    print("=" * 65)
    print("LinkedIn uses Cloudflare & PerimeterX bot protection, which")
    print("often blocks automated browsers from logging in directly.\n")
    print("Choose an authentication method:")
    print("  1. Paste 'li_at' session cookie from your browser (100% Reliable & Recommended)")
    print("  2. Open Stealth Chrome Browser window to log in")
    print("=" * 65)
    
    choice = input("Enter choice (1 or 2, default: 1): ").strip()
    if choice in ("2",):
        return False

    print("\n--- HOW TO GET YOUR 'li_at' COOKIE ---")
    print("1. Open LinkedIn in your normal browser (where you are already logged in).")
    print("2. Press F12 (or right-click -> Inspect) to open Developer Tools.")
    print("3. Click on the 'Application' tab (in Chrome/Edge) or 'Storage' tab (in Firefox).")
    print("4. On the left menu, expand 'Cookies' -> click 'https://www.linkedin.com'.")
    print("5. Find the row named 'li_at' and double-click its 'Value' column to copy it.")
    print("---------------------------------------\n")

    li_at = input("Paste your 'li_at' cookie value here: ").strip()
    if not li_at:
        print("Empty cookie provided. Falling back to browser window...\n")
        return False

    create_linkedin_session_from_cookie(li_at, out_path=out_path)
    print(f"\nSUCCESS: LinkedIn session saved to {out_path}!")
    print("You can now run: python linkedin_apply.py")
    return True


def main():
    if len(sys.argv) == 2 and sys.argv[1].lower() in SITES:
        site = sys.argv[1].lower()
    else:
        print("\n" + "=" * 50)
        print("  LOGIN CAPTURE — SELECT PLATFORM")
        print("=" * 50)
        print("  1. Naukri (opens Naukri login page)")
        print("  2. LinkedIn (opens LinkedIn login page or paste li_at)")
        print("=" * 50)
        choice = input("Enter choice (1 for Naukri, 2 for LinkedIn, default: 1): ").strip()
        site = "linkedin" if choice == "2" else "naukri"

    url = SITES[site]
    out_path = f"session_{site}.json"

    if site == "linkedin":
        handled = handle_linkedin_cookie_option(out_path)
        if handled:
            return

    username, password = get_credentials(site)
    if not username or not password:
        print(f"\nNo saved credentials found for {site} in .env.")
        ans = input(f"Would you like to save your {site} credentials in .env? (y/n): ").strip().lower()
        if ans in ("y", "yes"):
            username = input(f"Enter your {site.capitalize()} Email / Username: ").strip()
            password = getpass.getpass(f"Enter your {site.capitalize()} Password: ").strip()
            if username and password:
                save_credentials(site, username, password)

    print(f"\nOpening stealth browser for {site}...")
    with sync_playwright() as p:
        browser = launch_stealth_browser(p, headless=False)
        context = new_stealth_context(browser)
        page = context.new_page()
        page.goto(url)

        if username and password:
            print(f"Auto-filling saved credentials for {username}...")
            time.sleep(2.0)
            fill_credentials_and_submit(page, site, username, password)
            print("If a Cloudflare check, OTP, or CAPTCHA appears, solve it in the browser.")
        else:
            print("Please log into your account in the browser.")

        while True:
            print("\nOnce you are on your logged-in feed/homepage, press Enter here to save...")
            try:
                input()
            except EOFError:
                time.sleep(5)

            cookies = context.cookies()
            if site == "linkedin":
                has_li_at = any(c.get("name") == "li_at" and bool(c.get("value")) for c in cookies)
                if not has_li_at:
                    print("WARNING: 'li_at' cookie not found! You appear to not be fully logged in yet.")
                    retry = input("Try pressing Enter again after finishing login? (y/n): ").strip().lower()
                    if retry in ("y", "yes", ""):
                        continue
                    else:
                        print("Session not saved. Exiting.")
                        browser.close()
                        return
            break

        context.storage_state(path=out_path)
        print(f"\nSUCCESS: Session saved to {out_path}.")
        browser.close()


if __name__ == "__main__":
    main()
