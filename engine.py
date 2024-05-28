import json
import random

from langchain.callbacks.base import BaseCallbackHandler
from termcolor import cprint

from agent import Agent
from func_agent import FuncAgent
from user import User
from utils import (AGENT_COLOR, DOMAINS, HEADER_COLOR, HEADER_WIDTH,
                   RESET_COLOR, USER_COLOR, calc_openai_cost)


def run_with_user_agent(user, agent, max_iter=15):
    logs = []
    agent_utter = None
    for turn_idx in range(1, max_iter + 1):
        print('=' * 50 + f' Turn {turn_idx} ' + '=' * 50, end='\n\n')
        
        user_utter = user(agent_utter)
        cprint(f'User: {user_utter}', color='blue', attrs=['bold'], force_color=True, end='\n\n')

        if 'dialogue ends' in user_utter.lower():
            break

        agent_utter = agent(user_utter)
        cprint(f'AI Assistant: {agent_utter}', color='yellow', attrs=['bold'], force_color=True, end='\n\n')

        logs.append({'turn_idx': turn_idx, 'user': user_utter, 'agent': agent_utter})

    return logs


def transform_dialog(dialog):
    goals = {d: dialog['goal'][d] for d in DOMAINS if dialog['goal'].get(d)}

    dialog_refer = []
    turn_idx = 0
    for i, turn in enumerate(dialog['log']):
        if i % 2 == 0:
            turn_idx += 1
            log_turn = {
                'turn_idx': turn_idx,
                'user': turn['text'],
                'agent': None,
            }
        else:
            log_turn['agent'] = turn['text']
            dialog_refer.append(log_turn)

    return goals, dialog['goal']['message'], dialog_refer


class CostHandler(BaseCallbackHandler):

    def __init__(self):
        self.cost = 0.0

    def on_llm_end(self, response, **kwargs):
        cost = calc_openai_cost(response.llm_output['model_name'], response.llm_output['token_usage'])
        self.cost += cost


class AgentUtterTrimHandler:

    def __init__(self, patterns, turn_threshold, verbose=True):
        self.turn_threshold = turn_threshold
        self.patterns = patterns
        self.verbose = verbose

    def on_turn_end(self, utter, turn_idx):
        if turn_idx < self.turn_threshold:
            return utter
        for pattern in self.patterns:
            if pattern in utter:
                p = utter.find(pattern)
                agent_utter_new = utter[:p].strip()
                if self.verbose:
                    print(f'AgentUtterTrimmerHandler: Agent utter trimmed.')
                    print(f'Before: {utter}')
                    print(f' After: {agent_utter_new}')
                return agent_utter_new
        else:
            return utter


def run(dialog, agent_type, model=None, agent_model=None, user_model=None, log_file=None, max_iter=15):
    final_user_model = user_model if user_model else model
    user = User(dialog, model=final_user_model)

    final_sys_model = agent_model if agent_model else model
    if agent_type == 'func':
        assert final_sys_model.startswith('gpt-3.5-turbo-0613')
        agent = FuncAgent(model=final_sys_model)
    else:
        agent = Agent(model=final_sys_model)

    cost_handler = CostHandler()
    trim = AgentUtterTrimHandler(
        patterns=['\nSure! I can help you with that.',
                  '\nSure, I can help you with that.',],
        turn_threshold=3,
        verbose=True,
    )

    logs = []
    turn_idx = 1
    sys_utter = None
    finish_status = None
    for turn_idx in range(1, max_iter + 1):
        print(HEADER_COLOR + '=' * HEADER_WIDTH + f' Turn {turn_idx} ' + '=' * HEADER_WIDTH + RESET_COLOR, end='\n\n')
        
        user_utter = user(sys_utter, callbacks=[cost_handler])
        print(USER_COLOR + f'User: {user_utter}' + RESET_COLOR, end='\n')

        if 'dialogue ends' in user_utter.lower():
            finish_status = 'dialogue ends'
            break

        callbacks = [cost_handler]
        if agent_type == 'func':
            callbacks.append(trim)
        sys_utter = agent(user_utter, callbacks=callbacks)
        print()
        print(AGENT_COLOR + f'AI Assistant: {sys_utter}' + RESET_COLOR, end='\n\n')

        logs.append({
            'turn_idx': turn_idx,
            'user': user_utter,
            'agent': sys_utter,
        })

    goals, goal_messages, dialog_refer = transform_dialog(dialog)
    result = {
        'cost': cost_handler.cost,
        'dialog_pred': logs,
        'goals': goals,
        'goal_messages': goal_messages,
        'dialog_refer': dialog_refer,
        'finish_status': finish_status,
    }

    if log_file:
        with open(log_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'\nLogs are saved at "{log_file}".')

    return result


if __name__ == '__main__':
    DATA_PATH = 'MultiWOZ_2.1/data.json'

    with open(DATA_PATH) as f:
        data = json.load(f)

    dialog_id = random.choice(list(data.keys()))
    # dialog_id = 'PMUL3178.json'
    print(f'[Dialog Id] {dialog_id}', end='\n\n')
    dialog = data[dialog_id]

    run(dialog)
