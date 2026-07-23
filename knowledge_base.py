import json
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_FILE = ROOT_DIR / "knowledge_terms.json"
STATIC_KNOWLEDGE_DIR = ROOT_DIR / "static" / "knowledge"


@dataclass(frozen=True)
class KnowledgeTerm:
    id: str
    title: str
    aliases: tuple[str, ...]
    category: str
    text: str
    image: str | None = None


MAIN_MENUS = {
    "technical": {
        "title": "Technical",
        "aliases": ("Technical", "menu=technical"),
        "categories": ("technical_indicators", "chart_patterns", "price_action"),
    },
    "options": {
        "title": "Options",
        "aliases": ("Options", "menu=options"),
        "categories": ("options_positions", "options_greeks", "options_strategies"),
    },
    "market_basics": {
        "title": "Market Basics",
        "aliases": ("Market Basics", "menu=market_basics"),
        "categories": ("macro", "tw_flows"),
    },
}

CATEGORIES = {
    "technical_indicators": {
        "title": "技術指標",
        "menu": "technical",
        "aliases": ("技術指標",),
    },
    "chart_patterns": {
        "title": "常見線型",
        "menu": "technical",
        "aliases": ("常見線型",),
    },
    "price_action": {
        "title": "價格行為",
        "menu": "technical",
        "aliases": ("價格行為",),
    },
    "options_positions": {
        "title": "基礎部位",
        "menu": "options",
        "aliases": ("基礎部位",),
    },
    "options_greeks": {
        "title": "Greeks",
        "menu": "options",
        "aliases": ("Greeks",),
    },
    "options_strategies": {
        "title": "策略",
        "menu": "options",
        "aliases": ("策略",),
    },
    "macro": {
        "title": "總經",
        "menu": "market_basics",
        "aliases": ("總經",),
    },
    "tw_flows": {
        "title": "台股籌碼",
        "menu": "market_basics",
        "aliases": ("台股籌碼",),
    },
}


def normalize_key(value):
    return " ".join(str(value or "").strip().lower().split())


def load_terms(path=KNOWLEDGE_FILE):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        KnowledgeTerm(
            id=row["id"],
            title=row["title"],
            aliases=tuple(row.get("aliases", ())),
            category=row["category"],
            text=row["text"],
            image=row.get("image"),
        )
        for row in data
    ]


TERMS = load_terms()
TERMS_BY_ID = {term.id: term for term in TERMS}


def terms_for_category(category_id):
    return [term for term in TERMS if term.category == category_id]


def find_menu(value):
    normalized = normalize_key(value)
    for menu_id, menu in MAIN_MENUS.items():
        values = (menu_id, menu["title"], *menu["aliases"])
        if normalized in {normalize_key(item) for item in values}:
            return menu_id
    return None


def find_category(value):
    normalized = normalize_key(value)
    for category_id, category in CATEGORIES.items():
        values = (category_id, category["title"], *category["aliases"])
        if normalized in {normalize_key(item) for item in values}:
            return category_id
    return None


def find_term(value):
    normalized = normalize_key(value)
    for term in TERMS:
        values = (term.id, term.title, *term.aliases)
        if normalized in {normalize_key(item) for item in values}:
            return term
    return None


def image_exists(term):
    return bool(term.image and (STATIC_KNOWLEDGE_DIR / term.image).is_file())
