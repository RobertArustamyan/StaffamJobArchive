# Job Scraper Project

## Goal
Build a job scraper for https://staff.am/en/jobs with a local Flask UI.

## Tech stack
- Python
- Playwright (site is JS-rendered, blocks simple HTTP requests)
- Flask (local UI)
- JSON files for storage (no database)

## Project structure
scraper/
├── scraper.py
├── app.py
├── config.json
├── requirements.txt
├── setup.sh
└── data/
    ├── jobs.json
    └── runs.log

## config.json format
{
  "url": "https://staff.am/en/jobs",
  "fields": [],
  "keywords": ""
}

## scraper.py requirements
1. Load config.json for URL, fields and keyword filters
2. Launch Playwright headless browser with a realistic user-agent
3. Navigate to URL, wait for JS to render fully
4. Scroll to bottom repeatedly and click any "load more" button until
   no new cards appear
5. Extract only the fields listed in config.json from each job card
6. URL is always extracted regardless of config — needed for deduplication
7. If data/jobs.json does not exist → save ALL jobs (first run)
8. If data/jobs.json exists → compare by job URL, append only NEW jobs
9. If keywords set in config → skip jobs that don't contain those words
10. Add scraped_at timestamp to every job
11. Log each run to data/runs.log: timestamp + how many new jobs found
12. Never crash silently — catch all exceptions and log them

## Extractable fields (checkboxes in UI)
- title
- company
- location
- salary
- job_type
- posted_date
- deadline
- url
- description (warn user this is slow — requires visiting each job page)

## app.py requirements
Four routes:
- GET  /        → table of all scraped jobs, columns = active fields
                  from config.json, newest first, total count shown
- GET  /config  → form with:
                  * URL input (pre-filled)
                  * checkboxes for each field
                  * text input for keyword filter (comma separated)
- POST /config  → save updated config.json
- POST /scrape  → trigger scraper.py as subprocess, redirect to / with
                  a status message

One more route:
- GET /log      → show last 30 lines of data/runs.log in a <pre> block

## UI style
- No frontend framework
- Plain HTML + minimal inline CSS
- Clean, readable, dark header on table
- Must show a message if no jobs yet with link to /config

## setup.sh
pip install -r requirements.txt
playwright install chromium
mkdir -p data

## Rules
- Add small random delays (1-3 sec) between Playwright actions
- Use realistic Chrome user-agent string
- Deduplicate strictly by job URL
- Store null (not empty string) when a field is not found
- requirements.txt must list: playwright, flask
- When app.py starts, create data/ and config.json if they don't exist
- Print "Open http://localhost:5000/config to configure" on startup

## Important
After building, do NOT run the scraper yet.
First open staff.am/en/jobs with Playwright MCP, inspect one job card HTML,
find the correct CSS selectors for each field, then use those selectors
in scraper.py. Print the raw HTML of one card so I can verify.