from collections import defaultdict
import json

import click

from sgd.utils import load_schemas


class MetricTracker:

    def __init__(self):
        self.raw_dialog_results = {}

        self.schemas = load_schemas()

        # Intent Level
        self.intent_sep_scores = {}
        for service_name, service in self.schemas.items():
            self.intent_sep_scores[service_name] = {}
            for intent in service['intents']:
                intent_name = intent['name']
                self.intent_sep_scores[service_name][intent_name] = {
                    'inform': {'score': 0.0, 'hit': 0, 'total': 0},
                    'success': {'score': 0.0, 'hit': 0, 'total': 0},                    
                }
        self.intent_fuse_scores = {
            'inform': {'score': 0.0, 'hit': 0, 'total': 0},
            'success': {'score': 0.0, 'hit': 0, 'total': 0},
        }

        # Service Level
        self.service_sep_scores = {}
        for service_name, service in self.schemas.items():
            self.service_sep_scores[service_name] = {
                'inform': {'score': 0.0, 'hit': 0, 'total': 0},
                'success': {'score': 0.0, 'hit': 0, 'total': 0},                    
            }
        self.service_fuse_scores = {
            'inform': {'score': 0.0, 'hit': 0, 'total': 0},
            'success': {'score': 0.0, 'hit': 0, 'total': 0},
        }

        # Dialog Level
        self.dialog_fuse_scores = {
            'inform': {'score': 0.0, 'hit': 0, 'total': 0},
            'success': {'score': 0.0, 'hit': 0, 'total': 0},
        }

        self.cost_dict = defaultdict(float)
        self.cost_total = 0.0

    def accum_intent_eval_result(self, service_name, intent_name, inform, success):
        # Separate Intents
        score_dict = self.intent_sep_scores[service_name][intent_name]
        score_dict['inform']['total'] += 1
        score_dict['inform']['hit'] += inform
        if success is not None:
            score_dict['success']['total'] += 1
            score_dict['success']['hit'] += success

        # Fuse Intent
        self.intent_fuse_scores['inform']['total'] += 1
        self.intent_fuse_scores['inform']['hit'] += inform
        if success is not None:
            self.intent_fuse_scores['success']['total'] += 1
            self.intent_fuse_scores['success']['hit'] += success

    def accum_service_eval_results(self, service_name, service_results):
        inform = all(intent['inform'] for intent in service_results.values())
        success_list = [intent['success'] for intent in service_results.values() if intent['success'] is not None]
        success = all(success_list) if success_list else None

        # Separate Service
        score_dict = self.service_sep_scores[service_name]
        score_dict['inform']['total'] += 1
        score_dict['inform']['hit'] += inform
        if success is not None:
            score_dict['success']['total'] += 1
            score_dict['success']['hit'] += success

        # Fuse Service
        self.service_fuse_scores['inform']['total'] += 1
        self.service_fuse_scores['inform']['hit'] += inform
        if success is not None:
            self.service_fuse_scores['success']['total'] += 1
            self.service_fuse_scores['success']['hit'] += success

        return inform, success
    
    def accum_dialog_eval_results(self, service_dict):
        inform = all(serivce['inform'] for serivce in service_dict.values())
        success_list = [serivce['success'] for serivce in service_dict.values() if serivce['success'] is not None]
        success = all(success_list) if success_list else None

        # Fuse Dialog
        self.dialog_fuse_scores['inform']['total'] += 1
        self.dialog_fuse_scores['inform']['hit'] += inform
        if success is not None:
            self.dialog_fuse_scores['success']['total'] += 1
            self.dialog_fuse_scores['success']['hit'] += success
        
    def add_dialog_eval_results(self, dialog_id, eval_results):
        assert dialog_id not in self.raw_dialog_results, f'{dialog_id = }, {self.raw_dialog_results.keys() = }'
        self.raw_dialog_results[dialog_id] = eval_results

        # Intent Level
        for service_name, service_results in eval_results.items():
            for intent_name, intent_results in service_results.items():
                self.accum_intent_eval_result(service_name, intent_name, intent_results['inform'], intent_results['success'])

        # Service Level
        service_dict = {}
        for service_name, service_results in eval_results.items():
            inform, success = self.accum_service_eval_results(service_name, service_results)
            service_dict[service_name] = {'inform': inform, 'success': success}
    
        # Dialog Level
        self.accum_dialog_eval_results(service_dict)

    def add_cost(self, dialog_id, cost):
        self.cost_dict[dialog_id] += cost
        self.cost_total += cost

    def get_cost(self):
        n_dialog = len(self.cost_dict)
        avg = self.cost_total / n_dialog if n_dialog > 0 else 0.0
        return {
            'total': self.cost_total,
            'n_dialog': n_dialog,
            'average': avg,
        }

    def generate_postfix_str(self, fields=['cost', 'inform', 'success'], prefixes=[]):
        postfix_str = [s for s in prefixes]

        if 'cost' in fields:
            postfix_str.append(f'cost: {self.cost_total:.6f}')
            fields = [f for f in fields if f != 'cost']

        for m in fields:
            hit = self.intent_fuse_scores[m]['hit']
            total = self.intent_fuse_scores[m]['total']
            score = hit / total if total > 0.0 else 0.0
            self.intent_fuse_scores[m]['score'] = score
            postfix_str.append(f'{m}: {score * 100:.1f} ({hit}/{total})')

        postfix_str = ', '.join(postfix_str)
        return postfix_str
    
    def generate_fuse_table(self):
        # Intent-I, Intent-S, Service-I, Service-S, Dialog-I, Dialog-S
        METRIC_ABBR = {'inform': 'I', 'success': 'S', 'book': 'B', 'combine': 'C'}

        head, body = [], []

        # Intent
        for m in ['inform', 'success']:
            hit = self.intent_fuse_scores[m]['hit']
            total = self.intent_fuse_scores[m]['total']
            score = hit / total if total > 0.0 else 0.0
            self.intent_fuse_scores[m]['total'] = score

            head.append(f'Intent-{METRIC_ABBR[m]}')
            body.append(f'{score * 100:.1f}')

        # Service
        for m in ['inform', 'success']:
            hit = self.service_fuse_scores[m]['hit']
            total = self.service_fuse_scores[m]['total']
            score = hit / total if total > 0.0 else 0.0
            self.service_fuse_scores[m]['score'] = score

            head.append(f'Service-{METRIC_ABBR[m]}')
            body.append(f'{score * 100:.1f}')

        # Dialog
        for m in ['inform', 'success']:
            hit = self.dialog_fuse_scores[m]['hit']
            total = self.dialog_fuse_scores[m]['total']
            score = hit / total if total > 0.0 else 0.0
            self.dialog_fuse_scores[m]['score'] = score

            head.append(f'Dialog-{METRIC_ABBR[m]}')
            body.append(f'{score * 100:.1f}')

        table = []
        table.append('| ' + ' | '.join(head) + ' |')
        table.append('| ' + ' | '.join([':---:'] * len(head)) + ' |')
        table.append('| ' + ' | '.join(body) + ' |')
        table = '\n'.join(table)
        return table
    
    def generate_service_table(self):
        #         Service1, Servce2
        # Inform     x         y
        # Success    z         w
        head, body_count, body_inform, body_success = [''], ['Count'], ['Inform'], ['Success']

        for service_name, service_scores in self.service_sep_scores.items():
            head.append(service_name)
            body_count.append(str(service_scores['inform']['total']))
            for m, body in [('inform', body_inform), ('success', body_success)]:
                hit = service_scores[m]['hit']
                total = service_scores[m]['total']
                score = hit / total if total > 0.0 else 0.0
                service_scores[m]['score'] = score

                body.append(f'{score * 100:.1f}')

        table = []
        table.append('| ' + ' | '.join(head) + ' |')
        table.append('| ' + ' | '.join([':---:'] * len(head)) + ' |')
        table.append('| ' + ' | '.join(body_count) + ' |')
        table.append('| ' + ' | '.join(body_inform) + ' |')
        table.append('| ' + ' | '.join(body_success) + ' |')
        table = '\n'.join(table)
        return table
    
    def generate_cost_table(self):
        # #dialogs Total($) Average($)
        #    n        x        y
        d = self.get_cost()
        total, n_dialog, average = d['total'], d['n_dialog'], d['average']

        head = ['#dialogs', 'Total ($)', 'Average ($)']
        body = [str(n_dialog), f'{total:.4f}', f'{average:.4f}']

        table = []
        table.append('| ' + ' | '.join(head) + ' |')
        table.append('| ' + ' | '.join([':---:'] * len(head)) + ' |')
        table.append('| ' + ' | '.join(body) + ' |')
        table = '\n'.join(table)
        return table

    def generate_all_tables(self):
        summary = []
        summary.append('## Comprehensive Metrics')
        summary.append(self.generate_fuse_table())
        summary.append('')
        summary.append('## Service Metrics')
        summary.append(self.generate_service_table())
        summary.append('')
        summary.append('## Cost')
        summary.append(self.generate_cost_table())
        summary = '\n'.join(summary)
        return summary


@click.command()
@click.option('--log_file')
@click.option('--score_table_file')
def metric(log_file, score_table_file):
    with open(log_file) as f:
        data = [json.loads(s) for s in f.read().splitlines()]

    metric_tracker = MetricTracker()
    for item in data[1:]:
        if item['status'] != 'succeed':
            print(f'Skip dialog: {item["dialog_id"]}, status: {item["status"]}')
            continue
        metric_tracker.add_dialog_eval_results(item['dialog_id'], item['eval_results'])
        metric_tracker.add_cost(item['dialog_id'], item['cost'])

    summary = metric_tracker.generate_all_tables()

    with open(score_table_file, 'w') as f:
        f.write(summary + '\n')


if __name__ == '__main__':
    metric()
