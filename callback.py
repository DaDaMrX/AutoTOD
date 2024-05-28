from utils import calc_openai_cost


class BaseCallback:

    def on_llm_end(self, completion, **kwargs):
        pass

    def on_turn_end(self, utter, turn_idx, **kwargs):
        return utter
    
    def on_function_call_end(self, function_name, args, result, **kwargs):
        pass


class CostCallback(BaseCallback):

    def __init__(self):
        self.cost = 0.0

    def on_llm_end(self, completion):
        cost = calc_openai_cost(completion['model'], completion['usage'])
        self.cost += cost


class AgentUtterTrimCallback(BaseCallback):

    def __init__(self, patterns='default', turn_threshold=3, verbose=True):
        if patterns == 'default':
            self.patterns = ['\nSure! I can help you with that.',
                             '\nSure, I can help you with that.',]
        else:
            self.patterns = patterns
        self.turn_threshold = turn_threshold
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
        

class FunctionCallCollectCallback(BaseCallback):

    def __init__(self):
        self.callings = []

    def on_function_call_end(self, function_name, args, result):
        self.callings.append({'name': function_name, 'args': args, 'result': result})
