"""How a word's meaning and reading are shown inside the game (2026-11-20).

Only game display uses this: the 3 choices, the reading under the image,
judging, the result review card and the Daily Question pages. Word detail
pages, category lists and the sitemap keep the stored value untouched, and
nothing here writes to the database.

Readings (`name` / `romaji`): every bracket and its contents are removed,
e.g. "kaki(kudamono)" -> "kaki".

Meanings (`englishname*`): a bracket is only touched when it duplicates the
word's own reading, because in many values the bracket is part of the
meaning ("To take (a photo)") or is the meaning itself
("Kyusu (Japanese teapot)"). Rules, in order:

1. Leave the value as it is when it contains Hangul or Thai script, has no
   bracket, has more than one bracket, or has unbalanced brackets.
2. The bracket matches the reading -> drop the bracket:
   "Colador (Zaru)" -> "Colador".
3. The text outside matches the reading -> show only the bracket's contents:
   "Motsunabe (Offal Hot Pot)" -> "Offal Hot Pot".
4. Otherwise leave the value as it is.

Brackets are ASCII "( )" and full-width.
"""
import re
import unicodedata

_OPENERS = '(（'
_CLOSERS = ')）'
_BRACKET = re.compile('[(（]([^()（）]*)[)）]')
_HANGUL_OR_THAI = re.compile('[ᄀ-ᇿ㄰-㆏가-힯฀-๿]')
_KANA_ONLY = re.compile('^[぀-ヿ 　]+$')
_LONG_VOWELS = (('ou', 'o'), ('uu', 'u'), ('oo', 'o'), ('aa', 'a'), ('ii', 'i'), ('ee', 'e'))


def _collapse(value):
    return ' '.join((value or '').split())


def _balanced(value):
    depth = 0
    for ch in value:
        if ch in _OPENERS:
            depth += 1
        elif ch in _CLOSERS:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def strip_brackets(value):
    """Remove every bracket with its contents (innermost first). Unbalanced
    brackets are left alone, and a value that is nothing but brackets keeps
    its inner text, so the result is never empty when the input wasn't."""
    text = _collapse(value)
    if not text or not _balanced(text):
        return text
    stripped = text
    while True:
        reduced = _BRACKET.sub(' ', stripped)
        if reduced == stripped:
            break
        stripped = reduced
    stripped = _collapse(stripped)
    if stripped:
        return stripped
    return _collapse(''.join(ch for ch in text if ch not in _OPENERS + _CLOSERS))


def _romaji_key(value):
    text = ''.join(ch for ch in unicodedata.normalize('NFKD', value or '') if not unicodedata.combining(ch)).casefold()
    text = re.sub('[^a-z0-9]', '', text)
    for long_vowel, short in _LONG_VOWELS:
        text = text.replace(long_vowel, short)
    return text


def _hiragana(value):
    return ''.join(chr(ord(ch) - 0x60) if 'ァ' <= ch <= 'ヶ' else ch for ch in value if not ch.isspace())


def _is_reading(part, romaji, jpname):
    part = _collapse(part)
    if not part:
        return False
    if _KANA_ONLY.match(part):
        kana, own = _hiragana(part), _hiragana(jpname or '')
        return bool(own) and (kana == own or (len(kana) >= 2 and (kana in own or own in kana)))
    key, own = _romaji_key(part), _romaji_key(strip_brackets(romaji))
    if not key or not own:
        return False
    return key == own or (min(len(key), len(own)) >= 4 and (key in own or own in key))


def reading_value(word):
    """The romaji a word's meanings are checked against: ThankJapanPremium
    has its own romaji column, ThankJapanModel keeps it in `name`."""
    romaji = getattr(word, 'romaji', None)
    if romaji is None:
        romaji = getattr(word, 'name', '')
    return romaji or ''


def game_meaning(value, romaji, jpname):
    """A meaning as the game shows it (see the module docstring)."""
    text = _collapse(value)
    if not text or _HANGUL_OR_THAI.search(text) or not _balanced(text):
        return text
    pairs = list(_BRACKET.finditer(text))
    brackets = sum(1 for ch in text if ch in _OPENERS)
    if len(pairs) != 1 or brackets != 1:
        return text
    match = pairs[0]
    inner = _collapse(match.group(1))
    outer = _collapse(text[:match.start()] + ' ' + text[match.end():])
    if not inner:
        return outer or text
    if _is_reading(inner, romaji, jpname):
        return outer or inner
    if outer and _is_reading(outer, romaji, jpname):
        return inner
    return text


def word_game_meaning(word, field):
    """game_meaning() of one meaning column of a word object."""
    return game_meaning(getattr(word, field, None), reading_value(word), word.jpname)
