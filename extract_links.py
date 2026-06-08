import json
import re
import sys

logs = ["social-automation/logs/engagement_log.jsonl"]
urls = set()

for log in logs:
    try:
        with open(log, "r") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    content = data.get("content", "")
                    target_url = data.get("target_url", "")
                    
                    # Find all dogfoodandfun.com urls in content
                    found = re.findall(r'https?://dogfoodandfun\.com[^\s\\]*', content)
                    urls.update(found)
                    
                    # Also check if target_url has it (unlikely)
                    if "dogfoodandfun.com" in target_url:
                        urls.add(target_url)
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass

for u in sorted(urls):
    print(u)
