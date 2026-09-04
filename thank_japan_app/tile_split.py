"""Word/phrase splitting for the tile-rearrangement game.

Two tile modes, chosen per word:

- mora split: for short, kana-only words (<=5 mora, no alphabet). Pure
  regex, no dependencies. Reads from the word's own hiragana (jpname).
- chunk split: for long words or words containing alphabet. Uses fugashi
  (bundles a compiled MeCab, no system libmecab needed) + unidic-lite,
  grouped into bunsetsu-like chunks. Reads from example_sentence_ja
  (ruby-tagged kanji sentence), not the bare hiragana reading, because
  hiragana-only input segments far less reliably (see
  memory/project_game_renewal_feasibility_2026_09_04.md).
"""

import re
import threading

import fugashi

MORA_MAX_FOR_TILE_SPLIT = 5

_SMALL_KANA = 'ゃゅょぁぃぅぇぉャュョァィゥェォヮゎ'
_MORA_RE = re.compile(rf'[ぁ-んァ-ヶー][{_SMALL_KANA}]?')
_ALPHA_RE = re.compile(r'[A-Za-z]')

_RUBY_RE = re.compile(r'<ruby>(.*?)<rt>.*?</rt></ruby>', re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')

_INDEPENDENT_POS1 = {
    '名詞', '動詞', '形容詞', '形状詞', '副詞', '連体詞', '接続詞', '感動詞', '代名詞',
}
_DEPENDENT_POS2 = {'非自立可能', '非自立'}

_tagger = None
_tagger_lock = threading.Lock()


def _get_tagger():
    global _tagger
    if _tagger is None:
        with _tagger_lock:
            if _tagger is None:
                _tagger = fugashi.Tagger()
    return _tagger


def split_mora(text):
    """Split kana text into mora tiles (youon/sokuon/chouon aware).

    Returns None if `text` contains anything outside the hiragana/
    katakana/chouon range (kanji, alphabet, punctuation) - such words
    aren't eligible for mora-tile splitting.
    """
    if not text:
        return None
    moras = []
    pos = 0
    for m in _MORA_RE.finditer(text):
        if m.start() != pos:
            return None
        moras.append(m.group())
        pos = m.end()
    if pos != len(text):
        return None
    return moras


def has_alphabet(text):
    return bool(text) and bool(_ALPHA_RE.search(text))


def classify_route(jpname):
    """Decide mora vs chunk routing from the word's own hiragana reading.

    Returns a dict: {route: 'mora'|'chunk', mora_count, has_alpha}.
    route is 'chunk' whenever the reading isn't cleanly mora-splittable
    (e.g. contains alphabet) or exceeds MORA_MAX_FOR_TILE_SPLIT.
    """
    alpha = has_alphabet(jpname)
    moras = split_mora(jpname) if not alpha else None
    mora_count = len(moras) if moras else None

    if moras is None or mora_count > MORA_MAX_FOR_TILE_SPLIT:
        route = 'chunk'
    else:
        route = 'mora'
    return {'route': route, 'mora_count': mora_count, 'has_alpha': alpha}


def strip_ruby_tags(html_text):
    """Extract plain kanji-mixed text from an example_sentence_ja value.

    `<ruby>駐輪場<rt>ちゅうりんじょう</rt></ruby>が...` -> `駐輪場が...`
    Keeps the ruby base text (kanji), drops the furigana reading, and
    strips any other remaining markup.
    """
    if not html_text:
        return html_text
    without_ruby = _RUBY_RE.sub(r'\1', html_text)
    return _TAG_RE.sub('', without_ruby).strip()


def split_chunks(text):
    """Split kanji-mixed text into bunsetsu-like meaning chunks.

    Rule: a new chunk starts at each independent word (POS1 in
    _INDEPENDENT_POS1) whose POS2 isn't 非自立可能/非自立 (i.e. not an
    auxiliary-use verb/noun); 接頭辞 (prefixes) attach forward instead of
    backward; consecutive bare nouns with nothing attached yet merge into
    one compound-noun chunk.

    Known limitation: unidic-lite sometimes tags 行く/来る/いる etc. as
    非自立可能 (auxiliary-use) even when they're the sentence's main verb
    (e.g. "現場に行きます" merges into one chunk instead of splitting
    before 行きます), because those verbs are genuinely ambiguous between
    independent and auxiliary use and the lite dictionary's disambiguation
    isn't perfect. Affects a minority of sentences; worth a manual spot
    check pass over the premium phrase set before shipping.
    """
    if not text:
        return []
    tagger = _get_tagger()
    chunks = []
    current = ''
    prev_pos1 = None
    prev_pos2 = None
    prev_was_prefix = False

    for w in tagger(text):
        pos1 = w.feature[0]
        pos2 = w.feature[1] if len(w.feature) > 1 else '*'

        if pos1 == '接頭辞':
            if current:
                chunks.append(current)
            current = w.surface
            prev_pos1, prev_pos2 = pos1, pos2
            prev_was_prefix = True
            continue

        is_independent = pos1 in _INDEPENDENT_POS1 and pos2 not in _DEPENDENT_POS2
        is_noun_continuation = (
            is_independent and pos1 == '名詞' and prev_pos1 == '名詞'
            and prev_pos2 not in _DEPENDENT_POS2
        )

        if is_independent and current and not is_noun_continuation and not prev_was_prefix:
            chunks.append(current)
            current = w.surface
        else:
            current += w.surface

        prev_pos1, prev_pos2 = pos1, pos2
        prev_was_prefix = False

    if current:
        chunks.append(current)
    return chunks


def build_tiles(jpname, example_sentence_ja):
    """Top-level entry point: decide the route and return its tiles.

    Returns {route, tiles, answer_text}. For 'chunk' routing, answer_text
    is the plain (ruby-stripped) example_sentence_ja text since that's
    what actually gets tiled - the game question changes from "arrange
    this word" to "arrange this example sentence" for these words.
    """
    info = classify_route(jpname)
    if info['route'] == 'mora':
        tiles = split_mora(jpname)
        return {'route': 'mora', 'tiles': tiles, 'answer_text': jpname}

    plain = strip_ruby_tags(example_sentence_ja)
    tiles = split_chunks(plain)
    return {'route': 'chunk', 'tiles': tiles, 'answer_text': plain}
