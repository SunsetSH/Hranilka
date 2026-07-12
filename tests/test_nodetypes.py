"""Константы типов узлов дерева (Фаза 0 фин-сущностей).

Проверяют значения констант (формат node["type"] исторический — совместимость
с БД и стешем) и состав множеств листовых типов."""
from hranilka.core import nodetypes as nt


def test_constant_values_match_legacy_literals():
    # Значения обязаны совпадать с историческими строковыми литералами —
    # они уже лежат в БД (sort_order-таблицы) и в node["type"] дерева.
    assert nt.FOLDER == "folder"
    assert nt.SERVICE == "service"
    assert nt.ACCOUNT == "account"
    assert nt.CARD == "card"
    assert nt.WALLET == "wallet"


def test_leaf_types_membership():
    assert nt.ACCOUNT in nt.LEAF_TYPES
    assert nt.CARD in nt.LEAF_TYPES
    assert nt.WALLET in nt.LEAF_TYPES
    # Тип «Банковский счёт» удалён (итерация 2), «Электронный кошелёк» —
    # в итерации 3.
    assert not hasattr(nt, "BANK_ACCOUNT")
    assert not hasattr(nt, "EWALLET")
    # Контейнеры листьями не являются.
    assert nt.FOLDER not in nt.LEAF_TYPES
    assert nt.SERVICE not in nt.LEAF_TYPES


def test_fin_leaf_types_are_leaves_without_account():
    # Итерация 3: остаются только card/wallet (bank_account и ewallet удалены).
    assert nt.FIN_LEAF_TYPES == {nt.CARD, nt.WALLET}
    assert nt.ACCOUNT not in nt.FIN_LEAF_TYPES
    # Финансовые листья — подмножество всех листьев.
    assert nt.FIN_LEAF_TYPES <= nt.LEAF_TYPES
