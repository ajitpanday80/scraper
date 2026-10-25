"""Draft message variants through OpenRouter (OpenAI-compatible chat completions)."""
import json
import os
import re

import requests

URL = "https://openrouter.ai/api/v1/chat/completions"
OPT_OUT = {"English": "Reply STOP to opt out.", "Hindi": "बंद करने के लिए STOP लिखें। (Reply STOP to opt out.)",
           "Hinglish": "Band karne ke liye STOP likhein. (Reply STOP to opt out.)"}


class AIError(Exception):
    pass


def _parse(text, n):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            raise AIError("The model did not return a list of messages. Try again or pick a different model.")
        try:
            data = json.loads(m.group(0))
        except ValueError:
            raise AIError("The model returned malformed output. Try again or pick a different model.")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out = [str(x).strip() for x in data if isinstance(x, (str, int, float)) and str(x).strip()]
    if not out:
        raise AIError("The model returned no usable messages.")
    return out[:n]


def ensure_opt_out(body, language="English"):
    """Every variant must tell the recipient how to stop messages."""
    return body if re.search(r"\bstop\b", body, re.I) else f"{body}\n{OPT_OUT.get(language, OPT_OUT['English'])}"


def generate_variants(idea, link, language, n, model=None):
    key = os.environ.get("OPENROUTER_API_KEY")
    model = model or os.environ.get("OPENROUTER_MODEL")
    if not key:
        raise AIError("OPENROUTER_API_KEY is not set on the server.")
    if not model:
        raise AIError("No AI model chosen. Set OPENROUTER_MODEL on the server or type a model name in the campaign settings.")
    if not idea.strip():
        raise AIError("Write the basic idea of your message first (campaign settings).")
    system = (
        "You write short outbound messages from one local business owner to another local business. "
        f"Write {n} distinct variants of the same offer in {language}. Rules: be honest and specific to the brief; "
        "never invent facts, prices, discounts, deadlines or claims that are not in the brief; no fake urgency; "
        "do not pretend to know the recipient or to be a customer. Use the placeholder {name} once, near the start, "
        "for the recipient's business name. "
        + ("Use the placeholder {link} exactly once for the link. " if link else "Do not include any link. ")
        + "End every variant with a line telling the recipient how to opt out (for example 'Reply STOP to opt out.'). "
        "Keep each under 450 characters, with different openings and structure. "
        f"Output ONLY a JSON array of {n} strings, nothing else."
    )
    try:
        r = requests.post(URL, timeout=90, headers={"Authorization": f"Bearer {key}", "X-Title": "Lead Finder"},
                          json={"model": model, "temperature": 0.9,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": f"Brief:\n{idea.strip()}"}]})
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise AIError(f"Could not reach OpenRouter: {e}")
    if r.status_code != 200:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise AIError(f"OpenRouter error: {err.get('message') or r.text[:200]}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("OpenRouter returned an unexpected response.")
    return [ensure_opt_out(v, language) for v in _parse(content or "", n)]
