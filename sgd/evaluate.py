from collections import OrderedDict
import json
import sqlite3

import openai
import tenacity
from termcolor import cprint

from evaluate import ANSWER_FORMAT_TEMPLATE, HUMAN_TEMPLATE, SYSTEM_PROMPT
from sgd.user import prepare_goals_str
from sgd.utils import INFO_DB_PATH, load_schemas
from utils import calc_openai_cost, tenacity_retry_log

schemas = load_schemas()

def extract_user_goals_canonical(dialog):
    goals = OrderedDict()

    for turn in dialog['turns']:
        if turn['speaker'] != 'USER':
            continue
        for frame in turn['frames']:
            service_name = frame['service']
            intent = frame['state']['active_intent']
            if service_name not in goals:
                goals[service_name] = OrderedDict()
            if intent not in goals[service_name]:
                goals[service_name][intent] = {'inform': {}, 'request': []}

            for action in frame['actions']:
                if action['act'] == 'INFORM':
                    goals[service_name][intent]['inform'][action['slot']] = action['canonical_values'][0]
                elif action['act'] == 'REQUEST':
                    goals[service_name][intent]['request'].append(action['slot'])

    goals = {k: v for k, v in goals.items() if v}
    for service_name, service in goals.items():
        service = {k: v for k, v in service.items() if v['inform'] or v['request']}
        for intent in service.values():
            intent['request'] = list(set(intent['request']))
        goals[service_name] = service

    return goals


# region: Inform

def check_inform(service_name, intent_name, inform_state, callings):
    func_name = service_name + '_' + intent_name
    for call in callings:
        if call['name'] == func_name:
            matches = []
            for k, v in inform_state.items():
                v = str(v).lower()
                vv = call['args'].get(k)
                vv = str(vv).lower()
                matches.append(v == vv)
            if all(matches):
                return True
    return False


def evaluate_inform(dialog, callings):
    gold_goals = extract_user_goals_canonical(dialog)

    result = {}
    for service_name, service in gold_goals.items():
        result[service_name] = {}
        for intent_name, intent in service.items():
            inform = check_inform(service_name, intent_name, intent['inform'], callings)
            result[service_name][intent_name] = int(inform)

    return result

# endregion

# region: Success: llm

def prepare_log_dialog_str(logs):
    dialog_str = []
    for turn in logs:
        dialog_str.append(f"User: {turn['user']}")
        dialog_str.append(f"AI Assistant: {turn['agent']}")
    dialog_str = '\n'.join(dialog_str)
    return dialog_str


def prepare_questions_and_answer_formarts(goals):
    questions = []
    answer_formats = []
    q_idx = 1

    for service_name, service in goals.items():
        for intent_name, intent in service.items():
            intent_dict = {it['name']: it for it in schemas[service_name]['intents']}
            slot_dict = {it['name']: it for it in schemas[service_name]['slots']}
            for slot in intent['request']:
                intent_desc = intent_dict[intent_name]['description']
                intent_desc = intent_desc[0].lower() + intent_desc[1:]

                slot_desc = slot_dict[slot]['description']
                slot_desc = slot_desc[0].lower() + slot_desc[1:]

                questions.append(f'{q_idx}. When the user {intent_desc}, what is the {slot_desc}?')
                answer_formats.append(f'"{service_name} {slot}": "<fill the answer of question {q_idx}>"')
                q_idx += 1


    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


@tenacity.retry(wait=tenacity.wait_exponential(min=2, max=60),
                stop=tenacity.stop_after_attempt(8),
                reraise=True,
                before_sleep=tenacity_retry_log,
                retry=tenacity.retry_if_exception_type((openai.OpenAIError, json.JSONDecodeError)))
def request_slots_llm_qa(goals_str, dialog_str, questions, answer_formats, model_name):
    human_prompt = HUMAN_TEMPLATE.format(
        goals=goals_str, 
        dialog=dialog_str, 
        questions=questions, 
        answer_formats=answer_formats,
    )

    completion = openai.ChatCompletion.create(
        model=model_name,
        temperature=0,
        request_timeout=10,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": human_prompt}],
    )
    cost = calc_openai_cost(model_name, completion['usage'])
    result_origin = completion['choices'][0]['message']['content']

    # Clean json string
    def clean_json_string(text):
        text = text.strip('`')
        if (pos := text.find('```')) > -1:
            text = text[:pos]
        return text
    result_cleann = clean_json_string(result_origin)

    llm_answer = json.loads(result_cleann)

    # Clean
    llm_answer2 = {}
    for k, v in llm_answer.items():
        if k.endswith('price') and v.startswith('$'):
            llm_answer2[k] = v.lstrip('$')
        else:
            llm_answer2[k] = v
    llm_answer = llm_answer2

    return llm_answer, cost

# endregion

# region: Success: match

def sgd_function_info(service_name, intent, args, db_path=INFO_DB_PATH):
    fields = ', '.join(f'"{field}"' for field in intent['result_slots'])
    sql = f'SELECT {fields} FROM {service_name}'
    if args:
        conditions = ' AND '.join(f'"{k}" = "{v}"' for k, v in args.items())
        sql += f' WHERE {conditions}'

    conn = sqlite3.connect(db_path)
    cursor = conn.execute(sql)

    slots = [desc[0] for desc in cursor.description]
    records = []
    for item in cursor:
        record = {slot: value for slot, value in zip(slots, item)}
        records.append(record)

    conn.close()
    return records


def record_satisfying(record, slot_values):
    for k, v in slot_values.items():
        v = str(v).lower()
        vv = record.get(k)
        vv = str(vv).lower()
        if v != vv:
            return False
    return True


def check_success(service_name, intent, callings, slot_values):
    for call in callings:
        if call['name'].startswith(service_name):
            records = sgd_function_info(service_name, intent, call['args'])
            if any(record_satisfying(record, slot_values) for record in records):
                return True
    return False


def make_request_eval_result(llm_answer, gold_goals, callings):
    print(f'make_request_eval_result: llm_answer')
    for k, v in llm_answer.items():
        print(f'{k}: {v}')
    print()
    result = {}
    for service_name, service in gold_goals.items():
        result[service_name] = {}
        for intent_name, intent_goals in service.items():
            if intent_goals['request'] == []:
                result[service_name][intent_name] = None
                continue
            slot_values = {slot: llm_answer.get(f'{service_name} {slot}') for slot in intent_goals['request']}
            intent_dict = {it['name']: it for it in schemas[service_name]['intents']}
            intent = intent_dict[intent_name]
            success = check_success(service_name, intent, callings, slot_values)
            result[service_name][intent_name] = int(success)
    return result

# endregion

# region: Success

def evaluate_request(dialog, logs, callings, model_name='gpt-3.5-turbo-0613'):
    goals_str = prepare_goals_str(dialog)

    dialog_str = prepare_log_dialog_str(logs)

    gold_goals = extract_user_goals_canonical(dialog)
    questions, answer_formats = prepare_questions_and_answer_formarts(gold_goals)

    llm_answer, cost = request_slots_llm_qa(goals_str, dialog_str, questions, answer_formats, model_name)

    result = make_request_eval_result(llm_answer, gold_goals, callings)

    return result, cost

# endregion

# region: Evaluation (Inform & Success)

def evaluate(dialog, logs, callings):
    inform_result = evaluate_inform(dialog, callings)
    success_result, cost = evaluate_request(dialog, logs, callings)
    
    eval_result = {}
    gold_goals = extract_user_goals_canonical(dialog)
    for service_name, service_goal in gold_goals.items():
        eval_result[service_name] = {intent_name: {'inform': None, 'success': None} for intent_name in service_goal}

    for service_name, service_result in eval_result.items():
        for intent_name, intent_result in service_result.items():
            inform = inform_result[service_name][intent_name]
            success = success_result[service_name][intent_name]
            intent_result['inform'] = int(inform)
            intent_result['success'] = success if success is None else int(inform and success)

    return eval_result, cost

# endregion

def show_eval_result(eval_result):
    for service_name, service_result in eval_result.items():
        print('service: ', end='')
        cprint(service_name, 'red', attrs=['bold'], force_color=True)
        for intent_name, intent_result in service_result.items():
            print(f'  intent: ', end='')
            cprint(intent_name, 'green', attrs=['bold'], force_color=True, end='')
            print(intent_result)
