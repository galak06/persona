import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path("/Users/gilcohen/Projects/dogfoodandfun/social-automation")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.config import settings

SESSION_FILE = settings.paths.facebook_session
url = "https://www.facebook.com/groups/homemade.dog.food.recipes/posts/1053896582722055/"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(storage_state=str(SESSION_FILE))
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(5)
    page.evaluate("window.scrollTo(0, 1000)")
    time.sleep(2)
    
    res = page.evaluate('''() => {
        const articles = Array.from(document.querySelectorAll('[role="article"]'));
        return articles.map(el => (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 100));
    }''')
    for i, t in enumerate(res):
        print(f"[{i}] {t}")
    browser.close()
