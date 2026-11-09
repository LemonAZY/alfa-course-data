import math
import re
from collections import Counter

from app.models import Material, Profile, Recommendation, Result

ALIASES = {
    "маркетинг": ("продвиж", "реклам", "маркетинг", "клиент", "соцсет", "трафик", "лид"),
    "продажи": ("продаж", "воронк", "переговор", "возражен", "конверси", "сделк"),
    "финансы": ("денеж", "финанс", "бюджет", "кассов", "прибыл", "расход", "юнит"),
    "учёт": ("налог", "отчет", "учет", "бухгалтер", "деклара", "усн", "ндс"),
    "запуск": ("запуск", "откры", "регистрац", "идея", "с чего начать", "ип"),
    "команда": ("команд", "найм", "наня", "сотрудник", "делегир"),
}
STOP = set("как что для это мне надо хочу нужно чтобы свой свое уже могу где чем или про бизнес бизнеса".split())
LEVELS = ("beginner", "intermediate", "advanced")


def tokens(value: str) -> set[str]:
    words = re.findall(r"[a-zа-я0-9]+", value.casefold().replace("ё", "е"))
    return {word for word in words if len(word) >= 2 and word not in STOP}


def themes(value: str) -> set[str]:
    normalized = value.casefold().replace("ё", "е")
    words = re.findall(r"[a-zа-я0-9]+", normalized)
    return {
        topic for topic, stems in ALIASES.items()
        if any(
            stem in normalized if " " in stem else
            any(word == stem if len(stem) <= 2 else word.startswith(stem) for word in words)
            for stem in stems
        )
    }


def overlap(left: set[str], right: set[str]) -> set[str]:
    keys = {word[:5] for word in right}
    return {word for word in left if word[:5] in keys}


class Ranker:
    def __init__(self, materials: list[Material]):
        if not materials or len({item.id for item in materials}) != len(materials):
            raise ValueError("Catalog must contain unique material IDs")
        self.materials = {item.id: item for item in materials}
        self.topic_text = {item.id: " ".join(item.topics) for item in materials}
        self.text = {
            item.id: tokens(f"{item.title} {self.topic_text[item.id]} {item.summary}")
            for item in materials
        }
        self.themes = {
            item.id: themes(f"{item.title} {self.topic_text[item.id]}")
            for item in materials
        }
        self.df = Counter(word[:5] for doc in self.text.values() for word in {w[:5] for w in doc})

    def recommend(self, profile: Profile) -> Result:
        query = f"{profile.topic} {profile.task}".strip()
        words, intents = tokens(query), themes(query)
        latest = {}
        for event in profile.history:
            if event.material_id in self.materials:
                latest.pop(event.material_id, None)
                latest[event.material_id] = event.event
        hidden = {key for key, event in latest.items() if event in {"completed", "dismissed", "purchased"}}
        recent = [key for key, event in reversed(list(latest.items()))
                  if event in {"view", "started", "clicked", "completed", "purchased"}][:5]
        history_topics = set().union(*(self.themes[key] for key in recent))
        related = {target for key in recent for target in self.materials[key].related}
        cold = not (query or latest or profile.stage or profile.level or profile.industry or profile.preferred_format)
        explicit_words = tokens(profile.topic)
        explicit_themes = themes(profile.topic)
        rows = []
        for item in self.materials.values():
            if item.id in hidden:
                continue
            direct = overlap(words, self.text[item.id])
            common = intents & self.themes[item.id]
            topic_words = overlap(words, tokens(self.topic_text[item.id]))
            relevant = bool(direct or common)
            score = 0.0
            reasons = []
            if profile.strategy == "baseline":
                score = 3 * len(topic_words) + len(intents & themes(self.topic_text[item.id]))
                if score:
                    reasons.append("Совпадает с темой запроса.")
            else:
                score = sum(1 + math.log((1 + len(self.materials)) / (1 + self.df[w[:5]]))
                            for w in sorted(direct)) + 3 * len(common)
                if overlap(explicit_words, tokens(self.topic_text[item.id])) or explicit_themes & themes(self.topic_text[item.id]):
                    score += 4
                if relevant:
                    reasons.append("Связан с вашей задачей" + (f" — {', '.join(sorted(common))}." if common else "."))
                if profile.industry and overlap(tokens(profile.industry), tokens(" ".join(item.industries))):
                    score += 2.5
                    reasons.append("Подходит для вашей отрасли.")
                if profile.stage and item.stages:
                    if profile.stage in item.stages:
                        score += 2
                        reasons.append("Подходит для вашей стадии бизнеса.")
                    else:
                        score -= 1
                if profile.level and item.level:
                    gap = LEVELS.index(item.level) - LEVELS.index(profile.level)
                    if gap == 0:
                        score += 1.8
                        reasons.append("Подходит по уровню подготовки.")
                    elif gap > 0:
                        score -= 1.5 * gap
                if profile.preferred_format == item.format:
                    score += 2.2
                    reasons.append("Совпадает с выбранным форматом.")
                if item.id in related or (history_topics & self.themes[item.id] and item.id not in recent):
                    score += 1.6
                    reasons.append("Продолжает тему из вашей истории.")
                if latest.get(item.id) == "started":
                    score += 1.8
                    reasons.append("Вы уже начали этот материал.")
                elif item.id in recent:
                    score -= 0.6
                if item.access == "free" and (cold or not relevant):
                    score += 1.4
                if cold and item.level == "beginner":
                    score += 1
            score += min(math.log1p(item.popularity), 8) * 0.07
            rows.append((item, score, relevant, reasons))

        has_match = any(row[2] for row in rows)
        mode = "cold_start" if cold else "fallback" if query and not has_match else "personalized"
        if profile.strategy == "personalized" and query and has_match:
            rows = [row for row in rows if row[2]]
        results = []
        formats = Counter()
        while rows and len(results) < profile.limit:
            row = min(rows, key=lambda entry: (
                -(entry[1] - (0.65 * formats[entry[0].format] if profile.strategy == "personalized" else 0)),
                entry[0].id,
            ))
            rows.remove(row)
            item, _, _, reasons = row
            formats[item.format] += 1
            fallback_reason = "Бесплатный материал для знакомства с платформой." if item.access == "free" else "Материал из доступной подборки."
            next_step = {
                "template": "Откройте шаблон и заполните его для своей задачи.",
                "calculator": "Проверьте свой сценарий в калькуляторе.",
                "course": "Начните с первого доступного урока.",
                "article": "Прочитайте статью и выберите одно действие на сегодня.",
            }.get(item.format, "Откройте материал и отметьте, что примените на практике.")
            if item.access == "paid":
                next_step = "Посмотрите карточку курса и условия доступа." if item.url else "Найдите этот материал в каталоге по названию."
            if item.access == "unknown":
                next_step = "Уточните условия доступа в каталоге."
            results.append(Recommendation(material=item, reasons=reasons or [fallback_reason], next_step=next_step))
        clarification = None
        if mode == "fallback":
            clarification = "Уточните, какого результата хотите добиться или выберите тему."
        elif mode == "cold_start":
            clarification = "По желанию укажите задачу, стадию бизнеса или удобный формат."
        return Result(strategy=profile.strategy, mode=mode, clarification=clarification, recommendations=results)
