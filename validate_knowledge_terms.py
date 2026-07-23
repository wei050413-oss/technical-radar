import json
import re
import sys
from collections import Counter
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
KNOWLEDGE_FILE = ROOT_DIR / "knowledge_terms.json"
IMAGE_DIR = ROOT_DIR / "static" / "knowledge"
REQUIRED_FIELDS = {"id", "title", "aliases", "category", "text"}
IMAGE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.png$")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
TRADITIONAL_GUARD_CHARS = set("为会个与专业买卖权价实图线体应过数据涨获筹码")


def has_cjk(value):
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def has_latin(value):
    return bool(re.search(r"[A-Za-z]", value))


def validate():
    errors = []
    data = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        return ["knowledge_terms.json must contain a JSON array."]

    ids = [row.get("id") for row in data if isinstance(row, dict)]
    id_counts = Counter(ids)
    for term_id, count in id_counts.items():
        if count > 1:
            errors.append(f"duplicate id: {term_id}")

    existing_ids = set(ids)
    normalized_titles = Counter()

    for index, row in enumerate(data):
        label = row.get("id", f"index:{index}") if isinstance(row, dict) else f"index:{index}"
        if not isinstance(row, dict):
            errors.append(f"{label}: entry must be an object")
            continue

        missing = REQUIRED_FIELDS - set(row)
        if missing:
            errors.append(f"{label}: missing required fields {sorted(missing)}")

        term_id = row.get("id", "")
        if not isinstance(term_id, str) or not ID_PATTERN.match(term_id):
            errors.append(f"{label}: id must be snake_case")

        title = row.get("title", "")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{label}: title must be a non-empty string")
        else:
            normalized_titles[title.strip().lower()] += 1

        aliases = row.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            errors.append(f"{label}: aliases must be a string array")
            aliases = []

        alias_keys = [alias.strip().lower() for alias in aliases]
        for alias, count in Counter(alias_keys).items():
            if count > 1:
                errors.append(f"{label}: duplicate alias {alias}")

        text = row.get("text", "")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{label}: text must be non-empty")
        else:
            if "InvestJar" in text:
                errors.append(f"{label}: text contains InvestJar")
            if "💡 KKZ 提醒" not in text:
                errors.append(f"{label}: text missing 💡 KKZ 提醒")
            if "📖 是什麼" not in text:
                errors.append(f"{label}: text missing 📖 是什麼")
            if "🧠 為什麼重要" not in text:
                errors.append(f"{label}: text missing 🧠 為什麼重要")
            if "👀 實際怎麼看" not in text:
                errors.append(f"{label}: text missing 👀 實際怎麼看")
            if "⚠️ 常見誤區" not in text:
                errors.append(f"{label}: text missing ⚠️ 常見誤區")
            if "🔗 延伸閱讀" not in text:
                errors.append(f"{label}: text missing 🔗 延伸閱讀")
            simplified_hits = sorted(TRADITIONAL_GUARD_CHARS & set(text))
            if simplified_hits:
                errors.append(f"{label}: possible simplified Chinese chars {''.join(simplified_hits)}")

        title_and_aliases = " ".join([str(title), *aliases])
        if not (has_latin(title_and_aliases) and has_cjk(title_and_aliases)):
            errors.append(f"{label}: title/aliases should include both English and Chinese naming")

        related_terms = row.get("related_terms", [])
        if related_terms is not None:
            if not isinstance(related_terms, list) or not all(isinstance(item, str) for item in related_terms):
                errors.append(f"{label}: related_terms must be a string array")
            else:
                for related_id, count in Counter(related_terms).items():
                    if count > 1:
                        errors.append(f"{label}: duplicate related_terms id {related_id}")
                for related_id in related_terms:
                    if related_id not in existing_ids:
                        errors.append(f"{label}: related_terms references missing id {related_id}")

        image = row.get("image")
        if image is not None:
            if not isinstance(image, str) or not IMAGE_PATTERN.match(image):
                errors.append(f"{label}: image filename must be lowercase kebab-case .png")
            if isinstance(image, str) and image.startswith(("http://", "https://")):
                errors.append(f"{label}: image must be a local filename, not a URL")

    for title_key, count in normalized_titles.items():
        if count > 1:
            errors.append(f"duplicate title: {title_key}")

    return errors


if __name__ == "__main__":
    problems = validate()
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}")
        sys.exit(1)

    data = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    image_count = sum(1 for row in data if row.get("image"))
    existing_image_count = sum(
        1 for row in data if row.get("image") and (IMAGE_DIR / row["image"]).is_file()
    )
    print(
        "knowledge_terms.json OK: "
        f"{len(data)} terms, {image_count} image references, "
        f"{existing_image_count} existing image files"
    )
