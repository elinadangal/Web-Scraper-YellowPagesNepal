import asyncio
import json
import re
import httpx
from json_repair import repair_json
from config.settings import LLM_URL, LLM_SEM

class LLMService:
    @staticmethod
    async def extract_business_details(client: httpx.AsyncClient, page_text: str) -> dict:
        prompt = f"""
        Extract business contact details from the provided text.

        Return a strict, valid JSON object with this EXACT structure:
        {{
            "name": "Business Name or null",
            "description": "Full description or null",
            "phone_number": "All phone/mobile/fax numbers separated by commas or null",
            "email": "Email address or null",
            "address": "Full physical address or null",
            "website": "Website URL or null"
        }}

        Rules:
        - Phone numbers and emails may appear as lines like "Hidden contact value: X" —
          these came from images on the page and are just as valid as normal text.
          Classify each one as phone_number or email based on its format.
        - If multiple phone numbers exist, combine them with commas like "4275166, 4280132".
        - Return ONLY raw JSON. No introductory text or markdown ticks.

        Content:
        {page_text}
        """

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        async with LLM_SEM:
            for attempt in (1, 2, 3):
                try:
                    response = await client.post(LLM_URL, json=payload, timeout=60.0)
                    response.raise_for_status()
                    content = response.json()["choices"][0]["message"]["content"].strip()

                    match = re.search(r"\{.*\}", content, re.DOTALL)
                    if match:
                        content = match.group(0)

                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        parsed = json.loads(repair_json(content))

                    if isinstance(parsed, list):
                        parsed = next((x for x in parsed if isinstance(x, dict)), {})
                    if not isinstance(parsed, dict):
                        parsed = {}
                    return parsed
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status == 429 and attempt < 3:
                        retry_after = e.response.headers.get("retry-after")
                        wait = float(retry_after) if retry_after else 5 * attempt
                        print(f"     LLM 429 (rate limited), waiting {wait}s, retrying ({attempt}/3)...")
                        await asyncio.sleep(wait)
                        continue
                    if status in (502, 503, 504) and attempt < 3:
                        print(f"     LLM {status}, retrying ({attempt}/3)...")
                        await asyncio.sleep(2 * attempt)
                        continue
                    print(f"     Extraction Error: {e}")
                    return {}
                except Exception as e:
                    if attempt < 3:
                        print(f"     LLM error, retrying ({attempt}/3): {e}")
                        await asyncio.sleep(2 * attempt)
                        continue
                    print(f"     Extraction Error: {e}")
                    return {}
            return {}