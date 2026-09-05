from app.services.youtube_discovery_metadata import topic_metadata_fallback


def test_ikea_subjects_are_not_displaced_by_generic_title_verb():
    result = topic_metadata_fallback(
        'Why IKEA Put Furniture in Flat Boxes',
        'Flat boxes changed furniture shipping. IKEA customers assemble furniture at home.',
    )
    assert 'IKEA' in result['tags']
    assert 'Furniture' in result['tags']
    assert 'Flat Boxes' in result['tags']
    assert not any('put' in tag.casefold().split() for tag in result['tags'])
    assert 'Put' not in result['hashtags']
    assert len(result['tags']) <= 6 and len(result['hashtags']) <= 3


def test_english_title_verbs_are_not_standalone_discovery_hashtags():
    result = topic_metadata_fallback('How LEGO Makes Bricks', 'LEGO bricks connect.')
    assert 'LEGO' in result['tags']
    assert 'Bricks' in result['tags']
    assert all(tag.casefold() not in {'how', 'makes'} for tag in result['hashtags'])
