"""Accept a writer's redundant exact array indices, never reorder its scenes."""
from copy import deepcopy


def schema_with_indices(schema):
    schema = deepcopy(schema)
    scenes = schema['properties']['scenes']
    scenes['items']['properties']['index'] = {
        'type': 'integer', 'minimum': 0, 'maximum': scenes['maxItems'] - 1}
    return schema


def decode(value):
    from app.services.production_failures import ProductionContentError

    result = deepcopy(value)
    for position, row in enumerate(result['scenes']):
        if 'index' in row:
            if type(row['index']) is not int or row['index'] != position:
                raise ProductionContentError('Director scene index disagrees with its observed array position')
            row.pop('index')
    return result
