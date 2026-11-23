"""Verified input and paired evaluation for the archived four-case experiment."""
import json
from pathlib import Path

from zhihu_agent.ranking_eval import evaluation_case, evaluate_ranking, rating_template, summarize_pairs


class Lab:
    def __init__(self, cohort_file: Path, run_dir: Path):
        entries = json.loads(cohort_file.read_text(encoding='utf-8'))
        self.cases = {}
        self.proxy = {}
        for entry in entries:
            report = json.loads((run_dir / f"{entry['run_id']}.json").read_text(encoding='utf-8'))
            case = evaluation_case(report, entry['focus_override'])
            case_id = case['case_id']
            if case_id != entry['case_id'] or case_id in self.cases:
                raise ValueError('Evaluation inputs do not match the frozen cohort')
            if entry['ai_labels'].get('label_source') != 'ai_proxy':
                raise ValueError('Invalid proxy labels')
            evaluate_ranking(case, entry['ai_labels'])
            self.cases[case_id] = case
            self.proxy[case_id] = entry['ai_labels']

    def form(self, case_id, stored):
        template = rating_template(self.cases[case_id])
        if stored:
            grades = {item['candidate_id']: item['grade'] for item in stored['items']}
            for item in template['items']:
                item['grade'] = grades.get(item['candidate_id'])
        return template

    def compare(self, case_id):
        case = self.cases[case_id]
        return {'candidates': case['candidates'], 'rankings': case['rankings'], 'scores': case['focus_scores'],
                'home_order_source': case['home_order_source']}

    def metrics(self, stored, source):
        labels = self.proxy if source == 'ai_proxy' else stored
        eligible = {cid: annotation for cid, annotation in labels.items() if cid in self.cases
                    and any(item['grade'] is not None for item in annotation['items'])}
        result = {}
        for k in (1, 3):
            rows = [evaluate_ranking(self.cases[cid], annotation, k) for cid, annotation in eligible.items()]
            result[str(k)] = summarize_pairs(rows)
        return {'label_source': source, 'cases': len(self.cases),
                'candidate_count': sum(len(case['candidates']) for case in self.cases.values()),
                'labeled': sum(item['grade'] is not None for annotation in eligible.values() for item in annotation['items']),
                'cutoffs': result}
