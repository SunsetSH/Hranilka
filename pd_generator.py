"""Генератор случайных персональных данных с учётом рода (для русского языка).

generate_person(lang) -> dict: first, last, middle, birth_date (datetime.date), gender.
gender: "m" | "f".
"""
import random
from datetime import date, timedelta

# ----- Русские данные -----

_RU_MALE_NAMES = [
    "Александр", "Дмитрий", "Сергей", "Андрей", "Иван", "Михаил", "Николай",
    "Павел", "Владимир", "Алексей", "Роман", "Кирилл", "Егор", "Максим",
]
_RU_FEMALE_NAMES = [
    "Анна", "Мария", "Елена", "Ольга", "Наталья", "Татьяна", "Ирина",
    "Екатерина", "Светлана", "Юлия", "Анастасия", "Дарья", "Ксения", "Любовь",
]

# Базовая (мужская) форма фамилии; женская образуется в _feminize_surname.
_RU_SURNAMES = [
    "Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов", "Попов", "Васильев",
    "Соколов", "Михайлов", "Новиков", "Фёдоров", "Морозов", "Волков", "Лебедев",
    "Достоевский", "Маяковский", "Чайковский",
]

# Отчества: (мужское, женское)
_RU_PATRONYMICS = [
    ("Александрович", "Александровна"),
    ("Дмитриевич", "Дмитриевна"),
    ("Сергеевич", "Сергеевна"),
    ("Андреевич", "Андреевна"),
    ("Иванович", "Ивановна"),
    ("Михайлович", "Михайловна"),
    ("Николаевич", "Николаевна"),
    ("Павлович", "Павловна"),
    ("Владимирович", "Владимировна"),
    ("Алексеевич", "Алексеевна"),
    ("Петрович", "Петровна"),
    ("Викторович", "Викторовна"),
]


def _feminize_surname(male_surname):
    """Преобразует мужскую фамилию в женскую."""
    if male_surname.endswith("ий"):          # -ский -> -ская
        return male_surname[:-2] + "ая"
    if male_surname.endswith(("ов", "ев", "ин", "ын")):
        return male_surname + "а"
    return male_surname                       # несклоняемые оставляем как есть


# ----- Английские данные -----

_EN_MALE_NAMES = ["James", "John", "Michael", "David", "Robert", "William",
                  "Daniel", "Joseph", "Thomas", "Christopher"]
_EN_FEMALE_NAMES = ["Mary", "Jennifer", "Linda", "Patricia", "Elizabeth",
                    "Susan", "Jessica", "Sarah", "Emily", "Emma"]
_EN_SURNAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller",
                "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas"]


def _random_birth_date(min_age=18, max_age=75):
    today = date.today()
    age = random.randint(min_age, max_age)
    # случайный день внутри выбранного возраста
    start = today.replace(year=today.year - age - 1)
    end = today.replace(year=today.year - age)
    delta_days = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta_days - 1, 0)))


def generate_person(lang="ru"):
    gender = random.choice(["m", "f"])
    birth = _random_birth_date()

    if lang == "ru":
        if gender == "m":
            first = random.choice(_RU_MALE_NAMES)
            last = random.choice(_RU_SURNAMES)
            middle = random.choice(_RU_PATRONYMICS)[0]
        else:
            first = random.choice(_RU_FEMALE_NAMES)
            last = _feminize_surname(random.choice(_RU_SURNAMES))
            middle = random.choice(_RU_PATRONYMICS)[1]
    else:  # en
        if gender == "m":
            first = random.choice(_EN_MALE_NAMES)
        else:
            first = random.choice(_EN_FEMALE_NAMES)
        last = random.choice(_EN_SURNAMES)
        middle = ""  # отчество в английском не используется

    return {"first": first, "last": last, "middle": middle,
            "birth_date": birth, "gender": gender}
