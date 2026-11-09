"""Read selected PostgreSQL COPY blocks without restoring or executing SQL."""
import argparse
import json
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit

from app.models import Material
from app.ranking import themes

FORMATS = {
    "Articles": "article", "Courses": "course", "Documents": "template",
    "Webinars": "webinar", "Podcasts": "podcast", "Glossary": "glossary",
    "Calculators": "calculator",
}
LOOKUPS = {
    "ContentSettingsTopics": ("TopicId", "Topics", "topics"),
    "ContentSettingsTags": ("TagId", "Tags", "topics"),
    "ContentSettingsIndustries": ("IndustryId", "Industries", "industries"),
}
TABLES = [
    *FORMATS, *LOOKUPS, "Topics", "Tags", "Industries", "Statuses",
    "ContentSettings", "RecommendedItems", "CourseTariffBindings", "CourseUserBindings",
]
COPY = re.compile(r'^COPY public\."?(\w+)"? \((.+)\) FROM stdin;$')
ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}
FIELDS = {
    "Id", "Title", "StatusId", "IsDeleted", "IsClosed", "IsPublic", "IsOld", "IsLesson",
    "Description", "DescriptionTag", "CardDescription", "Cost", "CourseId", "Url",
    "ContentSettingsId", "EntityId", "TopicId", "TagId", "IndustryId", "ItemId",
}


def decode(value):
    if value == r"\N":
        return ""
    return re.sub(r"\\([nrtbfv\\])", lambda match: ESCAPES[match[1]], value)


def read_tables(backup: Path, pg_restore: str):
    tables = defaultdict(list)
    command = [pg_restore, "--data-only", "--no-owner", "--file=-", "--schema=public"]
    for name in TABLES:
        command.extend(["--table", name])
    command.append(str(backup.resolve()))
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        subprocess.run(command, stdout=output, check=True)
        output.seek(0)
        table = None
        for raw_line in output:
            line = raw_line.rstrip("\n")
            if table is None:
                header = COPY.fullmatch(line)
                if header and header[1] in TABLES:
                    table = header[1]
                    columns = [column.strip(' "') for column in header[2].split(",")]
            elif line == r"\.":
                table = None
            else:
                tables[table].append({
                    key: decode(value)
                    for key, value in zip(columns, line.split("\t"), strict=True)
                    if key in FIELDS
                })
    return tables


def clean(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def published_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname and not parsed.username and
                not parsed.password and not parsed.query and not parsed.fragment):
            return value
    except ValueError:
        pass
    return None


def convert(tables):
    published = {row["Id"] for row in tables["Statuses"] if row["Title"].casefold() == "опубликовано"}
    if not published:
        raise ValueError("Published status is missing from the dump")
    settings = {row["Id"]: row["EntityId"] for row in tables["ContentSettings"]
                if row["IsDeleted"] != "t" and row["StatusId"] in published}
    metadata = defaultdict(lambda: defaultdict(set))
    for relation, (key, lookup, field) in LOOKUPS.items():
        names = {row["Id"]: clean(row["Title"]) for row in tables[lookup]
                 if row["IsDeleted"] != "t" and row.get("IsPublic") != "f"}
        for row in tables[relation]:
            entity, label = settings.get(row["ContentSettingsId"]), names.get(row[key])
            if entity and label:
                metadata[entity][field].add(label)
    paid = {row["CourseId"] for row in tables["CourseTariffBindings"]
            if row["Cost"] and Decimal(row["Cost"]) > 0}
    activity = Counter(row["CourseId"] for row in tables["CourseUserBindings"] if row["IsDeleted"] != "t")
    related = defaultdict(set)
    for row in tables["RecommendedItems"]:
        related[row["ItemId"]].add(row["CourseId"])
    materials = []
    for table, fmt in FORMATS.items():
        for row in tables[table]:
            if (row["IsDeleted"] == "t" or row.get("IsClosed") == "t" or
                    row.get("IsPublic") == "f" or row.get("IsOld") == "t" or
                    row.get("IsLesson") == "t" or
                    ("StatusId" in row and row["StatusId"] not in published)):
                continue
            key, title = row["Id"], clean(row["Title"])
            if not title:
                continue
            price = row.get("Cost")
            if key in paid or (price and Decimal(price) > 0):
                access = "paid"
            elif table in {"Articles", "Documents", "Glossary", "Calculators"} or price == "0":
                access = "free"
            else:
                access = "unknown"
            summary = "Учебный курс по указанной теме." if access == "paid" else clean(
                row.get("CardDescription") or row.get("DescriptionTag") or
                (row.get("Description", "") if access == "free" else "")
            )[:280]
            topics = metadata[key]["topics"] or themes(title)
            stages = []
            level = None
            if "запуск" in topics or re.search(r"начинающ|первые|с нуля", title.casefold()):
                stages, level = ["idea", "launch"], "beginner"
            elif re.search(r"масштабир|растущ", title.casefold()):
                stages, level = ["growth"], "intermediate"
            materials.append(Material(
                id=key, title=title, summary=summary, format=fmt, access=access,
                url=published_url(row.get("Url", "")), topics=sorted(topics),
                industries=sorted(metadata[key]["industries"]), stages=stages, level=level,
                related=sorted(related[key] - {key}), popularity=activity[key],
            ))
    ids = {item.id for item in materials}
    return [item.model_copy(update={"related": [key for key in item.related if key in ids]})
            for item in materials]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--output", type=Path, default=Path("private/catalog.json"))
    parser.add_argument("--pg-restore", default="pg_restore")
    args = parser.parse_args()
    materials = convert(read_tables(args.backup, args.pg_restore))
    if not materials:
        parser.error("No published materials were imported")
    if args.output.suffix != ".json" or args.output.parent.name != "private":
        parser.error("Use a .json path inside a directory named private")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([item.model_dump(mode="json") for item in materials],
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Imported {len(materials)} materials")
    print(dict(sorted(Counter((item.format, item.access) for item in materials).items())))


if __name__ == "__main__":
    main()
