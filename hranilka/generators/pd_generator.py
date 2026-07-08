"""Генератор случайных персональных данных с учётом рода и эпохи рождения.

generate_person(lang, rng=None) -> dict: first, last, middle, birth_date
(datetime.date), gender, address.
gender: "m" | "f".
"""
import random
from datetime import date, timedelta
from typing import Optional, Sequence

from hranilka.generators import pd_data

RandomLike = random.Random


def _weighted_choice(rng: RandomLike, items: Sequence[str], weights: Sequence[int]) -> str:
    if sum(weights) <= 0:
        weights = [1] * len(items)
    return rng.choices(items, weights=weights, k=1)[0]


def _pick_name_for_decade(rng: RandomLike, names_by_decade: dict[str, dict[int, int]],
                           birth_year: int) -> str:
    names = list(names_by_decade.keys())
    decades = names_by_decade[names[0]].keys()
    nearest = min(decades, key=lambda d: abs(d - birth_year))
    weights = [names_by_decade[name][nearest] for name in names]
    return _weighted_choice(rng, names, weights)


def _pick_surname(rng: RandomLike, surnames: Sequence[tuple[str, int]]) -> str:
    names = [name for name, _ in surnames]
    weights = [weight for _, weight in surnames]
    return _weighted_choice(rng, names, weights)


def _feminize_surname(male_surname: str) -> str:
    """Преобразует мужскую фамилию в женскую."""
    if male_surname.endswith("ий"):              # -ский -> -ская
        return male_surname[:-2] + "ая"
    if male_surname.endswith("ой"):               # -ой -> -ая (Толстой -> Толстая)
        return male_surname[:-2] + "ая"
    if male_surname.endswith(("ов", "ев", "ёв", "ин", "ын")):
        return male_surname + "а"
    return male_surname                           # несклоняемые (-ко, -ух, -ых) оставляем как есть


def _derive_patronymic(father_name: str) -> tuple[str, str]:
    """Отчество (муж., жен.) от имени отца по морфологическим правилам."""
    if father_name in pd_data.RU_PATRONYMIC_EXCEPTIONS:
        return pd_data.RU_PATRONYMIC_EXCEPTIONS[father_name]
    if father_name.endswith("ий"):                # Анатолий -> Анатольевич
        stem = father_name[:-2]
        return stem + "ьевич", stem + "ьевна"
    if father_name.endswith(("ай", "ей", "ой", "уй")):  # Николай, Сергей -> …евич
        stem = father_name[:-1]
        return stem + "евич", stem + "евна"
    if father_name.endswith("ь"):                 # Игорь -> Игоревич
        stem = father_name[:-1]
        return stem + "евич", stem + "евна"
    if father_name.endswith(("а", "я")):           # Никита -> Никитич
        stem = father_name[:-1]
        return stem + "ич", stem + "ична"
    return father_name + "ович", father_name + "овна"  # Александр -> Александрович


def _pick_father_name(rng: RandomLike, birth_year: int) -> str:
    father_birth_year = birth_year - 28
    return _pick_name_for_decade(rng, pd_data.RU_MALE_NAMES, father_birth_year)


def _random_birth_date(rng: RandomLike, min_age: int = 18, max_age: int = 75) -> date:
    today = date.today()
    age = rng.randint(min_age, max_age)
    start = today.replace(year=today.year - age - 1)
    end = today.replace(year=today.year - age)
    delta_days = (end - start).days
    return start + timedelta(days=rng.randint(0, max(delta_days - 1, 0)))


def _build_address(rng: RandomLike, cities: dict[str, dict], lang: str) -> str:
    city_names = list(cities.keys())
    weights = [cities[name]["w"] for name in city_names]
    city = _weighted_choice(rng, city_names, weights)
    info = cities[city]
    street = rng.choice(info["streets"])
    zip_code = rng.randint(*info["zip"])

    if lang == "ru":
        house = f"д. {rng.randint(1, 120)}"
        parts = [f"г. {city}", f"ул. {street}", house]
        if rng.random() < 0.3:
            parts.append(f"корп. {rng.randint(1, 5)}")
        parts.append(f"кв. {rng.randint(1, 300)}")
        parts.append(str(zip_code))
        return ", ".join(parts)

    parts = [f"{rng.randint(1, 9999)} {street}"]
    if rng.random() < 0.2:
        parts.append(f"Apt {rng.randint(1, 40)}")
    parts.append(city)
    parts.append(str(zip_code))
    return ", ".join(parts)


def generate_person(lang: str = "ru", rng: Optional[RandomLike] = None) -> dict:
    rng = rng if rng is not None else random.Random()
    gender = rng.choice(["m", "f"])
    birth = _random_birth_date(rng)

    if lang == "ru":
        if gender == "m":
            first = _pick_name_for_decade(rng, pd_data.RU_MALE_NAMES, birth.year)
        else:
            first = _pick_name_for_decade(rng, pd_data.RU_FEMALE_NAMES, birth.year)
        surname_base = _pick_surname(rng, pd_data.RU_SURNAMES)
        last = _feminize_surname(surname_base) if gender == "f" else surname_base

        father_name = _pick_father_name(rng, birth.year)
        male_form, female_form = _derive_patronymic(father_name)
        middle = male_form if gender == "m" else female_form

        address = _build_address(rng, pd_data.RU_CITIES, "ru")
    else:  # en
        if gender == "m":
            first = _pick_name_for_decade(rng, pd_data.EN_MALE_NAMES, birth.year)
        else:
            first = _pick_name_for_decade(rng, pd_data.EN_FEMALE_NAMES, birth.year)
        last = _pick_surname(rng, pd_data.EN_SURNAMES)
        middle = ""  # отчество в английском не используется
        address = _build_address(rng, pd_data.EN_CITIES, "en")

    return {"first": first, "last": last, "middle": middle,
            "birth_date": birth, "gender": gender, "address": address}
