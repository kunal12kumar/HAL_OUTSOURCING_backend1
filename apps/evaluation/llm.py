"""Optional local LLM (Ollama). Used only to read free-form tender text when no structured requirements can be
parsed. Documents never leave this machine. Every answer is schema-validated by the caller; if the model is
unreachable the caller falls back to "requires manual verification"."""
import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
LLM_ENABLED = os.environ.get("EVAL_LLM_ENABLED", "1") == "1"


def generate_json(prompt, timeout=120):
    if not LLM_ENABLED:
        return None
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(json.loads(resp.read())["response"])
    except Exception as exc:
        logger.warning("local LLM unavailable or returned invalid JSON: %s", exc.__class__.__name__)
        return None
