"""Exact joined Turkish cardinal spelling; never fuzzy pronunciation matching."""
from functools import lru_cache


@lru_cache(maxsize=512)
def _decompose(token, dictionary):
    if not 2 <= len(token) <= 80:
        return None
    # Keep at most two decompositions per offset; ambiguity is a rejection.
    paths = {len(token): [()]}
    for offset in range(len(token) - 1, -1, -1):
        found = []
        for word in dictionary:
            if not token.startswith(word, offset):
                continue
            for tail in paths.get(offset + len(word), []):
                if len(tail) < 16:
                    found.append((word, *tail))
                    if len(found) == 2:
                        break
            if len(found) == 2:
                break
        paths[offset] = found
    found = paths[0]
    return found[0] if len(found) == 1 and len(found[0]) >= 2 else None


def compact_number_unit(tokens, start, value, matches):
    """Parse a complete number run while retaining original token boundaries.

    Both ``binbeşyüz`` and ``iki binbeşyüz`` use the existing exact cardinal
    grammar. Names, digits inside identifiers, punctuation and signed/decimal
    fragments are never guessed or stripped. Unchanged spaced numbers keep
    their existing comparison path.
    """
    from app.services import audio_qc as qc
    dictionary = tuple(sorted(qc._NUMBER_WORDS - {'virgül'}))
    words, compact, suffix, end = [], False, '', start
    for index in range(start, min(len(tokens), start + 10)):
        if index > start and not value[matches[index - 1].end():matches[index].start()].isspace():
            break
        token = qc._orthographic_fold(tokens[index])
        split = qc._split_number_word(token)
        if split is not None:
            word, suffix = split
            if word == 'virgül':
                return None
            words.append(word)
        else:
            parts = _decompose(token, dictionary)
            if parts is None:
                break
            words.extend(parts)
            compact = True
        end = index + 1
        if suffix:
            break
    if not compact:
        return None
    left, right = matches[start].start(), matches[end - 1].end()
    if ((left and (value[left - 1].isalnum() or value[left - 1] in '_+-−'))
            or (right < len(value) and (value[right].isalnum() or value[right] == '_'))):
        return None
    if end < len(tokens) and (qc._split_number_word(tokens[end]) is not None
            or _decompose(qc._orthographic_fold(tokens[end]), dictionary) is not None):
        return None
    number = qc._parse_integer_words(words)
    if number is None or not 0 <= number <= 10 ** 15:
        return None
    return qc._numeric_key(str(number), suffix), end - start
