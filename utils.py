from io import StringIO
import json
import os
import random
import re
from pprint import pprint

from termcolor import colored


DOMAINS = ['hotel', 'restaurant', 'attraction', 'train', 'taxi']

DATA_DIR = 'data/mwoz/origin'
DATA_PATH = 'data/mwoz/origin/data.json'

DB_PATH = 'data/mwoz/db/multiwoz.db'
BOOK_DB_PATH = 'data/mwoz/db/multiwoz_book.db'

OPENAI_API_KEY = os.environ['OPENAI_API_KEY']
print(f'{OPENAI_API_KEY = }')

HEADER_WIDTH = 50
HEADER_COLOR = '\u001b[1;31m'
USER_COLOR = HEADER_COLOR
AGENT_COLOR = HEADER_COLOR
RESET_COLOR = '\u001b[0m'

OPENAI_PRICE = {
    'gpt-3.5-turbo': {
        'input': 0.0015 / 1000,
        'output': 0.002 / 1000,
    },
    'text-davinci': {
        'text': 0.02 / 1000,
    },
}


class TableItem:

    def __repr__(self):
        d = {k: v for k, v in self.__dict__.items() if not k.startswith('_')}
        s = StringIO()
        pprint(d, stream=s)
        s = s.getvalue().strip()
        text = self.__tablename__.capitalize() + ':\n' + s
        return text
    
    def json_serialize(self):
        d = {'type': self.__tablename__.capitalize()}
        for k, v in self.__dict__.items():
            if not k.startswith('_'):
                d[k] = v
        return d
    

def json_default_func(obj):
    if isinstance(obj, TableItem):
        return obj.json_serialize()
    else:
        raise TypeError(f'Object of type {obj.__class__.__name__} '
                        f'is not JSON serializable')


def calc_openai_cost(model, usage):
    if model.startswith('gpt-3.5-turbo'):
        price = OPENAI_PRICE['gpt-3.5-turbo']
        cost = usage['prompt_tokens'] * price['input'] + usage['completion_tokens'] * price['output']
    elif model.startswith('text-davinci'):
        price = OPENAI_PRICE['text-davinci']
        cost = usage['total_tokens'] * price['text']
    else:
        raise ValueError(f'{model = }')
    return cost


def clean_time(time):
    time = time.lower()
    time = time.replace('after', '') 
    time = time.replace('before', '')
    time = time.replace('am', '')
    time = time.replace('pm', '')
    time = time.strip()
    time = '0' + time if re.fullmatch(r'\d:\d\d', time) else time
    return time


def clean_name(name):
    name = name.lower().strip()
    if name.startswith('the'):
        name = name[len('the'):]
    if name.endswith('restaurant'):
        name = name[:-len('restaurant')]
    if name.endswith('hotel'):
        name = name[:-len('hotel')]
    name = name.strip()
    return name


def prepare_goals_string(goals):
    if isinstance(goals, str):  # WOZxxx.json, One string
        result = re.match('Task \d{5}: (.*)', goals)
        assert result is not None
        goals = result[1].strip()
        goals = re.split('\.\s+', goals)
    goals_str = [msg if msg.endswith('.') else f'{msg}.' for msg in goals]
    goals_str = '\n'.join(goals_str)
    # Sub <span>xx<span> => xx
    goals_str = re.sub(r'<span\b[^>]*>(.*?)</span>', r'\1', goals_str)
    return goals_str


def load_data(data_path=DATA_PATH):
    with open(data_path) as f:
        data = json.load(f)

    # Remove dialogs in police & hospital
    data2 = {}
    for idx, dialog in data.items():
        if dialog['goal']['police'] or dialog['goal']['hospital']:
            continue
        data2[idx] = dialog
    data = data2

    return data


def load_data_split(split, data_dir=DATA_DIR):
    data_path = os.path.join(data_dir, 'data.json')
    with open(data_path) as f:
        data = json.load(f)

    with open(os.path.join(data_dir, 'testListFile.txt')) as f:
        test_ids = set(f.read().strip().splitlines())
    with open(os.path.join(data_dir, 'valListFile.txt')) as f:
        valid_ids = set(f.read().strip().splitlines())

    if split == 'train':
        data_split = {idx: dialog for idx, dialog in data.items() if idx not in test_ids and idx not in valid_ids}
    elif split == 'test':
        data_split = {idx: dialog for idx, dialog in data.items() if idx in test_ids}
    elif split == 'valid':
        data_split = {idx: dialog for idx, dialog in data.items() if idx in valid_ids}
    else:
        raise ValueError(f'{split = }')
    data = data_split

    # Remove dialogs in police & hospital
    data2 = {}
    for idx, dialog in data.items():
        if dialog['goal']['police'] or dialog['goal']['hospital']:
            continue
        data2[idx] = dialog
    data = data2

    return data


def print_dialog_goal(dialog, dialog_id):
    print(f'[Dialog Id] {dialog_id}', end='\n\n')

    goals = prepare_goals_string(dialog['goal']['message'])
    print(f'[User Goals]')
    print(goals, end='\n\n')

    for domain in ['restaurant', 'hotel', 'attraction', 'train', 'taxi']:
        if d := dialog['goal'][domain]:
            print(f'[{domain}]')
            pprint(d)
            print()


def pick_dialog(data, dialog_id='random', domain='all', exclusive=False):
    assert domain == 'all' or domain in DOMAINS

    if dialog_id == 'random':
        while True:
            dialog_id = random.choice(list(data.keys()))
            goal = data[dialog_id]['goal']
            if domain == 'all':
                break
            if exclusive:
                if goal[domain] and all(not goal[d] for d in DOMAINS if d != domain):
                    break
            else:
                if goal[domain]:
                    break
    else:
        assert dialog_id in data
    dialog = data[dialog_id]

    return dialog, dialog_id


def show_dialog_text(dialog):
    for i, turn in enumerate(dialog['log']):
        role = 'User' if i % 2 == 0 else 'AI Assistant'
        print(f'{role}: {turn["text"]}')


def tenacity_retry_log(retry_state):
    t = retry_state.next_action.sleep
    e = retry_state.outcome.exception()
    msg = f'Tenacity: Retrying call Agent in {t:.2f} seconds as it raise {e.__class__.__name__}: '
    msg = colored(msg, 'red', force_color=True) + str(e)
    print(msg)
