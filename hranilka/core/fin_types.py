"""Декларативные дескрипторы финансовых типов — единый источник истины для
UI (вкладки/поля), сериализации и экстракт-колонок. Без Qt и без SQL.

Из одного ItemTypeSpec выводятся: вкладки и поля карточки, список копируемых/
секретных полей, строки экспорта, экстракт card_last4/expires_on. Добавление
поля типа = одна строка в дескрипторе (закрытие риска «поля в 6+ местах»).
FieldSpec спроектирован совместимым, чтобы AccountData мог мигрировать позже.
"""
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from hranilka.core.fin_domain import card_last4, parse_expiry
from hranilka.core.nodetypes import CARD, WALLET

# Допустимые типы полей карточки. UI-фабрика отображает kind → виджет.
# "seed" — seed-фраза криптокошелька (сетка нумерованных ячеек, §6 концепта).
FIELD_KINDS = frozenset({"text", "multiline", "date", "enum", "int", "money",
                         "seed"})


@dataclass(frozen=True)
class FieldSpec:
    """Описание одного поля типа. secret — маскирование+reveal+копирование
    только через буфер с автоочисткой; options — варианты для kind='enum'."""
    key: str
    label: str
    kind: str
    tab: str
    secret: bool = False
    mask: Optional[str] = None          # подсказка UI-маски ('card' | 'last4' | ...)
    placeholder: str = ""
    options: tuple[str, ...] = ()       # варианты для enum
    # Подряд идущие поля одной вкладки с одинаковым row_group рендерятся в одну
    # строку (каждое — со своей подписью сверху, деля ширину поровну).
    row_group: Optional[str] = None


@dataclass(frozen=True)
class ListSpec:
    """Повторяемый блок полей (адреса, приватные ключи): каждый элемент —
    словарь по ключам item_fields."""
    key: str
    label: str
    tab: str
    item_fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class ItemTypeSpec:
    """Дескриптор финансового типа. extract(payload) -> (card_last4, expires_on)
    заполняет одноимённые экстракт-колонки БД (card_last4: str, expires_on: date
    или None). short_title — короткая подпись для кнопок ряда 2 («КАРТА»)."""
    type_id: str
    title: str
    node_type: str                      # тип узла дерева (CARD/WALLET из nodetypes)
    tree_prefix: str
    tabs: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    lists: tuple[ListSpec, ...]
    extract: Callable[[dict], tuple[str, Optional[date]]]
    short_title: str = ""               # короткая подпись кнопки («КАРТА»/«КРИПТО»)
    create_title: str = "Новая запись"  # заголовок диалога создания записи типа
    unsaved_label: str = "Записей"      # подпись подсчёта в диалоге закрытия
                                         # («Банковских карт»/«Криптокошельков»)

    def secret_keys(self) -> frozenset[str]:
        """Ключи секретных полей типа (для маскирования/копирования в UI)."""
        return frozenset(f.key for f in self.fields if f.secret)


# ─── Тип «Банковская карта» (bank_card) ──────────────────────────────────────

_PAYMENT_SYSTEMS = (
    "Мир", "Visa", "Mastercard", "American Express", "UnionPay", "JCB", "Maestro",
)
_CARD_KINDS = ("дебетовая", "кредитная", "предоплаченная", "виртуальная")
_CURRENCIES = ("RUB", "USD", "EUR", "GBP", "CNY", "KZT", "UAH")

_BANK_CARD_FIELDS = (
    # Вкладка «База»: валюта, дата выпуска и общие заметки (перенос со старой
    # вкладки «Заметки» — key сохранён, данные переживают перенос).
    FieldSpec("currency", "Валюта", "enum", "База", options=_CURRENCIES),
    FieldSpec("issue_date", "Дата выпуска", "date", "База"),
    FieldSpec("notes", "Заметки", "multiline", "База"),
    # Вкладка «Реквизиты»: номер карты, срок/CVV/PIN (три колонки, row_group
    # "ecp"), платёжная система и тип карты (row_group "ps" — перенос с «Базы»),
    # данные держателя.
    FieldSpec("card_number", "Номер карты", "text", "Реквизиты", secret=True,
              mask="card", placeholder="0000 0000 0000 0000"),
    FieldSpec("expiry", "Срок действия", "text", "Реквизиты", placeholder="MM/YY",
              row_group="ecp"),
    FieldSpec("cvv", "CVV/CVC", "text", "Реквизиты", secret=True, placeholder="000",
              row_group="ecp"),
    FieldSpec("pin", "PIN", "text", "Реквизиты", secret=True, row_group="ecp"),
    FieldSpec("payment_system", "Платёжная система", "enum", "Реквизиты",
              options=_PAYMENT_SYSTEMS, row_group="ps"),
    FieldSpec("card_kind", "Тип карты", "enum", "Реквизиты", options=_CARD_KINDS,
              row_group="ps"),
    FieldSpec("cardholder", "Держатель", "text", "Реквизиты",
              placeholder="IVAN IVANOV"),
    FieldSpec("holder_address", "Адрес держателя", "text", "Реквизиты"),
    # Вкладка «Счёт»: банковские реквизиты счёта карты. IBAN+SWIFT и ИНН+КПП —
    # парами в одну строку (row_group "ibsw"/"innkpp").
    FieldSpec("account_number", "Номер счёта", "text", "Счёт", secret=True,
              mask="last4"),
    FieldSpec("iban", "IBAN", "text", "Счёт", secret=True, row_group="ibsw"),
    FieldSpec("swift", "SWIFT", "text", "Счёт", row_group="ibsw"),
    FieldSpec("bik", "БИК", "text", "Счёт"),
    FieldSpec("corr_account", "Корр. счёт", "text", "Счёт"),
    FieldSpec("inn", "ИНН", "text", "Счёт", row_group="innkpp"),
    FieldSpec("kpp", "КПП", "text", "Счёт", row_group="innkpp"),
    FieldSpec("credit_limit", "Кредитный лимит", "money", "Счёт"),
    FieldSpec("contract_number", "Номер договора", "text", "Счёт"),
    # Вкладка «Банк»: банк-эмитент и его контакты (bank_name первым — перенос со
    # старой «Карты», key сохранён, данные переживают перенос).
    FieldSpec("bank_name", "Банк", "text", "Банк"),
    FieldSpec("bank_phone", "Телефон банка", "text", "Банк"),
    FieldSpec("code_word", "Кодовое слово", "text", "Банк", secret=True),
    FieldSpec("sms_phone", "Телефон для SMS", "text", "Банк"),
    FieldSpec("notes_bank", "Заметки (банк)", "multiline", "Банк"),
)


def _bank_card_extract(payload: dict) -> tuple[str, Optional[date]]:
    """Экстракт-значения карты: (последние 4 цифры номера, срок действия)."""
    last4 = card_last4(payload.get("card_number") or "")
    expires_on = parse_expiry(payload.get("expiry") or "")
    return (last4, expires_on)


_BANK_CARD = ItemTypeSpec(
    type_id="bank_card",
    title="Банковская карта",
    node_type=CARD,
    tree_prefix="[$] ",
    tabs=("База", "Реквизиты", "Счёт", "Банк"),
    fields=_BANK_CARD_FIELDS,
    lists=(),
    extract=_bank_card_extract,
    short_title="КАРТА",
    create_title="Создать банковскую карту",
    unsaved_label="Банковских карт",
)


# ─── Тип «Криптокошелёк» (crypto_wallet) ─────────────────────────────────────

_WALLET_KINDS = ("hot", "cold", "hardware", "exchange", "custodial")
_NETWORKS = ("BTC", "ETH", "TRON", "SOL", "TON", "other")

_CRYPTO_WALLET_FIELDS = (
    # Вкладка «Кошелёк»
    FieldSpec("wallet_kind", "Тип кошелька", "enum", "Кошелёк",
              options=_WALLET_KINDS),
    FieldSpec("vendor", "Приложение/устройство", "text", "Кошелёк",
              placeholder="MetaMask / Ledger / Trezor / Binance"),
    FieldSpec("derivation_path", "Derivation path", "text", "Кошелёк",
              placeholder="m/44'/60'/0'/0/0"),
    FieldSpec("created", "Дата создания", "date", "Кошелёк"),
    # Вкладка «Seed-фраза» (счётчик слов живёт внутри SeedPhraseWidget —
    # отдельного поля seed_words_count нет; старый ключ в payload игнорируется).
    FieldSpec("seed_phrase", "Seed-фраза", "seed", "Seed-фраза", secret=True),
    FieldSpec("passphrase", "Passphrase («25-е слово»)", "text", "Seed-фраза",
              secret=True),
    # Вкладка «Заметки»
    FieldSpec("notes", "Заметки", "multiline", "Заметки"),
)

_CRYPTO_WALLET_LISTS = (
    # Вкладка «Адреса»: один кошелёк — много адресов разных сетей. Адрес —
    # копируемый, НЕ секрет (публичная часть).
    ListSpec("addresses", "Адреса", "Адреса", (
        FieldSpec("network", "Сеть", "enum", "Адреса", options=_NETWORKS),
        FieldSpec("address", "Адрес", "text", "Адреса"),
        FieldSpec("label", "Метка", "text", "Адреса"),
    )),
    # Вкладка «Ключи»: приватные ключи — секрет, reveal построчно.
    ListSpec("private_keys", "Приватные ключи", "Ключи", (
        FieldSpec("label", "Метка", "text", "Ключи"),
        FieldSpec("key", "Ключ", "text", "Ключи", secret=True),
    )),
)


def _crypto_wallet_extract(payload: dict) -> tuple[str, Optional[date]]:
    """У кошелька нет ни last4, ни срока действия — экстракт-колонки пустые."""
    return ("", None)


_CRYPTO_WALLET = ItemTypeSpec(
    type_id="crypto_wallet",
    title="Криптокошелёк",
    node_type=WALLET,
    tree_prefix="[₿] ",
    tabs=("Кошелёк", "Seed-фраза", "Адреса", "Ключи", "Заметки"),
    fields=_CRYPTO_WALLET_FIELDS,
    lists=_CRYPTO_WALLET_LISTS,
    extract=_crypto_wallet_extract,
    short_title="КРИПТО",
    create_title="Создать криптокошелёк",
    unsaved_label="Криптокошельков",
)


# Реестр финансовых типов. Пункты меню создания и разбор дерева строятся отсюда.
FIN_TYPES: dict[str, ItemTypeSpec] = {
    _BANK_CARD.type_id: _BANK_CARD,
    _CRYPTO_WALLET.type_id: _CRYPTO_WALLET,
}


def _check_unique_node_types(types: dict[str, ItemTypeSpec]) -> None:
    """Узел дерева несёт только node_type (не type_id) — MainWindow.fin_tabs_by_type
    и FinCardMixin._open_fin_card ключуют карточку по нему (см. main_window.py).
    Совпадение node_type у двух типов реестра сделало бы их неразличимыми в
    дереве: один тип молча перекрыл бы карточку другого. Проверяем на загрузке
    модуля — ошибка конфигурации реестра падает сразу, а не тихой подменой
    полей в рантайме."""
    seen: dict[str, str] = {}
    for spec in types.values():
        clash = seen.get(spec.node_type)
        if clash is not None:
            raise ValueError(
                f"FIN_TYPES: node_type {spec.node_type!r} используют оба типа "
                f"{clash!r} и {spec.type_id!r} — узел дерева не может отличить "
                f"их карточки. Заведите отдельный node_type в core/nodetypes.py.")
        seen[spec.node_type] = spec.type_id


_check_unique_node_types(FIN_TYPES)
