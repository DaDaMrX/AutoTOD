import argparse
import json
import random

from langchain.chains import LLMChain
from langchain.llms import OpenAI
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
import openai
import tenacity

from client import MyOpenAI
from utils import OPENAI_API_KEY, prepare_goals_string, tenacity_retry_log


TEMPLATE = '''You are a dialogue simulator where you act as a user to talk to an AI assistant to complete some tasks.

You should carefully read and understand the User Goals below, then talk with the AI Assistant and gradually express the intents in the goals. Your purpose is to let the user achieve the goals as much as possible.  

Note that the AI Assistant is not perfect. It may make various mistakes, including ignoring the user's requests, executing the wrong instructions, forgetting early conversation content, etc. The user you play should talk to the AI Assistant as patiently as possible, remind him to correct when you find that the AI assistant made a mistake, and complete the task as much as possible.

When asking some information of a venue (restaurant, hotel, attraction) or a train, the user should specify the name or train id he chooses.

When the dialogue goals are completed or are not been completed, the user will output "Dialogue Ends" to indicate the end of the dialogue. The user doesn't need to try conditions other than the dialogue goals.

The user has a clear goal in mind, so he does not need to ask the AI assistant that "Is there anything else I need to know?".

The user does not need to talk too much with the AI assistant. If the task goals are completed, please end the conversation as soon as possible.

There is also a reference dialogue example to achieve the goals. The simulator user may learn from the language style and dialogue strategy. The final simulated dialogue style should be similar to the reference dialogue style. 


User Goals:

{user_goals}

Reference dialogue:

{ref_dialog}

Current conversation:
{{history}}
AI Assistant: {{input}}
User:'''


def prepare_user_simulator(dialog, model):
    # Prepare User Goals
    goals = prepare_goals_string(dialog['goal']['message'])

    # Prepare Reference Dailogue
    ref_dialog = []
    for i, turn in enumerate(dialog['log']):
        role = 'User' if i % 2 == 0 else 'AI Assistant'
        utter = role + ': ' + turn['text']
        ref_dialog.append(utter)
    ref_dialog = '\n'.join(ref_dialog)

    # First User Utter
    fisrt_user_utter = dialog['log'][0]['text']

    # LLM
    assert model.startswith('text-davinci-') or model.startswith('gpt-3.5-')
    if model.startswith('text-davinci-'):
        llm = OpenAI(
            model_name=model,
            temperature=0,
            max_tokens=-1,
            openai_api_key=OPENAI_API_KEY,
            request_timeout=10,
        )
    else:
        llm = MyOpenAI(
            model_name=model,
            temperature=0,
            # max_tokens=-1,
            openai_api_key=OPENAI_API_KEY,
            request_timeout=10,
        )

    # Prompt & Chain
    template = TEMPLATE.format(
        user_goals=goals,
        ref_dialog=ref_dialog,
    )

    class ConversationBufferMemoryStrip(ConversationBufferMemory):
        def _get_input_output(self, inputs, outputs):
            input_str, output_str = super()._get_input_output(inputs, outputs)
            return input_str.strip(), output_str.strip()

    memory = ConversationBufferMemoryStrip(
        human_prefix='AI Assistant',
        ai_prefix='User',
        memory_key='history',
    )
    user = LLMChain(
        prompt=PromptTemplate.from_template(template),
        llm=llm, 
        memory=memory,
        # verbose=True, 
    )

    return user, fisrt_user_utter


def run(user_simulator, fisrt_user_utter):
    print(f'==================== Turn 1 ====================', end='\n\n')
    # First user utter: use fixed instead AI generated
    user_simulator.memory.chat_memory.add_ai_message(fisrt_user_utter)
    print(f'User: {fisrt_user_utter}', end='\n\n')

    sys_utter = input('AI Assistant: ').strip()
    print()

    turn_idx = 2
    while sys_utter not in ['exit', 'e']:
        print(f'==================== Turn {turn_idx} ====================', end='\n\n')

        # User
        user_utter = user_simulator.predict(input=sys_utter, stop='AI Assistant')
        user_utter = user_utter.strip()
        print(f'User: {user_utter}', end='\n\n')

        if 'Dialogue Ends' in user_utter:
            break

        # Agent
        sys_utter = input('AI Assistant: ').strip()
        print()

        turn_idx += 1


class User:

    def __init__(self, dialog, model):
        self.user_simulator, self.fisrt_user_utter = prepare_user_simulator(dialog, model)
        self.turn_idx = 0

    def __call__(self, sys_utter, callbacks=None):
        self.turn_idx += 1

        if self.turn_idx == 1:  # First user utter: use fixed instead AI generated
            assert sys_utter in ['', None]
            self.user_simulator.memory.chat_memory.add_ai_message(self.fisrt_user_utter)
            return self.fisrt_user_utter

        retrying = tenacity.Retrying(wait=tenacity.wait_exponential(min=2, max=60),
                                     stop=tenacity.stop_after_attempt(8),
                                     before_sleep=tenacity_retry_log,
                                     retry=tenacity.retry_if_exception_type(openai.OpenAIError))
        user_utter = retrying(self.user_simulator.predict, input=sys_utter, stop='AI Assistant', callbacks=callbacks)

        user_utter = user_utter.strip()
        return user_utter


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_path', default='MultiWOZ_2.1/data.json')
    parser.add_argument('--id', '-i', default='random')
    args = parser.parse_args()

    with open(args.data_path) as f:
        data = json.load(f)

    if args.id == 'random':
        dialog_id = random.choice(list(data.keys()))
    else:
        dialog_id = args.id if args.id.endswith('.json') else args.id + '.json'
    print(f'Dialog Id: {dialog_id}', end='\n\n')

    dialog = data[dialog_id]

    goals = prepare_goals_string(dialog['goal']['message'])
    print(f'User Goals:')
    print(goals, end='\n\n')

    user_simulator, fisrt_user_utter = prepare_user_simulator(dialog)
    run(user_simulator, fisrt_user_utter)
