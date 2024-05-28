import json
import os
import random

from termcolor import cprint


DATA_DIR = 'data/sgd/origin'

INFO_DB_PATH = 'data/sgd/db/sgd.db'
TRANS_DB_PATH = 'data/sgd/db/sgd_trans.db'

schemas = None


def load_schemas(data_dir=DATA_DIR):
    global schemas

    if schemas is not None:
        return schemas

    schema_list = []
    with open(os.path.join(data_dir, 'train', 'schema.json')) as f:
        schema_list += json.load(f)
    with open(os.path.join(data_dir, 'dev', 'schema.json')) as f:
        schema_list += json.load(f)
    with open(os.path.join(data_dir, 'test', 'schema.json')) as f:
        schema_list += json.load(f)

    schemas = {schema['service_name']: schema for schema in schema_list}
    return schemas


dialogs = None


def load_dialogs(data_dir=DATA_DIR):
    global dialogs

    if dialogs is not None:
        return dialogs
    
    def load_dialogs_split(split):
        dialogs = []
        data_sub_dir = os.path.join(data_dir, split)
        print(f'Loading dialogs from "{data_sub_dir}"...')
        for name in os.listdir(data_sub_dir):
            if name.startswith('dialogues_'):
                with open(os.path.join(data_sub_dir, name)) as f:
                    dialogs += json.load(f)
        for dialog in dialogs:
            dialog['dialogue_id'] = split + '_' + dialog['dialogue_id']
        return dialogs

    dialogs = []
    dialogs += load_dialogs_split('train')
    dialogs += load_dialogs_split('dev')
    dialogs += load_dialogs_split('test')

    dialogs = {d['dialogue_id']: d for d in dialogs}

    print(f'Loading completed. {len(dialogs)} dialogs loaded.')
    return dialogs


def pick_dialog(dialogs, dialog_id='random'):
    if dialog_id == 'random':
        dialog_id = random.choice(list(dialogs.keys()))
    else:
        assert dialog_id in dialogs
    dialog = dialogs[dialog_id]

    return dialog


def show_dialog(dialog):
    for turn in dialog['turns']:
        if turn['speaker'] == 'USER':
            cprint('        User: ', 'blue', attrs=['bold'], force_color=True, end='')
            cprint(turn['utterance'], 'blue', force_color=True)
        else:
            cprint('AI Assistant: ', 'yellow', attrs=['bold'], force_color=True, end='')
            cprint(turn['utterance'], 'yellow', force_color=True)


def show_dialog_goals(goals):
    for service_name, service_goals in goals.items():
        print('service: ', end='')
        cprint(service_name, 'red', attrs=['bold'], force_color=True)
        for intent_name, intent_goals in service_goals.items():
            print(f'  intent: ', end='')
            cprint(intent_name, 'green', attrs=['bold'], force_color=True)
            inform_str = [f"{s} = {vd['canonical_value']}" for s, vd in intent_goals['inform'].items()]
            inform_str = ', '.join(inform_str)
            print('     inform:', inform_str)
            print('    request:', intent_goals['request'])
