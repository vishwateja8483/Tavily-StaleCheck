"""Generate the local API's tooling contract from explicit, reviewable schemas."""
import json
from pathlib import Path


def ref(name):
    return {'$ref': '#/components/schemas/' + name}


def obj(properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


string = {'type': 'string'}
strings = {'type': 'array', 'items': string}
schemas = {
    'Input': obj({'answer_text': {'type': 'string', 'minLength': 1},
                  'verification_date': {'type': 'string', 'format': 'date'},
                  'answer_date': {'type': ['string', 'null'], 'format': 'date'},
                  'primary_domains': strings, 'max_claims': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                  'max_attempts': {'type': 'integer', 'minimum': 1, 'maximum': 25},
                  'max_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 180}, 'consent': {'const': True}}),
    'Approval': obj({'queries': strings, 'primary_domains': strings, 'sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}),
    'Error': obj({'error': string}),
}
schemas['Job'] = obj({'id': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}, 'mode': {'enum': ['demo', 'live']},
                      'state': {'enum': ['extracting', 'awaiting_approval', 'researching', 'cancel_requested', 'completed', 'partial', 'declined', 'expired', 'cancelled', 'failed']},
                      'remaining_seconds': {'type': 'number', 'minimum': 0}, 'approval': {'anyOf': [ref('Approval'), {'type': 'null'}]},
                      'stop_reason': {'type': ['string', 'null']}, 'attempts': {'type': 'integer', 'minimum': 0, 'maximum': 25},
                      'artifacts': {'type': 'array', 'uniqueItems': True, 'items': {'enum': ['report.html', 'report.json', 'input.json', 'manifest.json']}}})
schemas['Capabilities'] = obj({'mode': {'enum': ['demo', 'live']}, 'configured': {'type': 'boolean'},
    'configuration': obj({k: {'type': 'boolean'} for k in ('BIFROST_URL', 'BIFROST_MODEL', 'BIFROST_VIRTUAL_KEY', 'TAVILY_API_KEY', 'BIFROST_SEARCH_RELAY')}),
    'max_sessions': {'type': 'integer', 'minimum': 1, 'maximum': 128}, 'demo_input': {'anyOf': [ref('Input'), {'type': 'null'}]}})


def operation(summary, response, body=None):
    result = {'summary': summary, 'responses': {'200': {'description': 'Successful response', 'content': {'application/json': {'schema': response}}},
        'default': {'description': 'Rejected safely; no automatic retry. See ASSESSMENT-API.md for status semantics.', 'content': {'application/json': {'schema': ref('Error')}}}}}
    if body is not None:
        result['parameters'] = [{'name': 'Origin', 'in': 'header', 'required': True, 'schema': string, 'description': 'Exact bound http://127.0.0.1:port origin'}]
        result['requestBody'] = {'required': True, 'content': {'application/json': {'schema': body}}}
    return result


paths = {
    '/api/capabilities': {'get': operation('Configuration presence, not connectivity', ref('Capabilities'))},
    '/api/jobs': {'post': operation('Create once per immutable idempotency key; 200 identical retry, 201 new job', ref('Job'),
                                   obj({'idempotency_key': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{16,100}$'}, 'input': ref('Input')}))},
    '/api/jobs/{id}': {'get': operation('Poll without repeating extraction', ref('Job'))},
    '/api/jobs/{id}/approval': {'post': operation('Decide exact complete approval object once', ref('Job'), obj({'request': ref('Approval'), 'approved': {'type': 'boolean'}}))},
    '/api/jobs/{id}/cancel': {'post': operation('Request cooperative cancellation; in-flight calls may be billed', ref('Job'), obj({}))},
    '/api/jobs/{id}/report': {'get': operation('Verified frozen v4 report (domain contract is V4-CONTRACT.md)', {'type': 'object'})},
    '/api/jobs/{id}/artifacts/{name}': {'get': {'summary': 'Fetch allowlisted verified artifact with launch-token header', 'responses': {
        '200': {'description': 'Verified artifact bytes; HTML is rendered from verified v4 report', 'content': {'application/json': {}, 'text/html': {}}},
        'default': {'description': 'Not available or rejected', 'content': {'application/json': {'schema': ref('Error')}}}}}},
}
paths['/api/jobs']['post']['responses']['201'] = paths['/api/jobs']['post']['responses']['200']
for path, item in paths.items():
    if '{id}' in path:
        item['parameters'] = [{'name': 'id', 'in': 'path', 'required': True, 'schema': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}}]
    if '{name}' in path:
        item['parameters'].append({'name': 'name', 'in': 'path', 'required': True, 'schema': {'enum': ['report.html', 'report.json', 'input.json', 'manifest.json']}})
spec = {'openapi': '3.1.0', 'info': {'title': 'StaleCheck local assessment', 'version': '1.0.0',
    'description': 'Loopback single-user service; no CORS. Per-launch token handed off in URL fragment, never HTTP URLs. All reads/writes/downloads authenticated. Demo is explicit, not live evidence.'},
    'servers': [{'url': 'http://127.0.0.1:{port}', 'variables': {'port': {'default': '4190'}}}],
    'security': [{'LaunchToken': []}], 'paths': paths,
    'components': {'securitySchemes': {'LaunchToken': {'type': 'apiKey', 'in': 'header', 'name': 'X-Assessment-Token'}}, 'schemas': schemas}}
if __name__ == '__main__':
    import sys
    with Path(sys.argv[1]).open('x', encoding='utf-8') as stream:
        json.dump(spec, stream, indent=2)
        stream.write('\n')
