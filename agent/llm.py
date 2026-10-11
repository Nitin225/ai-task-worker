import os
import time
from dotenv import load_dotenv
from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError

load_dotenv()

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3.8-flash"),
}
PROVIDER = os.getenv("LLM_PROVIDER", "groq")
BASE_URL, KEY_ENV, DEFAULT_MODEL = PROVIDERS[PROVIDER]
MODEL = os.getenv("LLM_MODEL", DEFAULT_MODEL)
client = OpenAI(base_url=BASE_URL, api_key=os.getenv(KEY_ENV), max_retries=0)
AUX_MODEL = os.getenv("LLM_MODEL_AUX", MODEL)   # used by verifier and pre-flight


def fn(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": v} for k, v in (props or {}).items()},
                "required": required or [],
            },
        },
    }


def generate(messages, tools=None, retries=5, model=None):
    kwargs = dict(model=model or MODEL, messages=messages, temperature=0)
    if tools:
        kwargs.update(tools=tools, tool_choice="auto")
    for attempt in range(retries):
        try:
            return client.chat.completions.create(**kwargs)
        except (APIConnectionError, APITimeoutError, APIStatusError) as e:
            code = getattr(e, "status_code", None)
            if code == 429 and "per day" in str(e).lower():
                raise RuntimeError(
                    f"Daily token quota exhausted for {kwargs['model']}. "
                    "Switch LLM_MODEL in .env or wait for the quota window to reset."
                ) from e
            malformed_tool_call = "tool_use_failed" in str(e)
            transient = code is None or code == 429 or code >= 500 or malformed_tool_call
            if not transient or attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"  [LLM call failed ({type(e).__name__} {code or ''}), retrying in {wait}s]")
            time.sleep(wait)