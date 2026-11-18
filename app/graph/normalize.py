"""Name normalization + canonical node IDs for graph building.

Two entities are the SAME node iff they share a type AND their names
canonicalize to the same string. Canonicalization is type-aware:
- EXAM: each notification is ABOUT one known exam, so every EXAM
  entity in a document maps to that document's canonical exam
  (DOC_EXAMS below; UPSC has two — CSE and Indian Forest Service,
  split on the word "forest"). This kills the variant explosion:
  advt numbers, paper names and stage names ("PAPER-I", "Tier-II",
  "CEN No. 06/2026") all resolve to the document's exam. Unknown
  documents (Phase 8) fall back to rule-based canonicalization.
  EXAM/POST ids are DOCUMENT-SCOPED: the same post name in two
  notifications is two different posts ("Probationary Officer" at
  IBPS vs SBI have different age limits).
- POST: strip parentheticals, expand acronyms. Pruned by JOB_WORDS
  (see is_job_title): the models label syllabus topics as POST, so a
  kept post must look like a job title.
- QUALIFICATION: map to a degree level ("Bachelor's degree in any
  discipline" -> "bachelor's degree"). Only the 7 known levels are
  kept (see KNOWN_LEVELS): the models label syllabus topics and
  medical conditions as QUALIFICATION, and those must go.
- DOCUMENT: keyword map (aadhaar/pan/passport/...). Sentence-like
  names, acts and people are dropped by is_valid_document.
- AGE_LIMIT: "20 to 30 years" and "Not below 21 years and not above
  30 years" both become "21-30 years" (or "20-30 years"). Kept: a
  strict range with min >= 18, or a single bound N years with N >= 18
  (kills heights, rule texts, relaxation clauses).
- DATE: parsed to ISO ("21.07.2026", "July 21, 2026" ->
  "2026-07-21"). Unparseable values keep their normalized name.
- FEE: "Rs. 850" / "850/-" -> "rs. 850" (number extraction).
- ORG: basic string cleanup only.

Every rule here is conservative and documented. Extend the maps when
Phase 8 brings new orgs, exams or documents.
"""
import re

# The Phase 2 schema. Entities/relations outside it (the models
# sometimes invent types like RELIGION or ORGANISM from syllabus
# text) are dropped by the resolver/builder and counted, never kept.
SCHEMA_TYPES = {
    "EXAM",
    "POST",
    "ORG",
    "QUALIFICATION",
    "AGE_LIMIT",
    "DATE",
    "FEE",
    "DOCUMENT",
}
SCHEMA_RELATIONS = {
    "CONDUCTED_BY",
    "OFFERS",
    "REQUIRES",
    "HAS_AGE_LIMIT",
    "LAST_DATE",
    "EXAM_DATE",
    "APPLICATION_FEE",
    "REQUIRES_DOCUMENT",
}

# Hand-built acronym map — only orgs in our 9-notification corpus.
ACRONYM_MAP = {
    "ibps": "institute of banking personnel selection",
    "sbi": "state bank of india",
    "rbi": "reserve bank of india",
    "ssc": "staff selection commission",
    "upsc": "union public service commission",
    "rrb": "railway recruitment board",
}

# Types whose node id includes the source document (see docstring).
DOC_SCOPED_TYPES = {"EXAM", "POST"}

# Each notification's canonical exam. Verified against the
# notifications themselves. Add one entry per new document in Phase 8.
DOC_EXAMS = {
    "IBPS_Clerk_CSA_XVI_Notification": "ibps clerk",
    "IBPS_PO_MT_XVI_Notification": "ibps po",
    "IBPS_PO_MT_XVI_PwBD_Notice": "ibps po",
    "RBI_Grade_B_2026_Notification": "rbi grade b",
    "RRB_NTPC_CEN_06_2026_Graduate": "rrb ntpc",
    "SBI_PO_2026_Notification": "sbi po",
    "SSC_CGL_2026_Notification": "ssc cgl",
    "SSC_CHSL_2026_Notification": "ssc chsl",
    "UPSC_CSE_2026_Notification": "civil services",
}

EXAM_ALIASES = {
    "cse": "civil services",
    "ifos": "indian forest service",
    "ifs": "indian forest service",
    "cgl": "combined graduate level",
    "chsl": "combined higher secondary level",
    "ntpc": "non technical popular categories",
    "csa": "customer service associate",
}
EXAM_DROP_WORDS = {
    "examination",
    "exam",
    "notification",
    "recruitment",
    "advertisement",
    "advt",
    "notice",
}

POST_ALIASES = {
    "po": "probationary officer",
    "csa": "customer service associate",
    "aso": "assistant section officer",
    "ias": "indian administrative service",
    "ips": "indian police service",
    "ifs": "indian foreign service",
    "irs": "indian revenue service",
    "ldc": "lower division clerk",
    "jsa": "junior secretariat assistant",
    "deo": "data entry operator",
    "udc": "upper division clerk",
    "jso": "junior statistical officer",
    "si": "sub inspector",
    "asi": "assistant sub inspector",
    "gr": "grade",
}
POST_DROP_WORDS = {"py"}  # panel-year noise ("PY 2026")

# A kept POST must contain one of these words. The models label
# syllabus topics ("Agriculture", "Psychology") and test names
# ("General Awareness") as POST; real posts are job titles.
JOB_WORDS = {
    "officer", "assistant", "inspector", "clerk", "auditor", "accountant",
    "master", "guard", "supervisor", "operator", "manager", "engineer",
    "analyst", "constable", "associate", "service", "agent", "technician",
    "superintendent", "controller", "investigator", "stenographer",
    "driver", "peon", "sepoy", "trainee", "grade", "instructor",
    "lecturer", "professor", "scientist", "nurse", "pharmacist",
    "surveyor", "draftsman", "mechanic", "electrician", "cook",
    "attendant", "helper", "coordinator", "executive", "advisor",
    "adviser", "director", "secretary", "registrar", "librarian",
    "translator", "programmer", "developer", "designer", "editor",
    "reporter", "pilot", "captain", "commander", "marshal", "warden",
    "ranger", "forester", "teacher", "tutor", "principal", "headmaster",
    "cashier", "teller", "conductor", "sub-inspector", "head", "chief",
    "deputy",
}

# The only qualification levels kept. Everything else the models
# labeled QUALIFICATION (syllabus topics, medical terms, disability
# codes) is dropped.
KNOWN_LEVELS = {
    "phd",
    "master's degree",
    "bachelor's degree",
    "class xii",
    "class x",
    "diploma",
    "certificate",
}

# Minimum age for a kept AGE_LIMIT (kills "1-2 years" experience
# periods, heights like "152 cms", and rule-violation texts).
AGE_MIN_YEARS = 18

DOCUMENT_KEYWORDS = [
    ("aadhaar", "aadhaar card"),
    ("aadhar", "aadhaar card"),
    ("pan card", "pan card"),
    ("passport", "passport"),
    ("voter", "voter id"),
    ("driving licence", "driving licence"),
    ("driving license", "driving licence"),
    ("photograph", "photograph"),
    ("photo", "photograph"),
    ("signature", "signature"),
    ("caste certificate", "caste certificate"),
    ("income", "income certificate"),
    ("disability", "disability certificate"),
    ("ews", "ews certificate"),
    ("domicile", "domicile certificate"),
    ("birth certificate", "birth certificate"),
    ("marksheet", "marksheet"),
    ("mark sheet", "marksheet"),
    ("degree certificate", "degree certificate"),
    ("experience certificate", "experience certificate"),
    ("migration", "migration certificate"),
    ("transfer certificate", "transfer certificate"),
    ("character certificate", "character certificate"),
    ("noc", "noc"),
    ("no objection certificate", "noc"),
    ("obc", "obc certificate"),
]

# Words that disqualify a DOCUMENT (acts, people, generic words).
DOC_STOPWORDS = {"act", "officer", "representative", "signatory"}

# Display names for canonical exams (the most frequent raw spelling
# is often an abbreviation like "CRP" or a stage like "Tier-II").
EXAM_DISPLAY = {
    "ibps clerk": "IBPS Clerk (CSA)",
    "ibps po": "IBPS PO",
    "rbi grade b": "RBI Grade B",
    "rrb ntpc": "RRB NTPC",
    "sbi po": "SBI PO",
    "ssc cgl": "SSC CGL",
    "ssc chsl": "SSC CHSL",
    "civil services": "Civil Services Examination",
    "indian forest service": "Indian Forest Service Examination",
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _plural_strip(word: str) -> str:
    """Light plural-strip: officers -> officer (not ss/us endings)."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def normalize_name(name: str) -> str:
    """Lowercase, strip parentheticals, collapse whitespace, expand
    acronyms, strip plurals."""
    s = name.strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .,:;")
    words = [_plural_strip(ACRONYM_MAP.get(w, w)) for w in s.split(" ")]
    return " ".join(words)


def _canonical_exam_fallback(base: str) -> str:
    """Rule-based exam canonicalization (unknown documents only)."""
    s = re.sub(r"\([^)]*\)", "", base)  # drop (preliminary), (main), ...
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)  # drop years
    s = re.sub(r"\b[ivxlcdm]+\b", "", s)  # drop roman numerals (editions, tiers)
    for org in sorted(ACRONYM_MAP.values(), key=len, reverse=True):
        if s.startswith(org + " "):
            s = s[len(org) + 1 :]
            break
    words = [w for w in s.split(" ") if w and w not in EXAM_DROP_WORDS]
    words = [EXAM_ALIASES.get(w, w) for w in words]
    s = " ".join(words)
    return s or base


def canonical_exam_for_doc(name: str, stem: str | None) -> str:
    """A document's EXAM entities all map to its canonical exam."""
    if stem == "UPSC_CSE_2026_Notification" and "forest" in normalize_name(name):
        return "indian forest service"
    if stem in DOC_EXAMS:
        return DOC_EXAMS[stem]
    if stem in _DOC_CANON_OVERRIDES:
        return _DOC_CANON_OVERRIDES[stem]
    return _canonical_exam_fallback(normalize_name(name))


# Per-stem canonical exams derived at graph-build time for documents
# NOT in DOC_EXAMS (Phase 12: new notifications from the daily
# update). Registered by app.graph.resolver.derive_doc_exams via
# set_doc_canon_overrides before resolution; DOC_EXAMS always wins.
_DOC_CANON_OVERRIDES: dict[str, str] = {}


def set_doc_canon_overrides(overrides: dict[str, str]) -> None:
    """Register derived canonical exams for unknown documents."""
    _DOC_CANON_OVERRIDES.update(overrides)


# Words that carry no exam identity on their own. A derived exam name
# made ONLY of these (e.g. "crp", "tier-ii", "paper-i") is discarded
# during derivation — the most frequent raw EXAM spelling in a
# notification is often a stage name, not the exam.
_EXAM_DERIVE_STOPWORDS = {
    "crp", "tier", "paper", "phase", "prelims", "preliminary", "mains",
    "main", "stage", "part", "exam", "examination", "test", "online",
    "i", "ii", "iii", "iv",
}


def canonical_exam_name(name: str) -> str:
    """Derive a canonical exam name from one raw EXAM spelling
    (Phase 12: used to vote a new document's single canonical exam).

    Unlike _canonical_exam_fallback this keeps the org in the name
    ("SBI Clerk" -> "sbi clerk", not "clerk") and does not expand
    acronyms. Returns "" if nothing meaningful survives.
    """
    s = name.strip().lower()
    s = re.sub(r"\([^)]*\)", "", s)  # drop (preliminary), (main), ...
    s = re.sub(r"[–—-]", " ", s)  # dashes -> spaces ("Tier-II" -> "tier ii")
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)  # drop years
    s = re.sub(r"\b[ivxlcdm]+\b", "", s)  # drop roman numerals
    words = [w for w in s.split() if w and w not in EXAM_DROP_WORDS]
    words = [EXAM_ALIASES.get(w, w) for w in words]
    words = [w for w in words if w not in _EXAM_DERIVE_STOPWORDS]
    return " ".join(words)


def _canonical_post(base: str) -> str:
    s = re.sub(r"\([^)]*\)", "", base)
    s = re.sub(r"^(the\s+)?posts?\s+of\s+", "", s)  # "post of Clerk" -> "clerk"
    s = re.sub(r"^officers?\s+in\s+", "", s)  # "Officers in Grade B" -> "grade b"
    s = re.sub(r"\b(19|20)\d{2}\b", "", s)  # years ("PY 2026")
    s = re.sub(r"[–—-]", " ", s)  # dashes -> spaces ("Grade-B" == "Grade B")
    s = re.sub(r"\s+(cadre|stream)s?\s*$", "", s)  # trailing "cadre"/"stream"
    words = [
        POST_ALIASES.get(w, w)
        for w in s.split(" ")
        if w and w not in POST_DROP_WORDS
    ]
    s = " ".join(words)
    return s or base


def _canonical_qualification(base: str) -> str:
    s = base
    if re.search(r"\b(ph\.?d|doctorate)\b", s):
        return "phd"
    if re.search(
        r"\b(master|m\.?tech|m\.?sc|mba|m\.?com|m\.?a|postgraduate|post graduate)\b",
        s,
    ):
        return "master's degree"
    if re.search(
        r"\b(bachelor|b\.?tech|b\.?sc|b\.?com|b\.?a|b\.?e|graduate|graduation|degree)\b",
        s,
    ):
        return "bachelor's degree"
    if re.search(r"\b(12th|xii|higher secondary|intermediate|10\+2)\b", s):
        return "class xii"
    if re.search(r"\b(10th|matriculation|secondary)\b", s):
        return "class x"
    if "diploma" in s:
        return "diploma"
    if "certificate" in s:
        return "certificate"
    return base


def _canonical_document(base: str) -> str:
    for kw, canon in DOCUMENT_KEYWORDS:
        if kw in base:
            return canon
    return base


def _canonical_age_limit(base: str) -> str:
    """Extract the age range: "21-30 years" -> "21-30 years";
    "Not below 21 years and not above 30 years" -> "21-30 years";
    "21 years" -> "21 years". The hyphenated range is tried first
    (it needs no "years" suffix — "18-33" is a valid range); the
    findall then catches "not below X / not above Y" phrasings.
    """
    m = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", base)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))} years"
    nums = re.findall(r"\b(\d{1,2})\s*years?", base)
    if len(nums) >= 2:
        return f"{int(nums[0])}-{int(nums[1])} years"
    if len(nums) == 1:
        return f"{int(nums[0])} years"
    return base


def _canonical_date(base: str) -> str:
    """Parse common date formats to ISO (YYYY-MM-DD or YYYY-MM).

    Unparseable values (relative dates like "August 2026" without a
    day, "closing date") fall back to the normalized name.
    """
    m = re.search(r"(\d{1,2})[.\-/ ]+(\d{1,2})[.\-/ ]+(\d{4})", base)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(
        r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})",
        base,
    )
    if m:
        mo, d, y = MONTHS[m.group(1)], int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(
        r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*(" + "|".join(MONTHS) + r")\s*,?\s*(\d{4})",
        base,
    )
    if m:
        d, y = int(m.group(1)), int(m.group(3))
        mo = MONTHS[m.group(2)]
        if 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(" + "|".join(MONTHS) + r")\s*,?\s*(\d{4})", base)
    if m:
        return f"{int(m.group(2)):04d}-{MONTHS[m.group(1)]:02d}"
    return base


def is_parseable_date(canonical_date_name: str) -> bool:
    """True if a canonical DATE name parsed to ISO form."""
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", canonical_date_name)
        or re.fullmatch(r"\d{4}-\d{2}", canonical_date_name)
    )


def _canonical_fee(base: str) -> str:
    m = re.search(r"(\d[\d,]*)", base)
    if m:
        return f"rs. {m.group(1).replace(',', '')}"
    return base


def canonical_name(entity_type: str, name: str) -> str:
    """Type-aware canonical form of an entity name (the dedupe key)."""
    base = normalize_name(name)
    if entity_type == "POST":
        return _canonical_post(base)
    if entity_type == "QUALIFICATION":
        return _canonical_qualification(base)
    if entity_type == "DOCUMENT":
        return _canonical_document(base)
    if entity_type == "AGE_LIMIT":
        return _canonical_age_limit(base)
    if entity_type == "FEE":
        return _canonical_fee(base)
    if entity_type == "DATE":
        return _canonical_date(base)
    return base


def node_id(entity_type: str, name: str, stem: str | None = None) -> str:
    """Canonical node id. EXAM/POST ids include the document stem."""
    if entity_type == "EXAM":
        base = canonical_exam_for_doc(name, stem)
    else:
        base = canonical_name(entity_type, name)
    if entity_type in DOC_SCOPED_TYPES and stem:
        return f"{entity_type}:{stem}:{base}"
    return f"{entity_type}:{base}"


def is_job_title(canonical_post_name: str) -> bool:
    """True if a canonical POST name looks like a job title.

    'grade' only counts as the FIRST word: "Grade B (DR)" is a post,
    "Trachoma Grade III" is a disease. Names containing 'test' are
    never posts ("Skill Test", "Typing Test").
    """
    words = canonical_post_name.replace("-", " ").replace("/", " ").split()
    if "test" in words:
        return False
    word_set = set(words)
    if "grade" in word_set and (not words or words[0] != "grade"):
        word_set.discard("grade")
    return bool(word_set & JOB_WORDS)


def is_known_level(canonical_qual_name: str) -> bool:
    """True if a canonical QUALIFICATION is a known degree level."""
    return canonical_qual_name in KNOWN_LEVELS


def is_valid_age(canonical_age_name: str) -> bool:
    """True if a canonical AGE_LIMIT is a strict range (min >= 18) or a
    single bound (N years, N >= 18)."""
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2}) years", canonical_age_name)
    if m:
        return int(m.group(1)) >= AGE_MIN_YEARS
    m = re.fullmatch(r"(\d{1,2}) years", canonical_age_name)
    return bool(m) and int(m.group(1)) >= AGE_MIN_YEARS


def is_valid_document(canonical_doc_name: str) -> bool:
    """True if a canonical DOCUMENT looks like a real document: short,
    no stoplist word (acts, people), not the generic word 'document'."""
    if canonical_doc_name in ("document", "documents"):
        return False
    words = set(canonical_doc_name.split())
    if words & DOC_STOPWORDS:
        return False
    return len(canonical_doc_name.split()) <= 6
