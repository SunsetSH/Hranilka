"""Генератор фейковых ПД: морфология фамилий/отчеств, согласование имя-эпоха,
адреса, детерминизм при заданном rng."""
import random
from datetime import date

from hranilka.generators import pd_data
from hranilka.generators import pd_generator


def test_feminize_surname_rules():
    assert pd_generator._feminize_surname("Толстой") == "Толстая"
    assert pd_generator._feminize_surname("Иванов") == "Иванова"
    assert pd_generator._feminize_surname("Соловьёв") == "Соловьёва"
    assert pd_generator._feminize_surname("Кузьмин") == "Кузьмина"
    assert pd_generator._feminize_surname("Черных") == "Черных"
    assert pd_generator._feminize_surname("Шевченко") == "Шевченко"


def test_derive_patronymic_exceptions():
    assert pd_generator._derive_patronymic("Дмитрий") == ("Дмитриевич", "Дмитриевна")
    assert pd_generator._derive_patronymic("Илья") == ("Ильич", "Ильинична")
    assert pd_generator._derive_patronymic("Лев") == ("Львович", "Львовна")


def test_derive_patronymic_morphology_rules():
    assert pd_generator._derive_patronymic("Николай") == ("Николаевич", "Николаевна")
    assert pd_generator._derive_patronymic("Игорь") == ("Игоревич", "Игоревна")
    assert pd_generator._derive_patronymic("Александр") == ("Александрович", "Александровна")
    assert pd_generator._derive_patronymic("Никита") == ("Никитич", "Никитична")
    assert pd_generator._derive_patronymic("Анатолий") == ("Анатольевич", "Анатольевна")


def test_pick_name_for_decade_respects_era():
    rng = random.Random(42)
    era_1950s_names = {n for n, w in pd_data._RU_FEMALE_NAME_WEIGHTS.items()
                        if dict(zip(pd_data.RU_DECADES, w))[1950] > 0}
    for _ in range(50):
        name = pd_generator._pick_name_for_decade(rng, pd_data.RU_FEMALE_NAMES, 1952)
        assert name in era_1950s_names


def test_pick_surname_weighted_towards_common_names():
    rng = random.Random(7)
    picks = [pd_generator._pick_surname(rng, pd_data.RU_SURNAMES) for _ in range(200)]
    top_surname = pd_data.RU_SURNAMES[0][0]
    rarest_surname = pd_data.RU_SURNAMES[-1][0]
    assert picks.count(top_surname) > picks.count(rarest_surname)


def test_build_address_ru_contains_city_and_valid_zip():
    rng = random.Random(3)
    address = pd_generator._build_address(rng, pd_data.RU_CITIES, "ru")
    assert not address.startswith(",") and not address.endswith(", ")
    assert ",," not in address
    matched_city = next(c for c in pd_data.RU_CITIES if c in address)
    zip_range = pd_data.RU_CITIES[matched_city]["zip"]
    zip_in_address = int(address.rsplit(", ", 1)[-1])
    assert zip_range[0] <= zip_in_address <= zip_range[1]


def test_generate_person_ru_has_all_fields():
    person = pd_generator.generate_person("ru", rng=random.Random(42))
    assert set(person) == {"first", "last", "middle", "birth_date", "gender", "address"}
    assert person["middle"] != ""
    assert isinstance(person["birth_date"], date)
    age = date.today().year - person["birth_date"].year
    assert 17 <= age <= 76


def test_generate_person_en_has_no_middle_name():
    person = pd_generator.generate_person("en", rng=random.Random(42))
    assert person["middle"] == ""
    assert any(ch.isdigit() for ch in person["address"])


def test_generate_person_is_deterministic_with_seeded_rng():
    person1 = pd_generator.generate_person("ru", rng=random.Random(123))
    person2 = pd_generator.generate_person("ru", rng=random.Random(123))
    assert person1 == person2
