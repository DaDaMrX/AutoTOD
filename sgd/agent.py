from functools import partial

from termcolor import cprint

from base_agent import BaseAgent
from sgd.function_schema import make_function_schemas
from sgd.functions import sgd_function
from sgd.utils import load_schemas

TEMPLATE = '''You are an intelligent AI Assistant to help the user complete complex tasks. There are many services to fulfill the user's goals. Each service consists of mulitple functions that the AI Assistant can call. The AI Assistant can choose to call a function in order to provide information or make a transaction for the user.

The functions are divided into query function and transaction function. The query function will return the records in the database that meets the conditions, and the transaction function will return the corresponding reference number if calling successfully.

Today is 2019-03-01, Friday. (This Saturady is 2019-03-02. This Sunday is 2019-03-03.)
When specifying an data type parameter without given full date, prefix as "2019-03-xx".

{services_info}

# Remember

- Don't make assumptions about what values to plug into functions. Ask for clarification if any parameter is missing or ambiguous.
- Before calling a transaction function for the user, such as transfer money and book restaurants, the AI Assistant MUST show all the function parameters and confirm with the user.
- You must not call the same function with the same parameters again and again.
- When finishing the user's goals, saying goodby to the user and finish the dialogue.
'''

SERVICE_TEMPLATE = '''# Service: {service_name}

## Description

{service_desc}

## Functions

{functions_info}
'''


EXAMPLE = '''
# Service: Buses_1

## Description

Book bus journeys from the biggest bus network in the country.

## Functions

- FindBus: Find a bus journey for a given pair of cities. (Query function)
- BuyBusTicket: Buy tickets for a bus journey. (Transaction function)
'''
            

class SgdAgent(BaseAgent):

    def __init__(self, model_name, service_names, callbacks=[]):
        self.service_names = service_names
        self.sgd_schemas = load_schemas()
        assert all(name in self.sgd_schemas for name in service_names)

        super().__init__(model_name, callbacks)

    def make_system_prompt(self):
        services_info = []
        for service_name in self.service_names:
            service_schema = self.sgd_schemas[service_name]
            functions_info = []
            for intent in service_schema['intents']:
                func_info = f'- {service_name}_{intent["name"]}: {intent["description"]}.'
                if not intent['is_transactional']:
                    func_info += ' (Query function)'
                else:
                    func_info += ' (Transaction function)'
                functions_info.append(func_info)
            functions_info = '\n'.join(functions_info)
            service_info = SERVICE_TEMPLATE.format(service_name=service_name, 
                                                service_desc=service_schema['description'],
                                                functions_info=functions_info)
            services_info.append(service_info)
        services_info = '\n\n'.join(services_info)

        prompt = TEMPLATE.format(services_info=services_info)
        return prompt

    def make_function_schemas(self):
        functions = make_function_schemas(self.service_names)
        return functions

    def make_function_map(self):
        function_map = {}
        for service_name in self.service_names:
            service_schema = self.sgd_schemas[service_name]
            for intent in service_schema['intents']:
                intent_name = intent['name']
                func_name = f'{service_name}_{intent_name}'
                func = partial(sgd_function, service_name=service_name, intent_name=intent_name)
                function_map[func_name] = func
        return function_map
    
    def fix_function_call(self, function_call):
        if not isinstance(function_call, dict):
            return function_call
        if 'name' not in function_call:
            return function_call
        
        name = function_call['name']
        if (fixed_name := name.split('.')[-1]) != name:
            cprint(f'Fix function name: {name} => {fixed_name}', 'yellow')
            function_call['name'] = fixed_name

        return function_call
