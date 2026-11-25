"""
LinkedIn Easy Apply. Requires session_linkedin.json from login_capture.py.

LinkedIn's automation detection is meaningfully tighter than Naukri's — keep
pace_seconds_between_actions generous, and expect this to need more manual
babysitting than the Naukri script. Stop instantly on any CAPTCHA, login
prompt, or "you've reached the Easy Apply limit" message.

Usage:
    python linkedin_apply.py
"""
import csv
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common import gemini
from common import learned_answers
from common.human_input import ask_user
from common.auth import (
    get_credentials,
    perform_login,
    launch_stealth_browser,
    new_stealth_context,
    get_linkedin_cookie,
    create_linkedin_session_from_cookie,
    has_valid_linkedin_session,
)
from common.job_exporter import log_external_job

SESSION_FILE = "session_linkedin.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "easy apply limit", "unusual activity", "verify it's you",
    "captcha", "security verification", "we've restricted",
]


def passes_filters(card: dict, profile: Profile) -> tuple[bool, str]:
    title = (card.get("title") or "").lower()
    company = (card.get("company") or "").lower()

    for excl_role in profile.data.get("exclude_roles", []):
        if excl_role.lower() in title:
            return False, f"hard excluded role ({excl_role})"

    for excl_kw in profile.data.get("exclude_keywords", []):
        clean_kw = excl_kw.strip().lower()
        if clean_kw and clean_kw in title:
            return False, f"hard excluded keyword ({excl_kw})"

    for excl_jt in profile.data.get("job_type", {}).get("exclude", []):
        if excl_jt.lower() in title:
            return False, f"excluded job type ({excl_jt})"

    for excl in profile.data.get("title_exclude", []):
        if excl.lower() in title:
            return False, f"title matched excluded pattern ({excl})"

    exp_filter = profile.data.get("experience_filter", {})
    if exp_filter.get("reject_senior_roles", True):
        senior_markers = ["senior", "sr.", "principal", "lead", "architect", "manager", "director", "head"]
        if any(marker in title for marker in senior_markers):
            return False, "senior position rejected (Fresher profile)"

    return True, ""


def log_row(row: list):
    new_file = not Path(LOG_FILE).exists()
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "source", "title", "company", "status", "reason"])
        w.writerow(row)


def page_has_stop_signal(page) -> str | None:
    text = page.inner_text("body").lower()
    for phrase in STOP_PHRASES:
        if phrase in text:
            return phrase
    if "/checkpoint/" in page.url or "/login" in page.url:
        return "session expired / checkpoint challenge"
    return None


def describe_modal(page) -> dict:
    return page.evaluate("""
        () => {
            const modal = document.querySelector('.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return {error: 'no dialog found'};
            const out = {heading: modal.querySelector('h2')?.innerText, fields: [], buttons: []};
            modal.querySelectorAll('input, textarea, select').forEach(el => {
                if (el.type === 'hidden') return;
                let label = '';
                if (el.id) {
                    const lbl = modal.querySelector(`label[for="${el.id}"]`);
                    if (lbl) label = lbl.innerText.trim();
                }
                out.fields.push({tag: el.tagName, type: el.type || '', id: el.id,
                                  label, value: el.value, checked: el.checked});
            });
            modal.querySelectorAll('button').forEach(b => {
                const t = b.innerText.trim();
                if (t) out.buttons.push(t);
            });
            return out;
        }
    """)


def fill_text_field(page, field_id: str, text: str):
    page.evaluate(
        """([id, text]) => {
            const el = document.getElementById(id);
            if (!el) return false;
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, text);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        [field_id, text],
    )


def fill_form_step(page, profile: Profile, job_context: str):
    answers = profile.answer_library()
    state = describe_modal(page)
    if state.get("error"):
        time.sleep(1.5)
        state = describe_modal(page)

    for field in state.get("fields", []):
        label = (field.get("label") or "").lower()
        if not label or field.get("value"):
            continue  # already filled or unlabeled, leave it

        # 1. Check learned answers from prior applications first
        stored = learned_answers.get_answer(label)
        if stored:
            fill_text_field(page, field["id"], stored)
            print(f"  (⚡ used remembered answer for: {label[:60]})")
            continue

        matched = None
        for key, val in answers.items():
            if key.replace("_", " ") in label:
                matched = val
                break

        if matched is not None:
            if field["tag"] == "SELECT":
                pass  # dropdowns handled separately below if needed
            else:
                fill_text_field(page, field["id"], str(matched))
        elif field["tag"] in ("INPUT",) and field["type"] == "text":
            draft = gemini.draft_answer(label, profile.data, job_context)
            if draft.startswith("[NEEDS_HUMAN_INPUT"):
                response = ask_user(f"LinkedIn screening question:\n{label}", timeout_seconds=90)
                if response:
                    learned_answers.save_answer(label, response)
                    fill_text_field(page, field["id"], response)
                else:
                    raise RuntimeError(f"profile gap on '{label}': {draft}")
            else:
                fill_text_field(page, field["id"], draft)


def click_modal_button(page, text: str) -> bool:
    return page.evaluate(
        """(text) => {
            const modal = document.querySelector('.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return false;
            const btn = [...modal.querySelectorAll('button')].find(b => b.innerText.trim() === text);
            if (!btn) return false;
            btn.click();
            return true;
        }""",
        text,
    )


def run_one_application(page, profile: Profile, job_title: str, company: str) -> bool:
    job_context = f"{job_title} at {company}"

    if not click_modal_button(page, "Easy Apply"):
        return False
    time.sleep(1.5)

    for _ in range(10):  # hard cap on steps per application
        stop = page_has_stop_signal(page)
        if stop:
            raise RuntimeError(f"stop signal mid-application: {stop}")

        state = describe_modal(page)
        buttons = state.get("buttons", [])

        fill_form_step(page, profile, job_context)
        time.sleep(0.8)

        if "Submit application" in buttons:
            # Read the full review text before submitting — this is the safety net.
            review_text = page.inner_text(
                '.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal'
            )
            print("--- Review before submit ---")
            print(review_text[:800])
            click_modal_button(page, "Submit application")
            time.sleep(2)
            done_clicked = click_modal_button(page, "Done")
            return True
        elif "Review" in buttons:
            click_modal_button(page, "Review")
        elif "Next" in buttons:
            click_modal_button(page, "Next")
        else:
            raise RuntimeError(f"unrecognized modal state, buttons={buttons}")
        time.sleep(1.2)

    raise RuntimeError("exceeded step cap without reaching submit")


def run():
    profile = Profile.load()
    if not has_valid_linkedin_session(SESSION_FILE):
        cookie = get_linkedin_cookie()
        if cookie:
            print("Creating LinkedIn session from LINKEDIN_LI_AT in .env...")
            create_linkedin_session_from_cookie(cookie, SESSION_FILE)
        else:
            raise SystemExit(
                f"Valid LinkedIn session not found in {SESSION_FILE}!\n"
                f"Please run: python login_capture.py linkedin\n"
                f"(Option 1: paste your 'li_at' cookie directly to bypass Cloudflare immediately)"
            )

    applied = 0
    with sync_playwright() as p:
        browser = launch_stealth_browser(p, headless=False, slow_mo=200)
        context = new_stealth_context(browser, storage_state=SESSION_FILE)
        page = context.new_page()

        for role in profile.target_roles:
            if applied >= profile.stop_after_n_applications:
                break
            search_url = (
                "https://www.linkedin.com/jobs/search/?keywords="
                + role.replace(" ", "%20")
                + "&f_AL=true"  # Easy Apply filter
            )
            print(f"\n--- Searching LinkedIn: {role} ---")
            page.goto(search_url)
            time.sleep(3)

            stop = page_has_stop_signal(page)
            if stop:
                print(f"STOPPING: {stop}")
                log_row([datetime.now(), "linkedin", "-", "-", "stopped", stop])
                try:
                    context.storage_state(path=SESSION_FILE)
                except Exception:
                    pass
                browser.close()
                return

            cards = page.evaluate("""
                () => Array.from(document.querySelectorAll('.jobs-search-results-list li, .scaffold-layout__list li'))
                  .map(c => ({
                    title: c.querySelector('[class*="job-card-list__title"]')?.innerText?.trim(),
                    company: c.querySelector('[class*="job-card-container__company-name"]')?.innerText?.trim(),
                  })).filter(c => c.title)
            """)

            for idx, card in enumerate(cards):
                if applied >= profile.stop_after_n_applications:
                    break

                ok, reason = passes_filters(card, profile)
                if not ok:
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "skipped", reason])
                    print(f"Skipped: {card.get('title')} @ {card.get('company')} — {reason}")
                    continue

                try:
                    page.click(f"(.jobs-search-results-list li, .scaffold-layout__list li) >> nth={idx}")
                    time.sleep(1.5)
                    success = run_one_application(page, profile, card.get("title", ""), card.get("company", ""))
                    if success:
                        applied += 1
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "applied", ""])
                        print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total)")
                    else:
                        log_external_job(
                            title=card.get("title", ""),
                            company=card.get("company", ""),
                            platform="LinkedIn",
                            listing_url=page.url,
                        )
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "skipped", "no Easy Apply button (external apply)"])
                        print(f"Skipped & Saved to Excel: {card.get('title')} @ {card.get('company')} — external apply")
                except RuntimeError as e:
                    print(f"STOPPING: {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "stopped", str(e)])
                    try:
                        context.storage_state(path=SESSION_FILE)
                    except Exception:
                        pass
                    browser.close()
                    return

                time.sleep(profile.pace_seconds_between_actions)

        try:
            context.storage_state(path=SESSION_FILE)
            print(f"Updated session state saved to {SESSION_FILE}.")
        except Exception:
            pass
        browser.close()

    print(f"\nDone. {applied} applications submitted this run. See {LOG_FILE} for the full log.")


if __name__ == "__main__":
    run()
