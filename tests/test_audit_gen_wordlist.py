"""Аудит B11: словарь парольных фраз, энтропия, словарь BIP-39."""
import hashlib
import math
import re

from hranilka.core.bip39_words import BIP39_WORDS
from hranilka.generators import password_gen
from hranilka.generators.password_gen import (
    PassphraseOptions, WORDLIST, generate_passphrase, passphrase_entropy_bits,
    settings_from_config)

# Хэш отсортированного списка (bip39_words — frozenset, порядок официального
# english.txt утрачен, поэтому хэш из шапки модуля напрямую не проверить).
BIP39_SORTED_SHA256 = (
    "187db04a869dd9bc7be80d21a86497d692c0db6abd3aa8cb6be5d618ff757fae")


def test_wordlist_size_unique_charset():
    assert len(WORDLIST) >= 2048
    assert len(set(WORDLIST)) == len(WORDLIST)
    assert all(re.fullmatch(r"[a-z]{3,9}", w) for w in WORDLIST)


def test_entropy_formula():
    n = len(WORDLIST)
    plain = PassphraseOptions(words=6, add_digit=False)
    assert passphrase_entropy_bits(plain) == 6 * math.log2(n)
    with_digit = PassphraseOptions(words=6, add_digit=True)
    assert math.isclose(passphrase_entropy_bits(with_digit),
                        6 * math.log2(n) + math.log2(6) + math.log2(10))
    assert passphrase_entropy_bits(PassphraseOptions(words=999)) == \
        passphrase_entropy_bits(PassphraseOptions(words=password_gen.WORDS_MAX))
    assert passphrase_entropy_bits(PassphraseOptions()) > 66


def test_default_words_at_least_six():
    assert PassphraseOptions().words >= 6
    assert settings_from_config({}).phrase.words >= 6


def test_passphrase_uses_wordlist():
    phrase = generate_passphrase(PassphraseOptions(
        words=6, separator="-", capitalize=False, add_digit=False))
    assert all(w in WORDLIST for w in phrase.split("-"))


def test_bip39_len_unique_hash():
    assert len(BIP39_WORDS) == 2048
    assert len(set(BIP39_WORDS)) == 2048
    digest = hashlib.sha256("\n".join(sorted(BIP39_WORDS)).encode()).hexdigest()
    assert digest == BIP39_SORTED_SHA256
