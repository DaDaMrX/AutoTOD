from collections import OrderedDict

from termcolor import cprint

from sgd.utils import load_schemas
from base_user import BaseUser

TEMPLATE = '''You are a dialogue simulator where you act as a user to talk to an AI assistant to complete some tasks.

You should carefully read and understand the User Goals below, then talk with the AI Assistant and gradually express the intents in the goals. Your purpose is to let the user achieve the goals as much as possible.  

Note that the AI Assistant is not perfect. It may make various mistakes, including ignoring the user's requests, executing the wrong instructions, forgetting early conversation content, etc. The user you play should talk to the AI Assistant as patiently as possible, remind him to correct when you find that the AI assistant made a mistake, and complete the task as much as possible.

When the AI Assistant can not find the objects that the user wants, the user may try to use the canonical format in the parentheses, like NYC to New York. When the AI Assistant still can not find any object, the user should stop trying.

When the dialogue goals are completed or are not been completed, the user will output "Dialogue Ends" to indicate the end of the dialogue. The user doesn't need to try conditions other than the dialogue goals.

The user has a clear goal in mind, so he does not need to ask the AI assistant that "Is there anything else I need to know?".

The user does not need to talk too much with the AI assistant. If the task goals are completed, please end the conversation as soon as possible.

There is also a reference dialogue example to achieve the goals. The simulator user may learn from the language style and dialogue strategy. The final simulated dialogue style should be similar to the reference dialogue style. 


User Goals:

{user_goals}

Reference dialogue:

{ref_dialog}

Current conversation:
{history}
AI Assistant: {input}
User:'''

schemas = load_schemas()


def extract_user_goals(dialog):
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
                    goals[service_name][intent]['inform'][action['slot']] = {
                        'value': action['values'][0],
                        'canonical_value': action['canonical_values'][0],
                    }
                elif action['act'] == 'REQUEST':
                    goals[service_name][intent]['request'].append(action['slot'])

    goals = {k: v for k, v in goals.items() if v}
    for service_name, service in goals.items():
        service = {k: v for k, v in service.items() if v['inform'] or v['request']}
        for intent in service.values():
            intent['request'] = list(set(intent['request']))
        goals[service_name] = service

    return goals


def make_goals_str(goals):
    goals_str = []
    goal_index = 0
    for service_name, service in goals.items():
        intent_dict = {it['name']: it for it in schemas[service_name]['intents']}
        slot_dict = {slot['name']: slot for slot in schemas[service_name]['slots']}
        for intent_name, intent in service.items():
            intent_desc = intent_dict[intent_name]['description']
            intent_desc = intent_desc[0].lower() + intent_desc[1:]

            goal_index += 1
            goals_str.append(f'\nGoal {goal_index}:')

            goals_str.append(f'You want to {intent_desc}.')

            inform_str = []
            for slot, value_dict in intent['inform'].items():
                slot_desc = slot_dict[slot]['description']
                slot_desc = slot_desc[0].lower() + slot_desc[1:]
                slot_value_str = f"the {slot_desc} is {value_dict['value']}"
                if 'canonical_value' in value_dict and value_dict['canonical_value'] != value_dict['value']:
                    slot_value_str += f" ({value_dict['canonical_value']})"
                inform_str.append(slot_value_str)
            if inform_str:
                inform_str = ', '.join(inform_str) + '.'
                goals_str.append(f'You will inform the AI Assistant that: {inform_str}')

            request_str = []
            for slot in intent['request']:
                slot_desc = slot_dict[slot]['description']
                slot_desc = slot_desc[0].lower() + slot_desc[1:]
                request_str.append(f'the {slot_desc}')
            if request_str:
                request_str = ', '.join(request_str) + '.'
                goals_str.append(f'You ask the AI Assistant to know: {request_str}')

    goals_str = '\n'.join(goals_str).strip()
    return goals_str


def prepare_goals_str(dialog):
    goals = extract_user_goals(dialog)
    goals_str = make_goals_str(goals)
    return goals_str


def extract_user_goals_steps(dialog):

    def make_one_step(service, intent, act, slot=None, value=None):
        return {'service': service, 'intent': intent, 'act': act, 'slot': slot, 'value': value}

    goals = []
    for turn in dialog['turns']:
        if turn['speaker'] != 'USER':
            continue
        for frame in turn['frames']:
            service = frame['service']
            intent = frame['state']['active_intent']
            for action in frame['actions']:
                if action['act'] == 'INFORM_INTENT':
                    goals.append(make_one_step(service, intent, action['act']))
                elif action['act'] == 'INFORM':
                    goals.append(make_one_step(service, intent, action['act'], action['slot'], action['values'][0]))
                elif action['act'] == 'REQUEST':
                    goals.append(make_one_step(service, intent, action['act'], action['slot']))
    
    return goals


def print_user_goals_steps(goals):
    for step in goals:
        service, intent, act, slot, value = step['service'], step['intent'], step['act'], step['slot'], step['value']
        cprint(f"{service}: {intent} ", 'red', force_color=True, end='')
        cprint(f"{act} ", 'yellow', force_color=True, end='')
        if step['act'] == 'INFORM':
            print(f"{slot} = {value}")
        elif step['act'] == 'REQUEST':
            print(f"{slot}")
        else:
            print()


def make_goals_str_steps(goals):

    def make_one_step(service, intent, act, slot=None, value=None):
        return {'service': service, 'intent': intent, 'act': act, 'slot': slot, 'value': value}

    goals_2 = []
    current_intent = None
    for step in goals:
        service, intent, act, slot, value = step['service'], step['intent'], step['act'], step['slot'], step['value']
        if act in ['INFORM', 'REQUEST'] and current_intent != intent:
            goals_2.append(make_one_step(service, intent, 'INFORM_INTENT'))
        current_intent = intent
        goals_2.append(step)
    goals = goals_2


    goals_str = []
    for step in goals:
        service, intent, act, slot, value = step['service'], step['intent'], step['act'], step['slot'], step['value']

        intent_dict = {it['name']: it for it in schemas[service]['intents']}
        slot_dict = {slot['name']: slot for slot in schemas[service]['slots']}

        if act == 'INFORM_INTENT':
            intent_desc = intent_dict[intent]['description']
            intent_desc = intent_desc[0].lower() + intent_desc[1:]
            goals_str.append(f'The user wants to {intent_desc}.')

        elif act == 'INFORM':
            slot_desc = slot_dict[slot]['description']
            slot_desc = slot_desc[0].lower() + slot_desc[1:]
            goals_str.append(f'The user will inform the AI Assistant that the {slot_desc} is {value}.')

        elif act == 'REQUEST':
            slot_desc = slot_dict[slot]['description']
            slot_desc = slot_desc[0].lower() + slot_desc[1:]
            goals_str.append(f'The user wants to ask the AI Assistant to know the {slot_desc}.')
        
    goals_str = '\n'.join(goals_str).strip()
    return goals_str


def prepare_goals_str_steps(dialog):
    goals = extract_user_goals_steps(dialog)
    goals_str = make_goals_str_steps(goals)
    return goals_str


def make_dialog_str(dialog):
    role_map = {'USER': 'User', 'SYSTEM': 'AI Assistant'}
    dialog_str = [role_map[turn['speaker']] + ': ' + turn['utterance'] for turn in dialog['turns']]
    dialog_str = '\n'.join(dialog_str)
    return dialog_str


class SgdUser(BaseUser):

    @staticmethod
    def get_fisrt_user_utter(dialog):
        fisrt_user_utter = None
        for turn in dialog['turns']:
            if turn['speaker'] == 'USER':
                fisrt_user_utter = turn['utterance']
                break
        return fisrt_user_utter

    @staticmethod
    def make_prompt(dialog, history, agent_utter):
        goals_str = prepare_goals_str(dialog)

        # goals_str = prepare_goals_str_steps(dialog)

        dialog_str = make_dialog_str(dialog)
        history_str = '\n'.join(history)

        prompt = TEMPLATE.format(user_goals=goals_str, ref_dialog=dialog_str, 
                                 history=history_str, input=agent_utter)
        return prompt
