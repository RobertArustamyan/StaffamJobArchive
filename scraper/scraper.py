"""
staff.am job scraper — parallel + streaming
--------------------------------------------
• 3 worker processes, each with its own Playwright browser (configurable via WORKERS)
• Detail pages fetched concurrently; results streamed to jobs.json every SAVE_EVERY jobs
• Timeouts on individual pages are caught and skipped — scraper never stops entirely
• Pagination via li.next a[aria-disabled="false"]

Playwright + multiprocessing note:
  'spawn' start method is used (not 'fork') because Playwright runs background
  threads that don't survive a fork safely.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import time
import traceback
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_DIR    = os.path.join(BASE_DIR, "data")
JOBS_PATH   = os.path.join(DATA_DIR, "jobs.json")
LOG_PATH    = os.path.join(DATA_DIR, "runs.log")
PID_PATH    = os.path.join(DATA_DIR, ".scraper.pid")

WORKERS    = 3   # parallel browser workers for detail pages
SAVE_EVERY = 5   # stream to disk after every N new jobs

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# JS: extract cards from one listing page
# ---------------------------------------------------------------------------
LISTING_JS = """
() => {
    const BASE = 'https://staff.am';

    function cardOf(a) {
        let el = a;
        for (let i = 0; i < 12; i++) {
            if (!el.parentElement) break;
            el = el.parentElement;
            if (el.getAttribute('tabindex') === '0' &&
                el.querySelector('img[alt="calendar-icon"]')) return el;
        }
        return null;
    }

    function textAfterImg(root, altText) {
        const img = root.querySelector('img[alt="' + altText + '"]');
        if (!img) return null;
        const parent = img.parentElement;
        if (!parent) return null;
        const children = [...parent.children];
        const idx = children.indexOf(img);
        const next = children[idx + 1];
        return next ? (next.innerText || '').trim() || null : null;
    }

    const seen = new Set();
    const jobs = [];
    for (const a of document.querySelectorAll('a[href^="/en/jobs/"]')) {
        const href = a.getAttribute('href');
        const fullUrl = BASE + href;
        if (seen.has(fullUrl)) continue;
        const card = cardOf(a);
        if (!card) continue;
        seen.add(fullUrl);
        const titleEl = a.querySelector('div[dir="auto"]');
        let company = null;
        for (const cl of card.querySelectorAll('a[href^="/company/"]')) {
            if (cl.querySelector('img[alt="left-icon"]')) continue;
            const d = cl.querySelector('div[dir="auto"]');
            if (d) { company = d.innerText.trim() || null; break; }
        }
        jobs.push({
            url:      fullUrl,
            title:    titleEl ? titleEl.innerText.trim() || null : null,
            company:  company,
            location: textAfterImg(card, 'JobAddress-icon'),
            deadline: textAfterImg(card, 'calendar-icon'),
        });
    }
    return jobs;
}
"""

# ---------------------------------------------------------------------------
# JS: extract detail fields from an individual job page
# ---------------------------------------------------------------------------
DETAIL_JS = """
() => {
    // img[alt="clockGreen"] sits inside a small icon-wrapper div.
    // The text div is a sibling of that WRAPPER (not of the img).
    // Path: img → wrapper div → row div → find div[dir="auto"] child
    function textAfterWrappedImg(altText) {
        const img = document.querySelector('img[alt="' + altText + '"]');
        if (!img) return null;
        const wrapper = img.parentElement;
        if (!wrapper) return null;
        const row = wrapper.parentElement;
        if (!row) return null;
        for (const child of row.children) {
            if (child !== wrapper && child.getAttribute('dir') === 'auto')
                return child.innerText.trim() || null;
        }
        const d = row.querySelector('div[dir="auto"]');
        return d ? d.innerText.trim() || null : null;
    }

    function labelValue(labelText) {
        for (const d of document.querySelectorAll('div[dir="auto"]')) {
            if (d.innerText.trim() === labelText) {
                let sib = d.nextElementSibling;
                if (sib) return (sib.innerText || '').trim() || null;
                const parent = d.parentElement;
                if (parent) {
                    const ch = [...parent.children];
                    const next = ch[ch.indexOf(d) + 1];
                    if (next) return (next.innerText || '').trim() || null;
                }
            }
        }
        return null;
    }

    // Description: content div right after the calendarGreen deadline row.
    // Strip social-share section ("Share this job...") from the end.
    let description = null;
    const calImg = document.querySelector('img[alt="calendarGreen"]');
    if (calImg) {
        let calRow = calImg;
        for (let i = 0; i < 5; i++) {
            if (!calRow.parentElement) break;
            calRow = calRow.parentElement;
            const s = calRow.getAttribute('style') || '';
            if (s.includes('flex-direction:row') || s.includes('flex-direction: row')) break;
        }
        const parent = calRow.parentElement;
        if (parent) {
            const siblings = [...parent.children];
            const contentDiv = siblings[siblings.indexOf(calRow) + 1];
            if (contentDiv) {
                let text = contentDiv.innerText.trim();
                const shareIdx = text.indexOf('Share this job');
                if (shareIdx > 0) text = text.slice(0, shareIdx).trim();
                description = text || null;
            }
        }
    }

    let category = null;
    for (const cl of document.querySelectorAll('a[href^="/en/jobs/"][target="_blank"]')) {
        const d = cl.querySelector('div[dir="auto"]');
        if (d) { category = d.innerText.trim() || null; break; }
    }

    return {
        jobType:        textAfterWrappedImg('clockGreen'),
        employmentTerm: labelValue('Employment term:'),
        category:       category,
        description:    description,
    };
}
"""


# ---------------------------------------------------------------------------
# Worker process state (one browser per worker, reused across jobs)
# ---------------------------------------------------------------------------
_w_pw      = None
_w_browser = None
_w_page    = None


def _worker_init(user_agent: str):
    """Called once per worker process to spin up a browser."""
    global _w_pw, _w_browser, _w_page
    import atexit
    _w_pw      = sync_playwright().start()
    _w_browser = _w_pw.chromium.launch(headless=True)
    ctx        = _w_browser.new_context(user_agent=user_agent)
    _w_page    = ctx.new_page()

    def _cleanup():
        try: _w_browser.close()
        except Exception: pass
        try: _w_pw.stop()
        except Exception: pass
    atexit.register(_cleanup)


def _worker_fetch(args: tuple) -> tuple:
    """
    Fetch one job detail page.
    Returns (card_dict, detail_dict).
    Never raises — timeouts and errors result in all-None detail fields.
    """
    global _w_page
    card, active_fields_list = args
    active_fields = set(active_fields_list)
    url = card["url"]

    detail = {"job_type": None, "employment_term": None,
              "category": None, "description": None}

    if not (active_fields & {"job_type", "employment_term",
                              "category", "description"}):
        return card, detail

    try:
        _w_page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(random.uniform(0.8, 1.5))
        result = _w_page.evaluate(DETAIL_JS)
        detail["job_type"]        = result.get("jobType")
        detail["employment_term"] = result.get("employmentTerm")
        detail["category"]        = result.get("category")
        detail["description"]     = result.get("description")
    except Exception:
        # Timeout or crash — reset the page so next job can proceed
        try: _w_page.goto("about:blank", timeout=5000)
        except Exception: pass

    return card, detail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def log(msg: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()} | {msg}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def load_existing_jobs():
    if not os.path.exists(JOBS_PATH):
        return None
    with open(JOBS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_jobs(jobs: list):
    """Atomic write so Flask never reads a half-written file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = JOBS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JOBS_PATH)


def collect_all_listing_pages(page, url: str) -> list:
    """Navigate through all pagination pages and return every card dict."""
    all_cards = []
    seen_urls: set = set()

    log("Navigating to listing page…")
    page.goto(url, wait_until="networkidle", timeout=30000)
    time.sleep(random.uniform(1.0, 2.0))

    page_num = 1
    while True:
        log(f"  listing page {page_num}: extracting cards…")
        cards = page.evaluate(LISTING_JS)
        new_cards = [c for c in cards if c["url"] not in seen_urls]
        for c in new_cards:
            seen_urls.add(c["url"])
        all_cards.extend(new_cards)
        log(f"  page {page_num}: {len(new_cards)} new cards "
            f"(running total {len(all_cards)})")
        try:
            next_btn = page.locator("li.next a[aria-disabled='false']").first
            if next_btn.is_visible(timeout=2000):
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(random.uniform(1.0, 2.0))
                page_num += 1
            else:
                break
        except Exception:
            break

    return all_cards


def keyword_match(job: dict, keywords: list) -> bool:
    if not keywords:
        return True
    text = " ".join(str(v) for v in job.values() if v).lower()
    return any(kw.lower() in text for kw in keywords)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Write PID so Flask can detect we're running
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))

    try:
        config        = load_config()
        url           = config.get("url", "https://staff.am/en/jobs")
        fields        = config.get("fields", [])
        kw_raw        = config.get("keywords", "")
        keywords      = [k.strip() for k in kw_raw.split(",") if k.strip()] \
                        if kw_raw else []
        active_fields = set(fields) | {"url"}
        need_detail   = bool(active_fields & {"job_type", "employment_term",
                                               "category", "description"})

        log(f"RUN START  url={url}  fields={fields}  keywords={keywords}  "
            f"workers={WORKERS if need_detail else 1}")

        existing      = load_existing_jobs()
        existing_urls = {j["url"] for j in (existing or []) if j.get("url")}

        # ── Step 1: collect all listing cards (single browser, sequential) ──
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx     = browser.new_context(user_agent=USER_AGENT)
            page    = ctx.new_page()
            all_cards = collect_all_listing_pages(page, url)
            browser.close()

        log(f"Total cards across all pages: {len(all_cards)}")

        to_process = [c for c in all_cards
                      if c.get("url")
                      and c["url"] not in existing_urls
                      and keyword_match(c, keywords)]
        log(f"New jobs to process: {len(to_process)}")

        # Working list starts with existing jobs (newest first after inserts)
        current_jobs: list = list(existing or [])
        new_count = 0

        # ── Step 2: fetch detail pages in parallel, stream saves ─────────────
        if need_detail and to_process:
            tasks = [(card, list(active_fields)) for card in to_process]

            with mp.Pool(
                processes=WORKERS,
                initializer=_worker_init,
                initargs=(USER_AGENT,),
            ) as pool:
                for i, (card, detail) in enumerate(
                        pool.imap_unordered(_worker_fetch, tasks), 1):
                    try:
                        job = {f: card.get(f)
                               for f in active_fields if f in card}
                        job["url"] = card["url"]
                        for f in ("job_type", "employment_term",
                                  "category", "description"):
                            if f in active_fields:
                                job[f] = detail[f]
                        for f in active_fields:
                            job.setdefault(f, None)
                        job["scraped_at"] = \
                            datetime.now(timezone.utc).isoformat()

                        current_jobs.insert(0, job)
                        new_count += 1
                        log(f"  [{i}/{len(to_process)}] saved: {card['url']}")

                        # Stream to disk
                        if new_count % SAVE_EVERY == 0:
                            save_jobs(current_jobs)
                            log(f"  (streamed {new_count} new jobs to disk)")

                    except Exception as exc:
                        log(f"ERROR assembling {card.get('url')}: {exc}\n"
                            f"{traceback.format_exc()}")

        elif to_process:
            # No detail fields needed — build directly from cards
            for card in to_process:
                job = {f: card.get(f) for f in active_fields if f in card}
                job["url"] = card["url"]
                for f in active_fields:
                    job.setdefault(f, None)
                job["scraped_at"] = datetime.now(timezone.utc).isoformat()
                current_jobs.insert(0, job)
                new_count += 1

        # Final save
        save_jobs(current_jobs)
        log(f"RUN END  new_jobs={new_count}  total_jobs={len(current_jobs)}")

    except Exception as exc:
        log(f"FATAL: {exc}\n{traceback.format_exc()}")
        raise

    finally:
        # Always clean up PID file
        try: os.remove(PID_PATH)
        except Exception: pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers", type=int, default=WORKERS,
        help="Number of parallel browser workers (default: %(default)s)"
    )
    args = parser.parse_args()
    WORKERS = args.workers
    mp.set_start_method("spawn", force=True)
    main()
