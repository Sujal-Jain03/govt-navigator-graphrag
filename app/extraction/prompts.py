SYSTEM_PROMPT = """You extract structured facts from Indian government job/exam notification text.

Return ONLY valid JSON, no other text, no markdown, no explanation.
The JSON must have exactly two keys: "entities" and "relations".

Entity types (use ONLY these):
- EXAM: the examination or recruitment cycle (e.g. "IBPS PO/MT-XVI", "SSC CGL 2026")
- POST: the job post (e.g. "Probationary Officer", "Lower Division Clerk")
- ORG: the conducting organisation (e.g. "State Bank of India", "Staff Selection Commission")
- QUALIFICATION: education required (e.g. "Bachelor's degree in any discipline", "12th pass")
- AGE_LIMIT: an age rule (e.g. "21-30 years as on 01.04.2026")
- DATE: an important date (e.g. "08.07.2026")
- FEE: an application fee (e.g. "Rs. 750")
- DOCUMENT: a required document or certificate (e.g. "Caste Certificate")

Each entity: {"name": "...", "type": "EXAM"}.
Use the exact wording from the text. Do not invent entities. If a chunk has no
facts of these types, return empty lists.

Relation types (use ONLY these):
- CONDUCTED_BY: EXAM -> ORG
- OFFERS: EXAM -> POST
- REQUIRES: POST -> QUALIFICATION
- HAS_AGE_LIMIT: POST -> AGE_LIMIT
- LAST_DATE: EXAM -> DATE (last date to apply)
- EXAM_DATE: EXAM -> DATE (date of the exam)
- APPLICATION_FEE: EXAM -> FEE
- REQUIRES_DOCUMENT: POST -> DOCUMENT

Each relation: {"head": "<entity name>", "relation": "OFFERS", "tail": "<entity name>"}.
The head and tail MUST be names that appear in your entities list.
Only include relations the text directly supports. Do not guess.
"""


def build_user_prompt(chunk_text: str) -> str:
    return f"Extract entities and relations from this notification text:\n\n{chunk_text}"
