import sys
from agent.llm import client, MODEL

models = sys.argv[1:] or [MODEL]
for m in models:
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=m,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        print(m, "-> OK")
        for k, v in raw.headers.items():
            if "ratelimit" in k.lower():
                print("   ", k, "=", v)
    except Exception as e:
        print(m, "-> ERROR:", str(e)[:300])