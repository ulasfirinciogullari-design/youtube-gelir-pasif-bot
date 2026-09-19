"""A successful CI matrix must execute the complete collection exactly once."""
from types import SimpleNamespace

import pytest

from scripts.run_ci_tests import CollectionPartition, partition


@pytest.mark.parametrize('count', [1, 2, 4, 7, 16])
def test_full_collection_is_disjoint_complete_and_ordered(count):
    items = [SimpleNamespace(nodeid=f'tests/test_{n // 11}.py::test_case[{n}]')
             for n in range(127)]
    seen, digests, sizes = [], set(), []
    for index in range(count):
        selected, omitted, record = partition(items, index, count)
        assert {id(item) for item in selected}.isdisjoint(id(item) for item in omitted)
        assert {id(item) for item in selected + omitted} == {id(item) for item in items}
        assert selected == sorted(selected, key=items.index)
        assert record['selected_items'] == len(selected)
        assert record['total_items'] == len(items)
        seen.extend(id(item) for item in selected)
        digests.add(record['collection_sha256'])
        sizes.append(len(selected))
    assert sorted(seen) == sorted(id(item) for item in items)
    assert len(digests) == 1 and max(sizes) - min(sizes) <= 1


@pytest.mark.parametrize('index,count', [(-1, 4), (4, 4), (0, 0), (0, 17),
                                        (True, 4), (0, True), ('0', 4), (0, 2.0)])
def test_invalid_partition_cannot_claim_coverage(index, count):
    with pytest.raises(pytest.UsageError):
        partition([SimpleNamespace(nodeid='one')], index, count)


@pytest.mark.parametrize('nodeids,count', [([], 1), (['same', 'same'], 1),
                                           (['only'], 2), ([''], 1), ([None], 1)])
def test_empty_duplicate_or_incomplete_collection_is_rejected(nodeids, count):
    with pytest.raises(pytest.UsageError):
        partition([SimpleNamespace(nodeid=nodeid) for nodeid in nodeids], 0, count)


def test_added_or_reordered_collection_changes_the_evidence():
    items = [SimpleNamespace(nodeid=name) for name in ('first', 'second', 'third', 'fourth')]
    original = partition(items, 0, 2)[2]
    assert original == partition(items, 0, 2)[2]
    for changed in (items[::-1], items + [SimpleNamespace(nodeid='added')]):
        assert partition(changed, 0, 2)[2]['collection_sha256'] != original['collection_sha256']


def test_hook_reports_actual_selection_and_deselection():
    items = [SimpleNamespace(nodeid=str(n)) for n in range(12)]
    original, omitted, lines = list(items), [], []
    config = SimpleNamespace(
        hook=SimpleNamespace(pytest_deselected=lambda *, items: omitted.extend(items)),
        pluginmanager=SimpleNamespace(get_plugin=lambda name:
            SimpleNamespace(write_line=lines.append) if name == 'terminalreporter' else None))
    plugin = CollectionPartition(2, 4)
    plugin.pytest_collection_modifyitems(None, config, items)
    assert len(items) == 3 and len(omitted) == 9 and len(original) == 12
    assert len(lines) == 1 and lines[0].startswith('CI_TEST_SHARD_V1:')
    assert plugin.record['selected_items'] == len(items)
