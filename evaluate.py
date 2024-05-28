import json
from pprint import pprint

import openai
import tenacity

import booking
import db
from utils import (DOMAINS, OPENAI_API_KEY, calc_openai_cost, clean_time,
                   prepare_goals_string, tenacity_retry_log)

openai.api_key = OPENAI_API_KEY


SYSTEM_PROMPT = '''You are a calm, objective and professional judger and good at to evaluate quality of dialuges between user and AI Assistant. Your judging results are always accurate and concise.'''

HUMAN_TEMPLATE = '''There is a dialogue between a user and an AI Assistant. The user has the goals in his minds (User Goals) and talks with the AI Assistant to achieve the goals. The AI Assistant is a intelligent agent that is able to understand the user utterances, decide to take actions to use external tools, and generate proper responses. Your task is to judge whether the AI Assistant helps the user achieve his goals successfully by answering the questions one by one.

User Goals:

{goals}

Dialogue:

{dialog}

Questions:

{questions}

{answer_formats}'''


ANSWER_FORMAT_TEMPLATE = '''Answer Format:

Please output the answer in json format like this:
```
{{
{answer_formats}
}}
```
If no answer for a question, please fill `none`.

Answer:

```'''


def prepare_dialog_string_with_action(dialog):
    dialog_str = []
    for turn in dialog:
        turn_str = []
        turn_str.append(f'Turn {turn["turn_idx"]}:')
        turn_str.append(f'User: {turn["user"]}')
        turn_str.append(f'AI Assistant: {turn["agent"]}')
        if len(turn['actions']) > 0:
            for action in turn['actions']:
                turn_str.append(f'System Action: {action["action_name"]}; Action Input: {action["action_input"]}')
        else:
            turn_str.append('System Action: None')
        turn_str = '\n'.join(turn_str)
        dialog_str.append(turn_str)
    dialog_str = '\n\n'.join(dialog_str)
    return dialog_str


def prepare_dialog_string(dialog):
    dialog_str = []
    for turn in dialog:
        dialog_str.append(f'User: {turn["user"]}')
        dialog_str.append(f'AI Assistant: {turn["agent"]}')
    dialog_str = '\n'.join(dialog_str)
    return dialog_str


# region: Taxi

TAXI_SLOT_MAP = {
    'departure': 'departure',
    'destination': 'destination',
    'leaveAt': 'leave time',
    'arriveBy': 'arrival time',
    'car type': 'car type',
    'phone': 'phone number',
}


def prepare_taxi_questions(goal):
    '''
    Goal:
        'info': {'arriveBy', 'departure', 'destination', 'leaveAt'}
        'reqt': {'car type', 'phone'}
    '''
    questions = []
    answer_formats = []
    q_idx = 1

    # Find Venue (info): arriveBy, departure, destination, leaveAt
    for slot in goal['info']:
        slot_mapped = TAXI_SLOT_MAP[slot]
        q = f'{q_idx}. What is the {slot_mapped} of the taxi that the user books?'
        a = f'"{slot_mapped}": "<fill the answer of question {q_idx}>"'
        questions.append(q)
        answer_formats.append(a)
        q_idx += 1

    # Request Slots (reqt): car type, phone
    for slot in goal['reqt']:
        slot_mapped = TAXI_SLOT_MAP[slot]
        q = f'{q_idx}. What is the {slot_mapped} of the taxi?'
        a = f'"{slot_mapped}": "<fill the answer of question {q_idx}>"'
        questions.append(q)
        answer_formats.append(a)
        q_idx += 1

    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


def evaluate_by_domain_taxi(goal, llm_answer):
    result = {
        'domain': 'taxi',
        'goal': goal,
        'inform': {
            'complete': None,
            'slot_values': None,
        },
        'success': {
            'complete': None,
            'slot_values': None,
        },
        'book': {
            'complete': None,
        }
    }

    # Clean time
    for slot in ['leave time', 'arrival time']:
        if time := llm_answer.get(slot):
            llm_answer[slot] = clean_time(time)

    # Inform
    if goal.get('info'):
        slot_values = {slot: llm_answer[TAXI_SLOT_MAP[slot]] for slot in goal['info']}
        complete = all(v.lower() == slot_values[s].lower() for s, v in goal['info'].items())

        result['inform']['complete'] = int(complete)
        result['inform']['slot_values'] = slot_values

    # Success
    if goal.get('reqt'):
        slot_values = {slot: llm_answer[TAXI_SLOT_MAP[slot]] for slot in goal['reqt']}
        complete = all(v != 'none' for v in slot_values.values())

        result['success']['complete'] = int(complete and result['inform']['complete'])
        result['success']['slot_values'] = slot_values

    return result

# endregion: Taxi

# region: Train

TRAIN_SLOT_MAP = {
    'trainID': 'train id',
    'price': 'price',
    'duration': 'duration',
    'leaveAt': 'leave time',
    'arriveBy': 'arrive time',
}


def prepare_train_questions(goal):
    '''
    Goal:
        'info': {'arriveBy', 'day', 'departure', 'destination', 'leaveAt'},
        'book': {'invalid', 'people'},
        'reqt': {'arriveBy', 'duration', 'leaveAt', 'price', 'trainID'},

    Asserts:
        - assert goal.get('reqt') XOR goal.get('book')
    '''

    questions = []
    answer_formats = []

    # info: implied by book reference number

    # Book (book)
    if goal.get('book'):
        assert not goal.get('reqt')
        q = f'1. What is the reference number of the booked train tickets?'
        a = f'"reference number": "<fill the answer of question 1>"'
        questions.append(q)
        answer_formats.append(a)
    
    # Request Slots (reqt)
    else:  
        assert not goal.get('book')
        q_idx = 1
        for slot in goal.get('reqt'):
            slot_mapped = TRAIN_SLOT_MAP[slot]
            if slot == 'trainID':
                q = f'{q_idx}. What is the id of the train?'
                a = f'"{slot_mapped}": "<fill the answer of question {q_idx}>"'
            else:
                q = f'{q_idx}. What is the {slot_mapped} of the train?'
                a = f'"{slot_mapped}": "<fill the answer of question {q_idx}>"'
            questions.append(q)
            answer_formats.append(a)
            q_idx += 1

    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


def evaluate_by_domain_train(goal, llm_answer):
    # Success XOR Book
    result = {
        'domain': 'train',
        'goal': goal,
        'inform': {
            'complete': None,
        },
        'success': {
            'complete': None,
            'slot_values': None,
        },
        'book': {
            'complete': None,
            'refer_number': None,
            'book_record': None,
            'train_info': None,
        }
    }

    # Clean time
    for slot in ['leave time', 'arrival time']:
        if time := llm_answer.get(slot):
            llm_answer[slot] = clean_time(time)

    # Success
    if goal.get('reqt'):
        slot_values = {slot: llm_answer[TRAIN_SLOT_MAP[slot]] for slot in goal['reqt']}
        items = db.query_trains(goal['info'])
        complete = any(item.satisfying(slot_values) for item in items)

        result['inform']['complete'] = int(complete)
        result['success']['complete'] = int(complete)
        result['success']['slot_values'] = slot_values

    # Book
    if goal.get('book'):
        refer_number = llm_answer['reference number']
        if book_record := booking.query_booking_by_refer_num('train', refer_number):
            if train := db.query_train_by_id(book_record.trainID):
                inform_complete = train.satisfying(goal['info'])
                book_complete = inform_complete and book_record.satisfying({'tickets': goal['book']['people']})
            else:
                train = f'"{book_record.trainID}" is not found in the "train" table.'
                inform_complete, book_complete = False, False
        else:
            book_record = f'"{refer_number}" is not found in the "book_train" table.'
            train = f'No train as invalid refer number "{refer_number}".'
            inform_complete, book_complete = False, False

        result['book']['refer_number'] = refer_number
        result['inform']['complete'] = int(inform_complete)
        result['book']['complete'] = int(book_complete)
        result['book']['book_record'] = book_record
        result['book']['train_info'] = train

    return result

# endregion: Train

# region: hotel, restaurant, attraction

def prepare_hotel_questions(goal):
    '''
    Goal:
        'info': {'area', 'internet', 'name', 'parking', 'pricerange', 'stars', 'type'},
        'book': {'day', 'invalid', 'people', 'pre_invalid', 'stay'},
        'reqt': {'area', 'pricerange', 'type', 'stars',
                'internet', 'parking',
                'address', 'phone', 'postcode',},

    Asserts:
        - assert fail_book.issubset(book)
        - Book: 'stay', 'people', 'day' all in book
    '''
    questions = []
    answer_formats = []
    q_idx = 1

    # Find Venue (info)
    if goal.get('book', []):
        q = f'{q_idx}. What hotel does the user choose and would like to book it?'
    else:
        q = f'{q_idx}. What hotel is the user interested in and asking information about it?'
    a = f'"hotel": "<fill the answer of question {q_idx}>"'
    questions.append(q)
    answer_formats.append(a)
    q_idx += 1

    # Book (book)
    if goal.get('book'):
        q = f'{q_idx}. What is the reference number of the booked hotel?'
        a = f'"reference number": "<fill the answer of question {q_idx}>"'
        questions.append(q)
        answer_formats.append(a)
        q_idx += 1

    # Request Slots (reqt)
    for slot in goal.get('reqt', []):
        if slot == 'area':
            q = f'{q_idx}. What is the area of the hotel? (east, west, north, south, centre)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (east, west, north, south, centre)>"'
        elif slot == 'pricerange':
            q = f'{q_idx}. What is the price of the hotel? (cheap, moderate, expensive)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (cheap, moderate, expensive)>"'
        elif slot == 'type':
            q = f'{q_idx}. What is the type of the hotel? (guesthouse, hotel)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (guesthouse, hotel)>"'
        elif slot == 'stars':
            q = f'{q_idx}. What is the stars of the hotel? (1, 2, 3, ...)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (1, 2, 3, ...)>"'
        elif slot == 'internet':
            q = f'{q_idx}. Does the hotel have free internet/wifi? (yes, no)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (yes, no)>"'
        elif slot == 'parking':
            q = f'{q_idx}.  Does the hotel have free parking? (yes, no)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (yes, no)>"'
        elif slot == 'address':
            q = f'{q_idx}. What is the address of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'phone':
            q = f'{q_idx}. What is the phone number of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'postcode':
            q = f'{q_idx}. What is the postcode of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        else:
            q = None
            a = None

        if q and a:
            questions.append(q)
            answer_formats.append(a)
            q_idx += 1

    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


def prepare_restaurant_questions(goal):
    '''
    Goal:
        'info': {'area', 'food', 'name', 'pricerange'},
        'book': {'day', 'invalid', 'people', 'pre_invalid', 'time'},
        'reqt': {'address', 'area', 'food', 'phone', 'postcode', 'pricerange'},

    Asserts:
        - assert fail_book.issubset(book)
        - Book: 'time', 'people', 'day' all in book
    '''

    questions = []
    answer_formats = []
    q_idx = 1

    # Find Venue (info)
    if goal.get('book', []):
        q = f'{q_idx}. What restaurant does the user choose and would like to book it?'
    else:
        q = f'{q_idx}. What restaurant is the user interested in and asking information about it?'
    a = f'"restaurant": "<fill the answer of question {q_idx}>"'
    questions.append(q)
    answer_formats.append(a)
    q_idx += 1

    # Book (book)
    if goal.get('book'):
        q = f'{q_idx}. What is the reference number of the booked restaurant?'
        a = f'"reference number": "<fill the answer of question {q_idx}>"'
        questions.append(q)
        answer_formats.append(a)
        q_idx += 1

    # Request Slots (reqt)
    for slot in goal.get('reqt', []):
        if slot == 'area':
            q = f'{q_idx}. What is the area of the hotel? (east, west, north, south, centre)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (east, west, north, south, centre)>"'
        elif slot == 'pricerange':
            q = f'{q_idx}. What is the price of the hotel? (cheap, moderate, expensive)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (cheap, moderate, expensive)>"'
        elif slot == 'food':
            q = f'{q_idx}. What is the food type of the restaurant?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'address':
            q = f'{q_idx}. What is the address of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'phone':
            q = f'{q_idx}. What is the phone number of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'postcode':
            q = f'{q_idx}. What is the postcode of the hotel?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        else:
            q = None
            a = None

        if q and a:
            questions.append(q)
            answer_formats.append(a)
            q_idx += 1

    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


def prepare_attraction_questions(goal):
    '''
    Goal:
        'info': {'area', 'name', 'type'},
        'reqt': {'address', 'area', 'entrance fee', 'phone', 'postcode', 'type'},
    '''

    questions = []
    answer_formats = []
    q_idx = 1

    # Find Venue (info)
    q = f'{q_idx}. What attraction is the user interested in and asking information about it?'
    a = f'"attraction": "<fill the answer of question {q_idx}>"'
    questions.append(q)
    answer_formats.append(a)
    q_idx += 1

    # Request Slots (reqt)
    for slot in goal.get('reqt', []):
        if slot == 'area':
            q = f'{q_idx}. What is the area of the attraction? (east, west, north, south, centre)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (east, west, north, south, centre)>"'
        elif slot == 'entrance fee':
            q = f'{q_idx}. What is the entrance fee of the attraction? (cheap, moderate, expensive)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (cheap, moderate, expensive)>"'
        elif slot == 'type':
            q = f'{q_idx}. What is the type of the attraction? (guesthouse, hotel)'
            a = f'"{slot}": "<fill the answer of question {q_idx} (guesthouse, hotel)>"'
        elif slot == 'address':
            q = f'{q_idx}. What is the address of the attraction?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'phone':
            q = f'{q_idx}. What is the phone number of the attraction?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        elif slot == 'postcode':
            q = f'{q_idx}. What is the postcode of the attraction?'
            a = f'"{slot}": "<fill the answer of question {q_idx}>"'
        else:
            q = None
            a = None

        if q and a:
            questions.append(q)
            answer_formats.append(a)
            q_idx += 1

    questions = '\n'.join(questions)

    answer_formats = [' ' * 4 + s for s in answer_formats]
    answer_formats = '\n'.join(answer_formats)
    answer_formats = ANSWER_FORMAT_TEMPLATE.format(answer_formats=answer_formats)

    return questions, answer_formats


def evaluate_by_domain_others(goal, llm_answer, domain):  # restaurant, hotel, attraction
    '''Domain: restaurant, hotel, attraction'''
    result = {
        'domain': domain,
        'goal': goal,
        'inform': {
            'complete': None,
            'venue_name': None,
            'venue_info': None,
        },
        'success': {
            'complete': None,
            'slot_values': None,
        },
        'book': {
            'complete': None,
            'refer_number': None,
            'book_record': None,
        }
    }

    # Inform
    name = llm_answer[domain]
    venue = db.query_venue_by_name(domain=domain, name=name)
    if venue:
        complete = venue.satisfying(goal['info']) or \
            bool(goal['fail_info']) and venue.satisfying(goal['fail_info'])
    else:
        venue = f'"{name}" is not found in the "{domain}" table.'
        complete = False
    
    result['inform']['complete'] = int(complete)
    result['inform']['venue_name'] = name
    result['inform']['venue_info'] = venue

    # Success
    if goal.get('reqt'):
        slot_values = {s: llm_answer[s] for s in goal['reqt']}
        if isinstance(venue, db.Veune) and result['inform']['complete']:
            complete = venue.satisfying(slot_values)
        else:
            complete = False

        result['success']['slot_values'] = slot_values
        result['success']['complete'] = int(complete)

    # Book
    if goal.get('book'):
        refer_number = llm_answer['reference number']
        book_record = booking.query_booking_by_refer_num(domain=domain, refer_number=refer_number)
        if book_record is None:
            book_record = f'"{refer_number}" is not found in the "book_{domain}" table.'

        if result['inform']['complete'] and isinstance(book_record, booking.BookRecord):
            f1 = book_record.name == venue.name
            f2 = book_record.satisfying(goal['book']) or \
                bool(goal['fail_book']) and book_record.satisfying(goal['fail_book'])
            complete = f1 and f2
        else:
            complete = False

        result['book']['refer_number'] = refer_number
        result['book']['complete'] = int(complete)
        result['book']['book_record'] = book_record

    return result

# endregion

@tenacity.retry(wait=tenacity.wait_exponential(min=2, max=60),
                stop=tenacity.stop_after_attempt(8),
                before_sleep=tenacity_retry_log,
                retry=tenacity.retry_if_exception_type((openai.OpenAIError, json.JSONDecodeError)))
def llm_qa(goal_messages, dialog_pred, questions, answer_formats, model):
    goals_str = prepare_goals_string(goal_messages)
    dialog_str = prepare_dialog_string(dialog_pred)
    human_prompt = HUMAN_TEMPLATE.format(
        goals=goals_str, 
        dialog=dialog_str, 
        questions=questions, 
        answer_formats=answer_formats,
    )

    completion = openai.ChatCompletion.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": human_prompt},
        ],
        request_timeout=10,
    )
    cost = calc_openai_cost(model, completion['usage'])
    result_origin = completion['choices'][0]['message']['content']

    # Clean json string
    def clean_json_string(text):
        text = text.strip('`')
        if (pos := text.find('```')) > -1:
            text = text[:pos]
        return text
    result_cleann = clean_json_string(result_origin)

    llm_answer = json.loads(result_cleann)
    return llm_answer, cost


def show_eval_result(result):
    RED = '\u001b[1;31m'
    GREEN = '\u001b[1;33m'
    RESET = '\u001b[0m'
    for k, v in result.items():
        if isinstance(v, str) or isinstance(v, float) or v is None:
            print(RED + f'[{k}]' + RESET + f' {v}')

        elif isinstance(v, dict):
            indent = 4
            if 'complete' in v:
                print(RED + f'[{k}] complete: {v["complete"]}' + RESET)
            else:
                print(RED + f'[{k}]' + RESET)
            for kk, vv in v.items():
                if kk == 'complete':
                    continue
                print(' ' * indent + GREEN + f'{kk}: ' + RESET, end='')
                print(vv)

        else:
            print(RED + f'[{k}]' + RESET)
            pprint(v)


def evaluate_by_domain(domain, run_result, model='gpt-3.5-turbo-0301', verbose=True):
    assert run_result['goals'].get(domain)
    
    goal_dict = run_result['goals'][domain]
    dialog_pred = run_result['dialog_pred']
    goal_messages = run_result['goal_messages']

    if domain == 'hotel':
        questions, answer_formats = prepare_hotel_questions(goal_dict)
        llm_answer, cost = llm_qa(goal_messages, dialog_pred, questions, answer_formats, model)
        result = evaluate_by_domain_others(goal_dict, llm_answer, domain)

    elif domain == 'restaurant':
        questions, answer_formats = prepare_restaurant_questions(goal_dict)
        llm_answer, cost = llm_qa(goal_messages, dialog_pred, questions, answer_formats, model)
        result = evaluate_by_domain_others(goal_dict, llm_answer, domain)

    elif domain == 'attraction':
        questions, answer_formats = prepare_attraction_questions(goal_dict)
        llm_answer, cost = llm_qa(goal_messages, dialog_pred, questions, answer_formats, model)
        result = evaluate_by_domain_others(goal_dict, llm_answer, domain)

    elif domain == 'train':
        questions, answer_formats = prepare_train_questions(goal_dict)
        llm_answer, cost = llm_qa(goal_messages, dialog_pred, questions, answer_formats, model)
        result = evaluate_by_domain_train(goal_dict, llm_answer)

    elif domain == 'taxi':
        questions, answer_formats = prepare_taxi_questions(goal_dict)
        llm_answer, cost = llm_qa(goal_messages, dialog_pred, questions, answer_formats, model)
        result = evaluate_by_domain_taxi(goal_dict, llm_answer)

    else:
        raise ValueError(f'{domain = }')
    
    if verbose:
        show_eval_result(result)

    result['cost'] = cost
    return result
