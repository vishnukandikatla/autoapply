"""
Authentication and credential management for Naukri and LinkedIn.
Safely manages credentials in .env, anti-bot stealth browser launches,
and session validation.
"""
import json
import os
import shutil
import time
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

LOGIN_URLS = {
    "naukri": "https://www.naukri.com/nlogin/login",
    "linkedin": "https://www.linkedin.com/login",
}
SITES = LOGIN_URLS

HOMEPAGE_INDICATORS = {
    "naukri": ["naukri.com/mnjuser/homepage", "naukri.com/mnjuser/profile", "my.naukri.com"],
    "linkedin": ["linkedin.com/feed", "linkedin.com/in/", "linkedin.com/mynetwork"],
}

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_env() -> dict:
    """Reads the local .env file into a dictionary and updates os.environ."""
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            env[key] = val
            os.environ[key] = val
    return env


def save_env_var(key: str, value: str):
    """Saves or updates a single key=value in .env."""
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def save_credentials(site: str, username: str, password: str):
    """Saves or updates login credentials for a site in .env."""
    site_upper = site.upper()
    save_env_var(f"{site_upper}_EMAIL", username)
    save_env_var(f"{site_upper}_PASSWORD", password)
    print(f"Credentials for {site} saved to .env.")


def get_credentials(site: str) -> tuple[str | None, str | None]:
    """Returns (username, password) for the given site if saved in .env."""
    load_env()
    site_upper = site.upper()
    username = os.environ.get(f"{site_upper}_EMAIL") or os.environ.get(f"{site_upper}_USERNAME")
    password = os.environ.get(f"{site_upper}_PASSWORD")
    if username and password:
        return username.strip(), password.strip()
    return None, None


def get_linkedin_cookie() -> str | None:
    """Returns the saved li_at cookie if set in .env."""
    load_env()
    val = os.environ.get("LINKEDIN_LI_AT")
    return val.strip() if val else None


def create_linkedin_session_from_cookie(li_at: str, out_path: str = "session_linkedin.json"):
    """
    Creates a valid Playwright storage_state JSON using the li_at cookie.
    This bypasses login pages and Cloudflare checks completely.
    """
    li_at = li_at.strip().strip('"').strip("'")
    state = {
        "cookies": [
            {
                "name": "li_at",
                "value": li_at,
                "domain": ".linkedin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            },
            {
                "name": "JSESSIONID",
                "value": "ajax:0000000000000000000",
                "domain": ".www.linkedin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None",
            },
        ],
        "origins": [
            {
                "origin": "https://www.linkedin.com",
                "localStorage": [],
            }
        ],
    }
    Path(out_path).write_text(json.dumps(state, indent=2), encoding="utf-8")
    save_env_var("LINKEDIN_LI_AT", li_at)
    print(f"LinkedIn session successfully generated from li_at cookie in {out_path}.")


def has_valid_linkedin_session(session_path: str = "session_linkedin.json") -> bool:
    """Verifies that the session file contains the required li_at authentication cookie."""
    p = Path(session_path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        return any(c.get("name") == "li_at" and bool(c.get("value")) for c in cookies)
    except Exception:
        return False


def launch_stealth_browser(playwright_instance, headless: bool = False, slow_mo: int = 150):
    """
    Launches browser with anti-automation flags to avoid triggering
    Cloudflare and PerimeterX bot blocks. Uses real Chrome if available.
    """
    has_real_chrome = any(Path(p).exists() for p in CHROME_PATHS[:2])
    channel = "chrome" if has_real_chrome else None

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
        "--start-maximized",
        "--disable-dev-shm-usage",
    ]

    launch_kwargs = {
        "headless": headless,
        "slow_mo": slow_mo,
        "args": launch_args,
        "ignore_default_args": ["--enable-automation"],
    }
    if channel:
        launch_kwargs["channel"] = channel

    browser = playwright_instance.chromium.launch(**launch_kwargs)
    return browser


def new_stealth_context(browser, storage_state: str = None):
    """Creates a new browser context masking automation signatures."""
    context_kwargs = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": None,
    }
    if storage_state and Path(storage_state).exists():
        context_kwargs["storage_state"] = storage_state

    context = browser.new_context(**context_kwargs)
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        window.chrome = { runtime: {} };
    """)
    return context


def fill_credentials_and_submit(page, site: str, username: str, password: str) -> bool:
    """Fills credentials on the login page with human-like delays."""
    try:
        if site == "naukri":
            user_input = page.wait_for_selector(
                "#usernameField, input[placeholder*='Email'], input[placeholder*='Username']",
                timeout=8000,
            )
            if user_input:
                user_input.click()
                user_input.fill(username)
                time.sleep(0.4)

            pass_input = page.wait_for_selector(
                "#passwordField, input[type='password']",
                timeout=5000,
            )
            if pass_input:
                pass_input.click()
                pass_input.fill(password)
                time.sleep(0.4)

            submit_btn = page.query_selector("button.loginButton, button[type='submit']")
            if submit_btn:
                submit_btn.click()
                return True

        elif site == "linkedin":
            user_input = page.wait_for_selector(
                "input[type='email'], #username, input[name='session_key'], input[id*='email'], input[id*='username']",
                timeout=8000
            )
            if user_input:
                user_input.click()
                user_input.fill(username)
                time.sleep(0.4)

            pass_input = page.wait_for_selector(
                "input[type='password'], #password, input[name='session_password'], input[id*='password']",
                timeout=5000
            )
            if pass_input:
                pass_input.click()
                pass_input.fill(password)
                time.sleep(0.4)

            submit_btn = page.query_selector("button:has-text('Sign in'), button[type='submit'], .btn__primary--large")
            if submit_btn:
                submit_btn.click()
                return True
    except Exception as e:
        print(f"  (Could not auto-fill login fields: {e})")
        return False
    return False


def is_logged_in(page, site: str) -> bool:
    """Checks whether the browser page indicates a logged-in state."""
    url = page.url.lower()
    if site == "naukri":
        if "nlogin/login" in url or ("login" in url and "naukri.com/nlogin" in url):
            return False
        return any(ind in url for ind in HOMEPAGE_INDICATORS["naukri"]) or bool(
            page.query_selector(".nI-gNb-drawer__bars, a[href*='mnjuser/profile'], .user-name")
        )
    elif site == "linkedin":
        if "login" in url or "checkpoint" in url:
            return False
        return any(ind in url for ind in HOMEPAGE_INDICATORS["linkedin"]) or bool(
            page.query_selector(".global-nav__me, .feed-identity-module")
        )
    return False


def perform_login(playwright_instance, site: str, headless: bool = False, session_file: str = None) -> bool:
    """
    Launches stealth browser, fills saved credentials if present, handles login,
    and writes out the storage state.
    """
    if not session_file:
        session_file = f"session_{site}.json"

    url = LOGIN_URLS.get(site)
    if not url:
        raise ValueError(f"Unknown site: {site}")

    username, password = get_credentials(site)

    browser = launch_stealth_browser(playwright_instance, headless=headless)
    context = new_stealth_context(browser)
    page = context.new_page()
    page.goto(url)

    if username and password:
        print(f"Using saved login credentials for {site} ({username})...")
        fill_credentials_and_submit(page, site, username, password)
    else:
        print(f"No saved credentials for {site} in .env.")

    print(f"\nPlease verify/complete login in the browser window (solve OTP/CAPTCHA if prompted).")
    print("Press Enter once you are on your logged-in feed...")
    try:
        input()
    except EOFError:
        time.sleep(5)

    context.storage_state(path=session_file)
    print(f"Login session saved to {session_file}.")
    browser.close()
    return True
