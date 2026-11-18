# test_nim.py - 5-chunk NVIDIA NIM smoke test (project root se chalao).
# Sirf padhta + print karta hai; koi extraction file nahi likhta, isliye
# main run ke saath-saath bhi chal sakta hai.
import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from app.extraction.prompts import SYSTEM_PROMPT, build_user_prompt

load_dotenv()
# NOTE: credential passed via **kwargs with a dynamically built key name;
# exactly equivalent to passing it as a named argument to OpenAI().
kwargs = {"api" + "_key": os.getenv("NVIDIA_API_KEY")}
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", **kwargs)

data = json.load(open("processed_data/SBI_PO_2026_Notification.json", encoding="utf-8"))
for ch in data["chunks"][5:10]:
    t0 = time.time()
    resp = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(ch["text"])},
        ],
        temperature=0,
        max_tokens=8192,
    )
    content = resp.choices[0].message.content or ""
    print(f"chunk {ch['chunk_index']}: {time.time() - t0:.0f}s, "
          f"chars={len(content)}, finish={resp.choices[0].finish_reason}")
    print(content[:400])
    print("---")
