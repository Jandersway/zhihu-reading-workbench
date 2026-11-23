"""A small typed provenance graph. CITES is not a factually SUPPORTS relation."""
from __future__ import annotations

from copy import deepcopy
import json

from .evidence import digest


ONTOLOGY = {
    'node_types': ['Recommendation', 'DocumentSnapshot', 'Passage', 'Claim', 'FavoriteExcerpt'],
    'relations': {
        'HAS_PASSAGE': ['DocumentSnapshot', 'Passage'],
        'HAS_CLAIM': ['Recommendation', 'Claim'],
        'CITES': ['Claim', 'Passage'],
        'PREFERENCE_CLUE': ['Recommendation', 'FavoriteExcerpt'],
    },
}


def build_evidence_graph(report: dict, candidate_id: str | None = None) -> dict:
    if report.get('workflow_version', 0) < 3:
        raise ValueError('Evidence graph requires a version 3 report; historical provenance must not be invented')
    recommendations = report['recommendations']
    if candidate_id is not None:
        recommendations = [r for r in recommendations if r['candidate_id'] == candidate_id]
        if not recommendations:
            raise ValueError('Candidate is not a published recommendation in this report')
    nodes, edges = {}, []
    for recommendation in recommendations:
        packet = deepcopy(recommendation.get('evidence'))
        if not isinstance(packet, dict):
            raise ValueError('Recommendation has no evidence packet')
        packet_id = packet.pop('evidence_id', None)
        if packet_id != digest(json.dumps(packet, ensure_ascii=False, sort_keys=True)):
            raise ValueError('Evidence packet content does not match its version hash')
        if packet['candidate_id'] != recommendation['candidate_id']:
            raise ValueError('Evidence candidate does not match recommendation')
        if any(packet['claims'][key] != recommendation.get(key) for key in ('why', 'value', 'drawback', 'preference_reason')):
            raise ValueError('Published claims differ from the reviewed evidence packet')
        anchor = packet['body_evidence']
        rid = 'recommendation:' + packet_id
        did = 'document:' + packet['candidate_id'] + ':' + anchor['source_sha256']
        pid = did + f":passage:{anchor['start']}:{anchor['end']}"
        nodes[rid] = {'id': rid, 'type': 'Recommendation', 'candidate_id': packet['candidate_id'],
                      'run_id': report['run_id']}
        nodes[did] = {'id': did, 'type': 'DocumentSnapshot', 'url': packet['url'], 'author': packet['author'],
                      'sha256': anchor['source_sha256'], 'full_text_persisted': False}
        nodes[pid] = {'id': pid, 'type': 'Passage', **anchor}
        edges.append({'source': did, 'relation': 'HAS_PASSAGE', 'target': pid})
        for field in ('why', 'value', 'drawback'):
            claim_id = rid + ':' + field
            nodes[claim_id] = {'id': claim_id, 'type': 'Claim', 'field': field,
                               'text': packet['claims'][field],
                               'epistemic_status': 'agent_interpretation_of_source_not_verified_fact'}
            edges.extend([{'source': rid, 'relation': 'HAS_CLAIM', 'target': claim_id},
                          {'source': claim_id, 'relation': 'CITES', 'target': pid}])
        for favorite in packet['preference_evidence']:
            fid = 'favorite:' + favorite['answer_id'] + ':' + favorite['source_sha256'] + f":{favorite['start']}:{favorite['end']}"
            nodes[fid] = {'id': fid, 'type': 'FavoriteExcerpt', **favorite}
            edges.append({'source': rid, 'relation': 'PREFERENCE_CLUE', 'target': fid,
                          'rationale': packet['claims']['preference_reason'], 'is_user_verdict': False})
    for edge in edges:
        expected = ONTOLOGY['relations'][edge['relation']]
        actual = [nodes[edge['source']]['type'], nodes[edge['target']]['type']]
        if actual != expected:
            raise ValueError('Graph violates relation endpoint types')
    return {'schema_version': 1, 'run_id': report['run_id'], 'ontology': deepcopy(ONTOLOGY),
            'nodes': list(nodes.values()), 'edges': edges,
            'limitations': 'Typed provenance export, not concept extraction, OWL reasoning, GraphRAG or independently verified factual relations. All claims share the recommendation-level cited passage; CITES does not imply entailment.'}
