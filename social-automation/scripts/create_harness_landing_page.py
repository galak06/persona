import sys
import os
import json
from pathlib import Path
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("landing_page")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "lib"))

import local_env

def main():
    # Load WP_URL, WP_USER, WP_APP_PASSWORD from .claude/settings.local.json
    local_env.load_local_env()
    
    wp_url = os.environ.get("WP_URL")
    wp_user = os.environ.get("WP_USER")
    wp_app_password = os.environ.get("WP_APP_PASSWORD")
    
    if not all([wp_url, wp_user, wp_app_password]):
        logger.error("Missing WP_URL, WP_USER, or WP_APP_PASSWORD in environment.")
        return

    wp_url = wp_url.rstrip("/")
    client = httpx.Client(
        base_url=wp_url,
        auth=(wp_user, wp_app_password),
        timeout=60.0,
        headers={"User-Agent": "landing-page-generator/0.1"}
    )
    
    asin = "B01M8JT6FT"
    # Note: Replace 'your_amazon_affiliate_id' with the actual tag in the future.
    affiliate_url = f"https://www.amazon.com/dp/{asin}?tag=dogfoodandfun-20"
    
    html_content = f"""
    <div style="font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
        <!-- Hero Section -->
        <div style="text-align: center; margin-bottom: 40px;">
            <h1 style="color: #333; font-size: 2.5em; margin-bottom: 10px;">Say Goodbye to Dog Pulling. Forever.</h1>
            <p style="font-size: 1.2em; color: #555; margin-bottom: 20px;">
                The <strong>Rabbitgoo No-Pull Harness</strong> gives you immediate control without choking or hurting your best friend.
            </p>
            <a href="{affiliate_url}" style="display: inline-block; background-color: #f0c14b; color: #111; padding: 15px 30px; text-decoration: none; font-size: 1.2em; font-weight: bold; border-radius: 5px; border: 1px solid #a88734;">
                Get Yours on Amazon
            </a>
            <p style="font-size: 0.9em; color: #888; margin-top: 10px;">Over 150,000+ positive reviews!</p>
        </div>

        <!-- Features Section -->
        <div style="display: flex; flex-wrap: wrap; gap: 20px; margin-bottom: 40px;">
            <div style="flex: 1; min-width: 200px; padding: 20px; background: #f9f9f9; border-radius: 8px;">
                <h3 style="color: #222;">🛑 Front-Clip No Pull</h3>
                <p style="color: #555;">Features a front metal D-ring that gently redirects your dog's forward motion when they pull, naturally teaching them to walk by your side.</p>
            </div>
            <div style="flex: 1; min-width: 200px; padding: 20px; background: #f9f9f9; border-radius: 8px;">
                <h3 style="color: #222;">🛡️ Safe & Comfortable</h3>
                <p style="color: #555;">Breathable air mesh keeps your dog cool, while soft padding protects their skin. The pressure is evenly distributed to prevent choking.</p>
            </div>
            <div style="flex: 1; min-width: 200px; padding: 20px; background: #f9f9f9; border-radius: 8px;">
                <h3 style="color: #222;">🌙 Nighttime Safety</h3>
                <p style="color: #555;">Super bright reflective strips ensure a safe walk both day and night. Plus, 4 fully adjustable straps provide a snug, escape-proof fit.</p>
            </div>
        </div>

        <!-- Social Proof / Why Choose This -->
        <div style="background: #eef2f5; padding: 30px; border-radius: 8px; margin-bottom: 40px;">
            <h2 style="text-align: center; color: #333;">Why We Picked Rabbitgoo for "Dog Food and Fun"</h2>
            <p style="color: #444; line-height: 1.6;">
                We tested multiple harnesses, but the Rabbitgoo offers the best balance of <strong>affordability, durability, and actual no-pull effectiveness.</strong> 
                Unlike traditional collars that can cause neck strain, this step-in vest design hugs the chest. 
                Whether you're hiking a trail or taking a quick neighborhood walk, you'll immediately notice the difference.
            </p>
        </div>

        <!-- Final CTA -->
        <div style="text-align: center;">
            <h2 style="color: #333;">Ready for Peaceful Walks?</h2>
            <p style="color: #555; margin-bottom: 20px;">Stop the struggle today and enjoy walking your dog again.</p>
            <a href="{affiliate_url}" style="display: inline-block; background-color: #f0c14b; color: #111; padding: 15px 30px; text-decoration: none; font-size: 1.2em; font-weight: bold; border-radius: 5px; border: 1px solid #a88734;">
                Buy on Amazon
            </a>
        </div>
        
        <p style="font-size: 0.8em; color: #999; text-align: center; margin-top: 40px;">
            *As an Amazon Associate, Dog Food and Fun earns from qualifying purchases.
        </p>
    </div>
    """

    payload = {
        "title": "Rabbitgoo No-Pull Dog Harness - Stop Pulling Instantly",
        "content": html_content,
        "status": "draft",
        "slug": "rabbitgoo-no-pull-harness"
    }

    logger.info("Publishing page draft to WordPress...")
    response = client.post("/wp-json/wp/v2/pages", json=payload)
    
    if response.status_code in [200, 201]:
        data = response.json()
        logger.info(f"Success! Page created.")
        logger.info(f"Page ID: {data.get('id')}")
        logger.info(f"Edit Link: {wp_url}/wp-admin/post.php?post={data.get('id')}&action=edit")
    else:
        logger.error(f"Failed to create page: {response.status_code} {response.text}")

if __name__ == "__main__":
    main()
