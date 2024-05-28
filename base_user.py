import openai
import tenacity

from utils import tenacity_retry_log


class BaseUser:

    def __init__(self, dialog, model_name, user_name='User', agent_name='AI Assistant', callbacks=[], **kwargs):
        self.dialog = dialog
        self.model_name = model_name
        self.user_name = user_name
        self.agent_name = agent_name
        self.callbacks = callbacks
        for k, v in kwargs.items():
            setattr(self, k, v)

        self.history = []
        self.turn_idx = 0

        self.prompt = self.make_prompt(self.dialog, self.history, '')

    def add_user_utter(self, user_utter):
        self.history.append(f'{self.user_name}: {user_utter}')

    def add_agent_utter(self, agent_utter):
        self.history.append(f'{self.agent_name}: {agent_utter}')

    def __call__(self, agent_utter):
        self.turn_idx += 1

        if self.turn_idx == 1:
            assert agent_utter in ['', None]
            user_utter = self.get_fisrt_user_utter(self.dialog)
            self.add_user_utter(user_utter)
        else:
            user_utter = self.run_model(agent_utter)
            self.add_agent_utter(agent_utter)
            self.add_user_utter(user_utter)

        for callback in self.callbacks:
            user_utter = callback.on_turn_end(user_utter, self.turn_idx)

        return user_utter

    @tenacity.retry(wait=tenacity.wait_exponential(min=2, max=60),
                    stop=tenacity.stop_after_attempt(8),
                    reraise=True,
                    before_sleep=tenacity_retry_log,
                    retry=tenacity.retry_if_exception_type(openai.OpenAIError))
    def run_model(self, agent_utter):
        self.prompt = self.make_prompt(self.dialog, self.history, agent_utter)

        completion = openai.ChatCompletion.create(
            model=self.model_name,
            temperature=0,
            messages=[{'role': 'user', 'content': self.prompt}],
            request_timeout=10,
        )

        for callback in self.callbacks:
            callback.on_llm_end(completion)

        user_utter = completion['choices'][0]['message']['content']

        stop_span = f'{self.agent_name}:'
        if stop_span in user_utter:
            p = user_utter.find(stop_span)
            user_utter = user_utter[:p]

        return user_utter.strip()
    
    @staticmethod
    def get_fisrt_user_utter(dialog):
        pass
    
    @staticmethod
    def make_prompt(dialog, history, agent_utter):
        pass
