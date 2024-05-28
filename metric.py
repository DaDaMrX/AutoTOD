import json
from collections import defaultdict

import click

from utils import DOMAINS


class MetricTracker:
    '''Dialog Metric Tracker
    - Fields: restaurant, hotel, attraction, train, taxi, domain, dialog
    - Metrics: inform, success, book, combine
    '''

    METICS = ['inform', 'success', 'book']

    def __init__(self):
        self.raw = defaultdict(dict)
        self.cost = defaultdict(float)
        self.cost_total = 0.0

        self.domain_scores = {}  # domain -> metric -> score (restaurant -> inform -> score)
        for domain in DOMAINS:
            self.domain_scores[domain] = {}
            for m in self.METICS:
                self.domain_scores[domain][m] = {'score': 0.0, 'hit': 0, 'total': 0}

        self.fuse_domain_scores = {m: {'score': 0.0, 'hit': 0, 'total': 0} for m in self.METICS}
        self.dialog_scores = {m: {'score': 0.0, 'hit': 0, 'total': 0} for m in self.METICS}

        self.combine_scores = {d: {'score': 0.0, 'accum': 0.0, 'total': 0} for d in DOMAINS + ['domain', 'dialog']}

    def add_domain_eval_result(self, dialog_id, domain, eval_result):
        assert domain in DOMAINS
        assert domain not in self.raw[dialog_id]
        if status := eval_result.get('status'):
            assert status == 'succeed'
        self.raw[dialog_id][domain] = eval_result

        # Inform, Success, Book Score
        for k in self.METICS:
            v = eval_result[k]
            if v['complete'] is not None:
                ## each domain
                self.domain_scores[domain][k]['hit'] += v['complete']
                self.domain_scores[domain][k]['total'] += 1

                # domain level
                self.fuse_domain_scores[k]['hit'] += v['complete']
                self.fuse_domain_scores[k]['total'] += 1

        # Combine Score
        combine_score = self.calc_combine_score(eval_result['inform']['complete'],
                                                eval_result['success']['complete'],
                                                eval_result['book']['complete'],)
        ## each domain
        self.combine_scores[domain]['accum'] += combine_score
        self.combine_scores[domain]['total'] += 1
        ## domain level
        self.combine_scores['domain']['accum'] += combine_score
        self.combine_scores['domain']['total'] += 1

    def add_dialog_eval_results(self, dialog_id, eval_results):
        assert dialog_id not in self.raw
    
        # Each Domain & Domain Level
        for domain, eval_result in eval_results.items():
            self.add_domain_eval_result(dialog_id, domain, eval_result)

        # Dialog level
        ## inform, success, book
        scores = {}
        for m in self.METICS:
            complete_list = [result[m]['complete'] for result in eval_results.values() if result[m]['complete'] is not None]
            if len(complete_list) > 0:
                scores[m] = int(all(complete_list))
                self.dialog_scores[m]['hit'] += scores[m]
                self.dialog_scores[m]['total'] += 1
            else:
                scores[m] = None

        ## combine
        combine_score = self.calc_combine_score(scores['inform'], scores['success'], scores['book'])
        self.combine_scores['dialog']['accum'] += combine_score
        self.combine_scores['dialog']['total'] += 1

    @staticmethod
    def calc_combine_score(inform, success, book):
        '''Formula
        score = 0.5 * inform + 0.5 * success_book
        success_book = (success + book) / (success_f + book_f)
        '''
        assert inform is not None

        if success is None and book is None:
            score = 1.0 * inform
        elif success is not None and book is None:
            score = 0.5 * inform + 0.5 * success 
        elif success is None and book is not None:
            score = 0.5 * inform + 0.5 * book
        else:
            score = 0.5 * inform + 0.25 * success + 0.25 * book

        return score

    def add_cost(self, dialog_id, cost):
        self.cost[dialog_id] += cost
        self.cost_total += cost

    def get_cost(self):
        n_dialog = len(self.raw)
        avg = self.cost_total / n_dialog if n_dialog > 0 else 0.0
        return {
            'total': self.cost_total,
            'n_dialog': n_dialog,
            'average': avg,
        }

    def generate_postfix_str(self, fields=['cost', 'inform', 'success', 'book'], prefixes=[]):
        postfix_str = [s for s in prefixes]

        if 'cost' in fields:
            postfix_str.append(f'cost: {self.cost_total:.6f}')
            fields = [f for f in fields if f != 'cost']
    
        for m in fields:
            hit, total = 0, 0
            for d in DOMAINS:
                hit += self.domain_scores[d][m]['hit']
                total += self.domain_scores[d][m]['total']
            s = hit / total if total > 0 else 0.0
            postfix_str.append(f'{m}: {s * 100:.1f} ({hit}/{total})')
        postfix_str = ', '.join(postfix_str)
        return postfix_str

    def generate_detail_table(self, domains=['restaurant', 'hotel', 'attraction', 'train', 'taxi'], 
                              metrics=['inform', 'success', 'book'],
                              invalid_colums=[('attraction', 'book'), ('taxi', 'success')],
                              fix_taxi=True):
        DOMAIN_ABBR = {'restaurant': 'Rest', 'hotel': 'Hotel', 'attraction': 'Attra', 'train': 'Train', 'taxi': 'Taxi'}
        METRIC_ABBR = {'inform': 'I', 'success': 'S', 'book': 'B'}

        scores = {}
        for domain in domains:
            for metric in metrics:
                dd = self.domain_scores[domain][metric]
                score = dd['hit'] / dd['total'] if dd['total'] > 0.0 else 0.0
                scores[(domain, metric)] = score

        if fix_taxi:
            scores['taxi', 'book'], scores['taxi', 'success'] = scores['taxi', 'success'], scores['taxi', 'book']

        if invalid_colums:
            for domain in domains:
                for metric in metrics:
                    if (domain, metric) in invalid_colums:
                        score = '--'
                    else:
                        score = scores[domain, metric]
                        score = f'{score * 100:.1f}'
                    scores[domain, metric] = score

        head, body = [], []
        for domain in domains:
            for metric in metrics:
                head.append(f'{DOMAIN_ABBR[domain]}-{METRIC_ABBR[metric]}')
                body.append(scores[domain, metric])

        table = []
        table.append('| ' + ' | '.join(head) + ' |')
        table.append('| ' + ' | '.join([':---:'] * len(head)) + ' |')
        table.append('| ' + ' | '.join(body) + ' |')
        table = '\n'.join(table)
        return table

    def generate_fuse_table(self, fields=['domain', 'dialog'],
                            metrics=['inform', 'success', 'book', 'combine']):
        FIELDS_MAP = {'domain': 'Domain', 'dialog': 'Dialog'}
        METRIC_ABBR = {'inform': 'I', 'success': 'S', 'book': 'B', 'combine': 'C'}

        head, body = [], []

        basic_metrics = [m for m in metrics if m in self.METICS]
        if 'domain' in fields:
            for m in basic_metrics:
                dd = self.fuse_domain_scores[m]
                dd['score'] = dd['hit'] / dd['total'] if dd['total'] > 0.0 else 0.0

                head.append(f"{FIELDS_MAP['domain']}-{METRIC_ABBR[m]}")
                body.append(f"{dd['score'] * 100:.1f}")

            if 'combine' in metrics:
                dd = self.combine_scores['domain']
                dd['score'] = dd['accum'] / dd['total'] if dd['total'] > 0.0 else 0.0

                head.append(f"{FIELDS_MAP['domain']}-{METRIC_ABBR['combine']}")
                body.append(f"{dd['score'] * 100:.1f}")

        if 'dialog' in fields:
            for m in basic_metrics:
                dd = self.dialog_scores[m]
                dd['score'] = dd['hit'] / dd['total'] if dd['total'] > 0.0 else 0.0

                head.append(f"{FIELDS_MAP['dialog']}-{METRIC_ABBR[m]}")
                body.append(f"{dd['score'] * 100:.1f}")

            if 'combine' in metrics:
                dd = self.combine_scores['dialog']
                dd['score'] = dd['accum'] / dd['total'] if dd['total'] > 0.0 else 0.0

                head.append(f"{FIELDS_MAP['dialog']}-{METRIC_ABBR['combine']}")
                body.append(f"{dd['score'] * 100:.1f}")

        table = []
        table.append('| ' + ' | '.join(head) + ' |')
        table.append('| ' + ' | '.join([':---:'] * len(head)) + ' |')
        table.append('| ' + ' | '.join(body) + ' |')
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

    def generate_summary_tables(self):
        summary = []
        summary.append('## Comprehensive Metrics')
        summary.append(self.generate_fuse_table())
        summary.append('')
        summary.append('## Domain Metrics')
        summary.append(self.generate_detail_table())
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

    summary = metric_tracker.generate_summary_tables()
    with open(score_table_file, 'w') as f:
        f.write(summary + '\n')


if __name__ == '__main__':
    metric()
