from callback import AgentUtterTrimCallback, CostCallback, FunctionCallCollectCallback
from engine import run_with_user_agent
from sgd.agent import SgdAgent
from sgd.user import SgdUser


def run(dialog, model_name='gpt-3.5-turbo-0613', max_iter=15, save_prompts=False):
    cost_callback = CostCallback()
    trim_callback = AgentUtterTrimCallback()
    func_callback = FunctionCallCollectCallback()

    user = SgdUser(dialog, model_name, callbacks=[cost_callback])
    agent = SgdAgent(model_name, dialog['services'], callbacks=[cost_callback, trim_callback, func_callback])

    if save_prompts:
        with open('agent_prompt.txt', 'w') as f:
            f.write(agent.system_prompt)
        with open('user_prompt.txt', 'w') as f:
            f.write(user.prompt)

    logs = run_with_user_agent(user, agent, max_iter=max_iter)
    return logs, cost_callback.cost, func_callback.callings
