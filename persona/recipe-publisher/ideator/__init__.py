"""Research-driven recipe ideator for your-brand.com.

Independent of scripts/content_pipeline.py. Owns its own research, enrichment,
approval gates, and seed-queue mutation. Designed to run weekly: refills
recipe-publisher/seeds/seeds.json with fresh research-grounded candidates that
recipe-publisher then drains every 2 days.
"""
