"""Compare both rankers against fixed synthetic relevance judgments."""
import json
import math

from app.main import DEMO_PATH
from app.models import Material, Profile
from app.ranking import Ranker

CASES = [
    ("Первое продвижение", dict(topic="маркетинг", task="найти первых клиентов",
                              stage="launch", level="beginner", industry="услуги"),
     {"m1": 3, "m2": 2, "m3": 1}),
    ("Реклама для роста", dict(topic="маркетинг", task="система продвижения",
                               stage="growth", level="intermediate", industry="розница",
                               preferred_format="course"),
     {"m3": 3, "m2": 2, "m1": 1}),
    ("Работа с воронкой", dict(topic="продажи", task="шаблон воронки продаж", stage="operating",
                              level="intermediate", preferred_format="template"),
     {"s2": 3, "s1": 1, "s3": 1}),
    ("Кассовый разрыв", dict(topic="финансы", task="кассовый разрыв", stage="operating",
                            level="beginner"),
     {"f1": 3, "f2": 2}),
    ("Отчётность в услугах", dict(topic="учёт", task="календарь отчётности", stage="operating",
                                 level="intermediate", industry="услуги", preferred_format="template"),
     {"a2": 3, "a1": 1}),
    ("Продолжение обучения", dict(history=[dict(material_id="t1", event="completed")], stage="growth",
                                  level="intermediate"),
     {"t2": 3}),
]


def dcg(grades):
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def evaluate():
    ranker = Ranker([Material.model_validate(row) for row in json.loads(DEMO_PATH.read_text())])
    totals = {}
    for strategy in ("baseline", "personalized"):
        scores = []
        for name, profile, grades in CASES:
            result = ranker.recommend(Profile(**profile, strategy=strategy, limit=3))
            ids = [item.material.id for item in result.recommendations]
            ndcg = dcg([grades.get(key, 0) for key in ids]) / dcg(sorted(grades.values(), reverse=True)[:3])
            scores.append(ndcg)
            print(f"{strategy:12} {name:25} {ndcg:.3f}  {', '.join(ids)}")
        totals[strategy] = sum(scores) / len(scores)
    print("Средний NDCG@3")
    for strategy, score in totals.items():
        print(f"{strategy:12} {score:.3f}")


if __name__ == "__main__":
    evaluate()
