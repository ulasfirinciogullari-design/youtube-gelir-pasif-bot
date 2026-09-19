"""Run one deterministic partition of the full pytest collection in CI."""
import argparse
import hashlib
import json

import pytest


def partition(items, index, count):
    if (type(index) is not int or type(count) is not int
            or not 1 <= count <= 16 or not 0 <= index < count):
        raise pytest.UsageError('Invalid CI test partition')
    nodeids = [item.nodeid for item in items]
    if (len(nodeids) < count or len(set(nodeids)) != len(nodeids)
            or any(type(nodeid) is not str or not nodeid for nodeid in nodeids)):
        raise pytest.UsageError('Full test collection must be nonempty and unique')
    selected = items[index::count]
    omitted = [item for position, item in enumerate(items) if position % count != index]

    def digest(values):
        return hashlib.sha256(json.dumps(values, ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest()

    record = {'version': 1, 'index': index, 'count': count,
        'total_items': len(items), 'selected_items': len(selected),
        'collection_sha256': digest(nodeids),
        'selection_sha256': digest([item.nodeid for item in selected])}
    return selected, omitted, record


class CollectionPartition:
    def __init__(self, index, count):
        self.index, self.count = index, count
        self.record = None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        selected, omitted, self.record = partition(items, self.index, self.count)
        items[:] = selected
        config.hook.pytest_deselected(items=omitted)
        reporter = config.pluginmanager.get_plugin('terminalreporter')
        if reporter is None:
            raise pytest.UsageError('CI collection evidence requires a terminal reporter')
        reporter.write_line('CI_TEST_SHARD_V1:' + json.dumps(self.record, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', required=True, type=int)
    parser.add_argument('--count', required=True, type=int)
    args = parser.parse_args()
    plugin = CollectionPartition(args.index, args.count)
    result = pytest.main(['-q', '--durations=15'], plugins=[plugin])
    if result == 0 and plugin.record is None:
        return int(pytest.ExitCode.USAGE_ERROR)
    return int(result)


if __name__ == '__main__':
    raise SystemExit(main())
