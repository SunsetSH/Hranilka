"""Реестр шорткатов, словарь BIP-39 и данные генератора ПД — санити."""
import hashlib

from hranilka.core import shortcuts
from hranilka.core.bip39_words import BIP39_WORDS
from hranilka.generators import pd_data


def test_shortcut_ids_unique_and_consistent():
    ids = [d[0] for d in shortcuts.SHORTCUT_DEFS]
    assert len(ids) == len(set(ids))
    assert set(shortcuts.DEFAULTS) == set(ids) == set(shortcuts.LABELS)


def test_shortcut_default_sequences_unique_and_categories_known():
    seqs = [d[2] for d in shortcuts.SHORTCUT_DEFS if d[2]]
    assert len(seqs) == len(set(seqs))
    assert {d[3] for d in shortcuts.SHORTCUT_DEFS} <= set(shortcuts.CATEGORIES)


def test_shortcuts_effective_overrides():
    eff = shortcuts.effective({"shortcuts": {"add_account": "Ctrl+Q", "legacy": "X",
                                             "open_bin": ""}})
    assert eff["add_account"] == "Ctrl+Q"
    assert eff["open_bin"] == ""           # пусто = сочетание снято
    assert "legacy" not in eff
    assert eff["focus_search"] == shortcuts.DEFAULTS["focus_search"]
    assert shortcuts.effective({}) == shortcuts.DEFAULTS
    assert shortcuts.effective({"shortcuts": None}) == shortcuts.DEFAULTS


def test_bip39_wordlist_integrity():
    assert len(BIP39_WORDS) == 2048
    assert all(w.isascii() and w.islower() and 3 <= len(w) <= 8 for w in BIP39_WORDS)
    digest = hashlib.sha256(("\n".join(sorted(BIP39_WORDS)) + "\n").encode()).hexdigest()
    assert digest == "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"


def test_pd_data_sanity():
    for names, decades in ((pd_data.RU_MALE_NAMES, pd_data.RU_DECADES),
                           (pd_data.RU_FEMALE_NAMES, pd_data.RU_DECADES),
                           (pd_data.EN_MALE_NAMES, pd_data.EN_DECADES),
                           (pd_data.EN_FEMALE_NAMES, pd_data.EN_DECADES)):
        assert names
        for weights in names.values():
            assert tuple(weights) == tuple(decades)
            assert all(w >= 0 for w in weights.values()) and sum(weights.values()) > 0
    for surnames in (pd_data.RU_SURNAMES, pd_data.EN_SURNAMES):
        assert surnames and all(w > 0 for _n, w in surnames)
        assert len({n for n, _w in surnames}) == len(surnames)
    for cities in (pd_data.RU_CITIES, pd_data.EN_CITIES):
        for name, c in cities.items():
            lo, hi = c["zip"]
            assert c["w"] > 0 and lo <= hi, name
            assert c["streets"], name
