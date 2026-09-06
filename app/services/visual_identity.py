import re


_MANUFACTURED_REPLICA_PATTERN = re.compile(
    r"\b(?:lego(?:['’]?(?:nun|ya|yu|da|dan))?|toys?|figurines?|replicas?|"
    r"action\s+figures?|scale\s+models?|model\s+kits?|miniatures?|dolls?|"
    r"oyuncak(?:lar(?:ı|ın|a|da|dan|la)?|ta|tan|la)?|"
    r"oyuncağ(?:ı(?:n(?:ı|a|da|dan)?)?|ıyla|ın|ınla|a)|"
    r"maket(?:i(?:n(?:i|e|de|den)?)?|iyle|in|e|te|ten|le|"
    r"ler(?:i|in|e|de|den|le)?)?|"
    r"figür(?:ü(?:n(?:ü|e|de|den)?)?|üyle|ün|e|de|den|le|"
    r"ler(?:i|in|e|de|den|le)?)?|"
    r"replika(?:sı(?:n(?:ı|a|da|dan)?)?|sıyla|nın|ya|yı|da|dan|yla|"
    r"lar(?:ı|ın|a|da|dan|la)?)?|"
    r"minyatür(?:ü(?:n(?:ü|e|de|den)?)?|üyle|ün|e|de|den|le|"
    r"ler(?:i|in|e|de|den|le)?)?)\b",
    flags=re.IGNORECASE,
)
_STRUCTURE_SUBJECT = (
    r'(?:bridges?|buildings?|houses?|castles?|monuments?|'
    r'köprü(?:nün|ler(?:in)?)?|bina(?:nın|lar(?:ın)?)?|'
    r'kale(?:nin|ler(?:in)?)?)'
)
_STRUCTURE_REPLICA_WORD = (
    r'(?:replicas?|replika(?:sı(?:n(?:ı|a|da|dan)?)?|sıyla|'
    r'nın|ya|yı|da|dan|yla|lar(?:ı|ın|a|da|dan|la)?)?)'
)
_STRUCTURE_REPLICA_PATTERN = re.compile(
    rf'\b(?:{_STRUCTURE_SUBJECT}\s+{_STRUCTURE_REPLICA_WORD}|'
    rf'{_STRUCTURE_REPLICA_WORD}\s+{_STRUCTURE_SUBJECT}|'
    rf'replicas?\s+of\s+(?:(?:a|an|the|this|that)\s+)?{_STRUCTURE_SUBJECT})\b',
    flags=re.IGNORECASE,
)
_AUTHORED_HUMAN_EXCLUSION_PATTERN = re.compile(
    r"\b(?:no|without)\s+(?:humans?|people|persons?|human\s+hands?|hands?)\b|"
    r"\b(?:insansız|insan\s+yok|kişi\s+yok|el\s+yok|"
    r"insan\s+olmasın|kişi\s+olmasın|el\s+olmasın|"
    r"insan\s+görünmesin|kişi\s+görünmesin|el\s+görünmesin)\b",
    flags=re.IGNORECASE,
)


def _scene_authored_text(scene: dict) -> str:
    visual_queries = scene.get('visual_queries') or []
    if isinstance(visual_queries, str):
        visual_queries = [visual_queries]
    return ' '.join([
        str(scene.get('narration') or ''),
        str(scene.get('ai_prompt') or ''),
        *[str(query or '') for query in visual_queries],
    ])


def _match_is_negated(text: str, match: re.Match[str]) -> bool:
    """Reject an identity word only when its own authored clause negates it."""
    clause_start = max(
        text.rfind(mark, 0, match.start())
        for mark in ('\n', '.', ',', ';', ':', '!', '?')
    ) + 1
    following_boundaries = [
        position
        for mark in ('\n', '.', ',', ';', ':', '!', '?')
        if (position := text.find(mark, match.end())) >= 0
    ]
    clause_end = min(following_boundaries) if following_boundaries else len(text)
    clause = text[clause_start:clause_end].casefold()
    relative_start = match.start() - clause_start
    relative_end = match.end() - clause_start
    prefix = clause[:relative_start]
    suffix = clause[relative_end:]

    matched_word = clause[relative_start:relative_end]
    if matched_word.endswith(('sız', 'siz', 'suz', 'süz')):
        return True
    turkish_negation = re.search(
        r"\b(?:değil(?:dir|di|miş|se|ken|ler)?|olmayan|olmasın|"
        r"olmayacak|olmadığı(?:nı|na|nda|ndan)?|olmaması|olmayışı|"
        r"görünmesin|benzemesin)\b",
        suffix,
    )
    if turkish_negation is not None:
        between = suffix[:turkish_negation.start()]
        tokens = re.findall(r"[a-zçğıöşü]+", between)
        contrast_tokens = {'ama', 'fakat', 'gerçek', 'canlı', 'aslında'}
        coordinated_identity = all(
            token in {
                'ya', 'da', 've', 'veya', 'oyuncak', 'oyuncağı', 'figür',
                'figürü', 'replika', 'replikası', 'maket', 'maketi',
                'minyatür', 'minyatürü', 'model', 'lego',
            }
            for token in tokens
        )
        if (
            len(tokens) <= 1
            or (len(tokens) <= 4 and coordinated_identity)
        ) and not contrast_tokens.intersection(tokens):
            return True

    english_negations = list(re.finditer(r"\b(?:not|no|without)\b", prefix))
    if not english_negations:
        return False
    english_negation = english_negations[-1]
    between = prefix[english_negation.end():]
    tokens = re.findall(r"[a-z]+", between)
    allowed_bridge_tokens = {
        'a', 'an', 'the', 'any', 'toy', 'toys', 'figurine', 'figurines',
        'replica', 'replicas', 'action', 'figure', 'figures', 'scale',
        'model', 'models', 'kit', 'kits', 'miniature', 'miniatures',
        'doll', 'dolls', 'or', 'and', 'plastic', 'small', 'tiny', 'large',
        'realistic', 'stylized', 'soft', 'plush', 'molded', 'wooden',
        'rubber', 'metal', 'resin', 'painted', 'manufactured', 'really',
        'actually', 'truly', 'merely', 'meant', 'intended', 'to', 'be',
    }
    return len(tokens) <= 6 and all(
        token in allowed_bridge_tokens for token in tokens
    )


def manufactured_replica_required(scene: dict) -> bool:
    """Recognize only explicitly authored toy, model or replica identities."""
    authored_text = _scene_authored_text(scene)
    # An architectural replica may be a functioning full-sized structure,
    # not a toy. Exclude only that replica noun phrase, never the whole scene:
    # an explicit miniature/Lego/scale-model identity must still be honored.
    # This is not evidence that a depicted building, banknote or clip is real.
    structure_replicas = list(_STRUCTURE_REPLICA_PATTERN.finditer(authored_text))
    return any(
        not _match_is_negated(authored_text, match)
        and not any(
            structure.start() <= match.start() and match.end() <= structure.end()
            for structure in structure_replicas
        )
        for match in _MANUFACTURED_REPLICA_PATTERN.finditer(authored_text)
    )


def manufactured_replica_guardrail(scene: dict) -> str:
    """Return a protected prompt clause for explicit manufactured replicas."""
    authored_text = _scene_authored_text(scene)
    if not manufactured_replica_required(scene):
        return ''
    guardrail = (
        'MANUFACTURED IDENTITY: visibly inanimate authored toy/model/doll/'
        'figurine/miniature/replica at authored real-world scale and material. '
        'Require 2+ clear manufactured cues suited to material: seams, part '
        'edges, studs, simplified sculpting, woven/plush construction, paint '
        'or molded surface. Never a live or dead biological original or '
        'photoreal organic tissue. Preserve authored face/limbs, material, '
        'condition and setting.'
    )
    if _AUTHORED_HUMAN_EXCLUSION_PATTERN.search(authored_text):
        guardrail += ' Authored exclusion: no person, human or hand.'
    return guardrail
