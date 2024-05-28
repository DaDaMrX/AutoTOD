import json

import openai
import tenacity

from utils import OPENAI_API_KEY, tenacity_retry_log

openai.api_key = OPENAI_API_KEY


GREEN_COLOR = '\u001b[1;32m'
MAG_COLOR = '\u001b[1;35m'
CYAN_COLOR = '\u001b[1;36m'
RESET_COLOR = '\u001b[0m'


class BaseAgent:

    def __init__(self, model_name, callbacks=[], **kwargs):
        self.model_name = model_name
        self.callbacks = callbacks
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.turn_idx = 0

        self.system_prompt = self.make_system_prompt()
        self.messages = [{'role': 'system', 'content': self.system_prompt}]

        self.functions = self.make_function_schemas()
        self.function_map = self.make_function_map()

    def make_system_prompt(self):
        pass

    def make_function_schemas(self):
        pass

    def make_function_map(self):
        pass

    @tenacity.retry(wait=tenacity.wait_exponential(min=2, max=60),
                    stop=tenacity.stop_after_attempt(8),
                    reraise=True,
                    before_sleep=tenacity_retry_log,
                    retry=tenacity.retry_if_exception_type(openai.OpenAIError))
    def chat(self, messages, extra_openai_args={}):
        completion = openai.ChatCompletion.create(
            model=self.model_name,
            temperature=0,
            messages=messages,
            functions=self.functions,
            request_timeout=10,
            **extra_openai_args,
        )

        for callback in self.callbacks:
            callback.on_llm_end(completion)

        return completion['choices'][0]['message']

    def __call__(self, user_utter):
        self.turn_idx += 1
        self.messages.append({'role': 'user', 'content': user_utter})
        last_function_call = None
        extra_openai_args = {}
        while True:
            msg = self.chat(self.messages, extra_openai_args)
            # BUG: no assistant message

            # Assistant Response
            if msg['content'] is not None:
                agent_utter = msg['content']

                for callback in self.callbacks:
                    agent_utter = callback.on_turn_end(agent_utter, self.turn_idx)

                return agent_utter.strip()

            # Avoid repeat function calling
            if msg.get('function_call') == last_function_call:
                print('Repeat function calling. Force AI Assistant to repsonse.')
                extra_openai_args = {'function_call': 'none'}
                continue
            last_function_call = msg.get('function_call')
            
            # Function calling Error
            function_call = msg.get('function_call')
            function_call = self.fix_function_call(function_call)
            passed, check_msg = self.check_function_call(function_call)
            if not passed:
                print()
                print('Function parsing error:')
                print(f'function_call: {msg.get("function_call")}')
                result = check_msg
                if msg.get('function_call') and msg['function_call'].get('name'):
                    name = msg['function_call']['name']
                else:
                    name = None
                print('Result: ' + CYAN_COLOR + f'{result}' + RESET_COLOR)
                self.messages.append({'role': 'function', 'name': name, 'content': result})
                continue

            # Function calling Right
            function_call = msg['function_call']
            name, args = function_call['name'], function_call['arguments']
            func = self.function_map[name]
            args = json.loads(args)
            result = func(**args)
            print()
            print('Function: ' + MAG_COLOR + f'{name}' + RESET_COLOR)
            print('Arguments: ' + GREEN_COLOR + f'{args}' + RESET_COLOR)
            for callback in self.callbacks:
                callback.on_function_call_end(name, args, result)

            print('Result: ' + CYAN_COLOR + f'{result}' + RESET_COLOR)
            self.messages.append({'role': 'function', 'name': name, 'content': result})

    def __call__deprecated(self, user_utter):
        self.turn_idx += 1
        self.messages.append({'role': 'user', 'content': user_utter})
        while True:
            msg = self.chat(self.messages)

            # Assistant Response
            if msg['content'] is not None:
                agent_utter = msg['content']

                for callback in self.callbacks:
                    agent_utter = callback.on_turn_end(agent_utter, self.turn_idx)

                return agent_utter.strip()
            
            # Function calling
            function_call = msg.get('function_call')
            function_call = self.fix_function_call(function_call)
            passed, check_msg = self.check_function_call(function_call)
            if passed:
                function_call = msg['function_call']
                name, args = function_call['name'], function_call['arguments']
                func = self.function_map[name]
                args = json.loads(args)
                result = func(**args)
                print()
                print('Function: ' + MAG_COLOR + f'{name}' + RESET_COLOR)
                print('Arguments: ' + GREEN_COLOR + f'{args}' + RESET_COLOR)
                for callback in self.callbacks:
                    callback.on_function_call_end(name, args, result)
            else:
                print()
                print('Function parsing error:')
                print(f'function_call: {msg.get("function_call")}')
                print(check_msg)
                result = check_msg
                if msg.get('function_call') and msg['function_call'].get('name'):
                    name = msg['function_call']['name']
                else:
                    name = None

            print('Result: ' + CYAN_COLOR + f'{result}' + RESET_COLOR)
            self.messages.append({'role': 'function', 'name': name, 'content': result})

    def fix_function_call(self, function_call):
        return function_call

    def check_function_call(self, function_call):
        if function_call is None:
            return False, 'No "function_call" provided.'
        if 'name' not in function_call:
            return False, 'No function name provided.'
        if 'arguments' not in function_call:
            return False, 'No function arguments provided.'
        
        name, args = function_call['name'], function_call['arguments']

        if name not in self.function_map:
            return False, f'Function {name} does not exist and cannot be called.'
        
        try:
            args = json.loads(args)
        except json.JSONDecodeError as e:
            return False, f'Invalid json parameters with the exception {e.__class__.__name__}: {e}.'

        func_schema_dict = {func['name']: func for func in self.functions}
        func_schema = func_schema_dict[name]

        if error_args := [arg for arg in args if arg not in func_schema['parameters']['properties']]:
            error_args_str = ', '.join(f'"{x}"' for x in error_args)
            right_args_str = ', '.join(f'"{x}"' for x in func_schema['parameters']['properties'])
            return False, f'Parameters {error_args_str} are not valid. Please provide the valid parameters {right_args_str}.'
        
        elif missing_args := [arg for arg in func_schema['parameters']['required'] if arg not in args]:
            args_str = ', '.join(f'"{x}"' for x in missing_args)
            return False,  f'The required parameters {args_str} are missing.'
        
        return True, 'succeed'
