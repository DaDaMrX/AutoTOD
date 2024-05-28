import json
import os
import random

import click
import tenacity
from termcolor import colored, cprint
from tqdm import tqdm

import engine
from evaluate import evaluate_by_domain
from metric import MetricTracker
from utils import DATA_PATH, DOMAINS, json_default_func, load_data


def run_and_evaluate(dialog, dialog_id, agent_type, agent_model, user_model):
    result = {
        'dialog_id': dialog_id,
        'status': None,
        'eval_summary': None,
        'cost': 0.0,
        'eval_results': None,
        'run_result': None,
    }

    # Step 1. Run dialog
    def before_sleep_func(retry_state):
        e = retry_state.outcome.exception()
        msg = f'Tenacity: Retrying {retry_state.fn} as it raise {e.__class__.__name__}: '
        msg = colored(msg, 'red') + str(e)
        print(msg)

    retrying = tenacity.Retrying(stop=tenacity.stop_after_attempt(2), before_sleep=before_sleep_func, reraise=True)
    try:
        run_result = retrying(engine.run, dialog=dialog, agent_type=agent_type, agent_model=agent_model, user_model=user_model)
    except Exception as e:
        msg = f'Run dialog failed as {e.__class__.__name__}: '
        print(colored(msg, 'red') + str(e))
        result['run_result'] = {'exception': msg + str(e)}
        result['status'] = 'failed on run dialog'
        return False, result
    else:
        result['run_result'] = run_result
        result['cost'] += run_result['cost']

    # Step 2. Evaluate
    eval_results = {}
    fail_domains = []
    for domain in DOMAINS:
        if not dialog['goal'].get(domain):
            continue
        try:
            eval_result = evaluate_by_domain(domain, run_result)
        except Exception as e:
            # raise e
            fail_domains.append(domain)
            msg = f'Run dialog failed as {e.__class__.__name__}: '
            print(colored(msg, 'red') + str(e))
            eval_results[domain] = {'exception': msg + str(e)}
        else:
            eval_results[domain] = eval_result
            result['cost'] += eval_result['cost']
    if eval_results == {}:
        cprint(f'No domain found for evaluation of dialog {dialog_id}.', 'red')
    result['eval_results'] = eval_results

    # Step 3. Status
    if fail_domains:
        result['status'] = 'failed on eval dialog of domain: ' + ', '.join(fail_domains)
    else:
        result['status'] = 'succeed'
    
    # Step 4. Summary
    result['eval_summary'] = {}
    for domain, eval_result in result['eval_results'].items():
        if 'exception' in eval_result:
            summary = {'status': 'exception'}
        else:
            summary = {}
            for metric in ['inform', 'success', 'book']:
                if (s := eval_result[metric]['complete']) is not None:
                    summary[metric] = s
        result['eval_summary'][domain] = summary

    succeed = result['status'] == 'succeed'
    return succeed, result


@click.group()
def batch_run():
    pass


@batch_run.command()
@click.option('--log_file')
@click.option('--score_table_file')
@click.option('--max_dialog', type=int, default=100)
@click.option('--data_path', default=DATA_PATH)
@click.option('--agent_type', default='func')
@click.option('--agent_model', default='gpt-3.5-turbo-0613')
@click.option('--user_model', default='gpt-3.5-turbo-0613')
def new(log_file, score_table_file, max_dialog, data_path, agent_type, agent_model, user_model):
    # Step 0. Check
    if os.path.exists(log_file):
        raise RuntimeError(f'mode = new and {log_file = } exists.')
    if os.path.exists(score_table_file):
        raise RuntimeError(f'mode = new and {score_table_file = } exists.')

    # Step 1. Sample Dialogs  # TODO: more elaborate samplings
    data = load_data(data_path)
    dialog_ids = list(data.keys())
    random.shuffle(dialog_ids)
    dialog_ids = dialog_ids[:max_dialog]
    data = [(idx, data[idx]) for idx in dialog_ids]

    first_line = {'max_dialog': max_dialog, 'dialog_ids': dialog_ids,
                  'agent_type': agent_type, 'agent_model': agent_model, 'user_model': user_model}
    with open(log_file, 'w') as f:
        f.write(json.dumps(first_line) + '\n')

    print(f'Sampled {max_dialog} dialogs from "{data_path}".')

    # Step 2. Batch Run
    metric_tracker = MetricTracker()
    n_succeed = 0
    pbar = tqdm(data)
    for idx, (dialog_id, dialog) in enumerate(pbar, start=1):
        pbar.set_description(f'Processing {dialog_id}')

        # TODO: print goal?
        try:
            succeed, result = run_and_evaluate(dialog, dialog_id, agent_type, agent_model, user_model)
        except Exception as e:
            # raise e
            msg = f'run_and_evaluate failed as {e.__class__.__name__}: '
            print(colored(msg, 'red') + str(e))
            result = {'dialog_id': dialog_id, 'status': 'run_and_evaluate', 'exception': msg + str(e)}
            succeed = False
        else:  # TODO: how about: if succeed:
            metric_tracker.add_dialog_eval_results(dialog_id, result['eval_results'])
            metric_tracker.add_cost(dialog_id, result['cost'])

        with open(log_file, 'a') as f:
            f.write(json.dumps(result, default=json_default_func) + '\n')

        n_succeed += succeed
        succeed_rate = n_succeed / idx
        succeed_str = f'succeed: {succeed_rate:.0%} ({n_succeed}/{idx})'

        postfix_str = metric_tracker.generate_postfix_str(prefixes=[succeed_str])
        pbar.set_postfix_str(postfix_str, refresh=False)

    # Step 3. Summary
    summary = metric_tracker.generate_summary_tables()
    with open(score_table_file, 'w') as f:
        f.write(summary + '\n')


@batch_run.command()
@click.option('--log_file')
@click.option('--score_table_file')
@click.option('--data_path', default=DATA_PATH)
def recover(log_file, score_table_file, data_path):
    # Step 0. Check
    if not os.path.exists(log_file):
        raise RuntimeError(f'mode = recover and {log_file = } does not exist.')
    if os.path.exists(score_table_file):
        raise RuntimeError(f'mode = recover and {score_table_file = } exists.')
    
    # Step 1. Load
    all_data = load_data(data_path)
    
    with open(log_file) as f:
        data = [json.loads(s) for s in f.read().splitlines()]
    n_target_dialogs = len(data[0]['dialog_ids'])
    n_finish_dialogs = len(data) - 1
    n_left_dialogs = n_target_dialogs - n_finish_dialogs
    print(f'Recover: Target: {n_target_dialogs}, Finish: {n_finish_dialogs}, Left: {n_left_dialogs}')
    agent_type, agent_model, user_model = data[0]['agent_type'], data[0]['agent_model'], data[0]['user_model']
    print(f'Run parameters: {agent_type = }, {agent_model = }, {user_model = }')

    # Step 2. Check dialog ids
    dialog_ids = data[0]['dialog_ids']
    for dialog_id, item in zip(dialog_ids, data[1:]):
        assert dialog_id == item['dialog_id']

    # Step 3. Batch Run
    metric_tracker = MetricTracker()
    n_succeed = 0

    # finished dialogs
    for result in data[1:]:
        n_succeed += result['status'] == 'succeed'
        metric_tracker.add_dialog_eval_results(result['dialog_id'], result['eval_results'])
        metric_tracker.add_cost(result['dialog_id'], result['cost'])

    # new dialogs
    dialog_ids = data[0]['dialog_ids'][n_finish_dialogs:]
    pbar = tqdm(dialog_ids)
    for idx, dialog_id in enumerate(pbar, start=n_finish_dialogs + 1):
        pbar.set_description(f'Processing {dialog_id}')
        dialog = all_data[dialog_id]

        try:
            succeed, result = run_and_evaluate(dialog, dialog_id, agent_type, agent_model, user_model)
        except Exception as e:
            msg = f'run_and_evaluate failed as {e.__class__.__name__}: '
            print(colored(msg, 'red') + str(e))
            result = {'dialog_id': dialog_id, 'status': 'run_and_evaluate', 'exception': msg + str(e)}
            succeed = False
        else:  # TODO: how about: if succeed:
            metric_tracker.add_dialog_eval_results(dialog_id, result['eval_results'])
            metric_tracker.add_cost(dialog_id, result['cost'])

        with open(log_file, 'a') as f:
            f.write(json.dumps(result, default=json_default_func) + '\n')

        n_succeed += succeed
        succeed_rate = n_succeed / idx
        succeed_str = f'succeed: {succeed_rate:.0%} ({n_succeed}/{idx})'

        postfix_str = metric_tracker.generate_postfix_str(prefixes=[succeed_str])
        pbar.set_postfix_str(postfix_str, refresh=False)

    # Step 3. Summary
    summary = metric_tracker.generate_summary_tables()
    with open(score_table_file, 'w') as f:
        f.write(summary + '\n')


@batch_run.command()
@click.option('--log_file', default='logs.jsonl')
@click.option('--updated_log_file', default='logs_updated.jsonl')
@click.option('--updated_score_table_file', default='logs_updated_table.md')
@click.option('--data_path', default=DATA_PATH)
def update(log_file, updated_log_file, updated_score_table_file, data_path):
    # Step 0. Check
    if not os.path.exists(log_file):
        raise RuntimeError(f'mode = update and {log_file = } does not exist.')
    if os.path.exists(updated_log_file):
        raise RuntimeError(f'mode = update and {updated_log_file = } exists.')
    if os.path.exists(updated_score_table_file):
        raise RuntimeError(f'mode = update and {updated_score_table_file = } exists.')
    
    # Step 1. Load
    all_data = load_data(data_path)
    
    with open(log_file) as f:
        data = [json.loads(s) for s in f.read().splitlines()]
    n_dialog = len(data) - 1
    print(f'Loaded {n_dialog} dialogus from "{log_file}".')
    agent_type, agent_model, user_model = data[0]['agent_type'], data[0]['agent_model'], data[0]['user_model']
    print(f'Run parameters: {agent_type = }, {agent_model = }, {user_model = }')

    # Step 2. Check dialog ids
    dialog_ids = data[0]['dialog_ids']
    assert len(dialog_ids) == len(data) - 1
    for dialog_id, item in zip(dialog_ids, data[1:]):
        assert dialog_id == item['dialog_id']

    # Step 3. Scan failed dialogs
    data_fails = []
    for i, item in enumerate(data[1:], start=1):
        if item['status'] != 'succeed':
            dialog_id = data[0]['dialog_ids'][i - 1]
            dialog = all_data[dialog_id]
            data_fails.append((i, dialog_id, dialog))
    if len(data_fails) > 0:
        print(f'{len(data_fails)} failed dialogs found.')
    else:
        print(f'No failed dialogs found. Finish.')
        return

    # Step 4. Update
    pbar = tqdm(data_fails)
    n_total, n_succeed = 0, 0
    for i, dialog_id, dialog in pbar:
        pbar.set_description(f'Processing {dialog_id}')

        try:
            succeed, result = run_and_evaluate(dialog, dialog_id, agent_type, agent_model, user_model)
        except Exception as e:
            # raise e
            msg = f'run_and_evaluate failed as {e.__class__.__name__}: '
            print(colored(msg, 'red') + str(e))
            print(f'Update dialog faided: Line {i + 1}, Dialog_id: {dialog_id}')
            succeed = False
        else:
            data[i] = result
            with open(updated_log_file, 'w') as f:
                data_raw = [json.dumps(item, default=json_default_func) for item in data]
                f.write('\n'.join(data_raw) + '\n')
            print(f'Updated to "{updated_log_file}": Line {i + 1}, Dialog_id: {dialog_id}')

        n_total += 1
        n_succeed += succeed
        succeed_rate = n_succeed / n_total
        pbar.set_postfix_str(f'succeed: {succeed_rate:.0%} ({n_succeed}/{n_total})', refresh=False)
    print(f'Finish: succeed: {succeed_rate:.0%} ({n_succeed}/{n_total})')

    # Step 5. Summary
    with open(updated_log_file) as f:
        data = [json.loads(s) for s in f.read().splitlines()]

    metric_tracker = MetricTracker()
    for item in data[1:]:
        if item['status'] == 'succeed':
            metric_tracker.add_dialog_eval_results(item['dialog_id'], item['eval_results'])
            metric_tracker.add_cost(item['dialog_id'], item['cost'])

    # Step 6. Summary
    summary = metric_tracker.generate_summary_tables()
    with open(updated_score_table_file, 'w') as f:
        f.write(summary + '\n')


if __name__ == '__main__':
    batch_run()
