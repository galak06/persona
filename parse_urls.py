import json, re
logs = ["social-automation/logs/engagement_log.jsonl"]
urls = set()
for log in logs:
    with open(log, "r") as f:
        for line in f:
            try:
                data = json.loads(line)
                content = data.get("content", "")
                if "dogfoodandfun.com" in content:
                    found = re.findall(r'https?://dogfoodandfun\.com[^\s\\]+', content)
                    urls.update(found)
            except:
                pass
for u in urls:
    print(u)
