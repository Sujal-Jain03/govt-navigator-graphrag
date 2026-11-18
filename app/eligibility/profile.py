"""User profile for eligibility checks."""
from dataclasses import dataclass
from datetime import date

# Qualification levels, lowest first. Diploma sits between XII and
# bachelor's (post-X, pre-degree vocational track).
LEVEL_RANK = {
    "certificate": 0,
    "class x": 1,
    "class xii": 2,
    "diploma": 3,
    "bachelor's degree": 4,
    "master's degree": 5,
    "phd": 6,
}

# Friendly input spellings -> canonical level key.
LEVEL_ALIASES = {
    "ph.d": "phd",
    "phd": "phd",
    "doctorate": "phd",
    "master": "master's degree",
    "masters": "master's degree",
    "master's": "master's degree",
    "pg": "master's degree",
    "m.tech": "master's degree",
    "mtech": "master's degree",
    "bachelor": "bachelor's degree",
    "bachelors": "bachelor's degree",
    "graduate": "bachelor's degree",
    "graduation": "bachelor's degree",
    "b.tech": "bachelor's degree",
    "btech": "bachelor's degree",
    "b.sc": "bachelor's degree",
    "b.com": "bachelor's degree",
    "b.a": "bachelor's degree",
    "12th": "class xii",
    "xii": "class xii",
    "class 12": "class xii",
    "10th": "class x",
    "x": "class x",
    "class 10": "class x",
    "diploma": "diploma",
    "certificate": "certificate",
}

CATEGORIES = {"general", "sc", "st", "obc", "ews", "pwbd"}


@dataclass
class Profile:
    name: str
    date_of_birth: date
    qualification: str  # canonical level key, e.g. "bachelor's degree"
    category: str = "General"

    def __post_init__(self):
        q = self.qualification.strip().lower()
        self.qualification = LEVEL_ALIASES.get(q, q)
        if self.qualification not in LEVEL_RANK:
            raise ValueError(f"unknown qualification level: {self.qualification!r}")
        if self.category.strip().lower() not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")
        self.category = self.category.strip()

    @property
    def level_rank(self):
        return LEVEL_RANK[self.qualification]

    @property
    def is_reserved(self):
        return self.category.lower() != "general"

    def age_on(self, ref: date) -> int:
        years = ref.year - self.date_of_birth.year
        if (ref.month, ref.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years
