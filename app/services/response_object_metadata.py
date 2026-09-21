"""Discard an exact echoed object-type annotation, never actual response data.

Some routed models copy a schema node's type into the result. Only an unowned
``type: object`` field at an authored object-schema node is redundant. Required
fields, other extra fields and every verdict/value remain subject to the full
original schema. Raw provider bytes are retained by the ordinary observer.
"""
from copy import deepcopy


def decode(value, schema):
    result = deepcopy(value)

    def visit(node, contract):
        if type(contract) is not dict:
            return
        if type(node) is dict and contract.get('type') == 'object':
            properties = contract.get('properties', {})
            if 'type' not in properties and node.get('type') == 'object':
                node.pop('type')
            for key, item in node.items():
                if key in properties:
                    visit(item, properties[key])
        elif type(node) is list and contract.get('type') == 'array':
            for item in node:
                visit(item, contract.get('items'))

    visit(result, schema)
    return result
