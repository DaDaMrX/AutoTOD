import re
from typing import Any, Dict, Optional

from langchain import SQLDatabase, SQLDatabaseChain
from langchain.agents import AgentExecutor, ConversationalAgent, Tool
from langchain.agents.conversational.output_parser import ConvoOutputParser
from langchain.callbacks.manager import CallbackManagerForChainRun
from langchain.chains import LLMChain
from langchain.llms import OpenAI
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
import tenacity

from booking import book_hotel, book_restaurant, book_taxi, book_train
from client import MyOpenAI
from prompts import AGENT_TEMPLATE, DB_TEMPLATE_DICT
from utils import (AGENT_COLOR, DB_PATH, HEADER_COLOR, HEADER_WIDTH,
                   OPENAI_API_KEY, RESET_COLOR, USER_COLOR, tenacity_retry_log)


class SQLDatabaseChainWithCleanSQL(SQLDatabaseChain):

    def _call(
        self,
        inputs: Dict[str, Any],
        run_manager: Optional[CallbackManagerForChainRun] = None,
    ) -> Dict[str, Any]:
        _run_manager = run_manager or CallbackManagerForChainRun.get_noop_manager()
        input_text = f"{inputs[self.input_key]}\nSQLQuery:"
        _run_manager.on_text(input_text, verbose=self.verbose)
        # If not present, then defaults to None which is all tables.
        table_names_to_use = inputs.get("table_names_to_use")
        table_info = self.database.get_table_info(table_names=table_names_to_use)
        llm_inputs = {
            "input": input_text,
            "top_k": self.top_k,
            "dialect": self.database.dialect,
            "table_info": table_info,
            "stop": ["\nSQLResult:"],
        }
        intermediate_steps = []
        sql_cmd = self.llm_chain.predict(
            callbacks=_run_manager.get_child(), **llm_inputs
        )
        sql_cmd = self.clean_sql(sql_cmd)  # NOTE: This is the new line
        intermediate_steps.append(sql_cmd)
        _run_manager.on_text(sql_cmd, color="green", verbose=self.verbose)
        result = self.database.run(sql_cmd)
        intermediate_steps.append(result)
        _run_manager.on_text("\nSQLResult: ", verbose=self.verbose)
        _run_manager.on_text(result, color="yellow", verbose=self.verbose)
        # If return direct, we just set the final result equal to the sql query
        if self.return_direct:
            final_result = result
        else:
            _run_manager.on_text("\nAnswer:", verbose=self.verbose)
            input_text += f"{sql_cmd}\nSQLResult: {result}\nAnswer:"
            llm_inputs["input"] = input_text
            final_result = self.llm_chain.predict(
                callbacks=_run_manager.get_child(), **llm_inputs
            )
            _run_manager.on_text(final_result, color="green", verbose=self.verbose)
        chain_result: Dict[str, Any] = {self.output_key: final_result}
        if self.return_intermediate_steps:
            chain_result["intermediate_steps"] = intermediate_steps
        return chain_result
    
    def clean_sql(self, sql_cmd):
        sql_cmd = sql_cmd.strip()
        if sql_cmd[0] == '"' and sql_cmd[-1] == '"':
            sql_cmd = sql_cmd[1:-1]
        if sql_cmd[0] == "'" and sql_cmd[-1] == "'":
            sql_cmd = sql_cmd[1:-1]

        sql_cmd = re.sub(r"name (=|LIKE) '(.*'.*)'", r'name \1 "\2"', sql_cmd)
        return sql_cmd


class Agent:

    def __init__(self, model='gpt-3.5-turbo-0301'):
        self.model = model
        self.agent_executor = None
        self.reset()

    def reset(self):
        self.agent_executor = self.prepare_agent_executor(self.model)

    @staticmethod
    def prepare_db_tools(llm):
        DB_URI = f'sqlite:///{DB_PATH}'

        def prepare_db_one_tool(domain, name):
            db = SQLDatabase.from_uri(
                database_uri=DB_URI,
                include_tables=[domain],
                sample_rows_in_table_info=5,
            )
            db_prompt = PromptTemplate.from_template(DB_TEMPLATE_DICT[domain])
            sql_chain = SQLDatabaseChainWithCleanSQL.from_llm(
                db=db, llm=llm, prompt=db_prompt, top_k=10, verbose=True)
            tool = Tool(func=sql_chain.run, name=name, description='')
            return tool
        
        # domain (table, prompt), name
        db_info = [
            ('restaurant', 'Restaurant Query'),
            ('hotel', 'Hotel Query'),
            ('attraction', 'Attraction Query'),
            ('train', 'Train Query'),
        ]
        tools = []
        for domain, name in db_info:
            if domain not in DB_TEMPLATE_DICT:  # TODO
                continue
            tool = prepare_db_one_tool(domain, name)
            tools.append(tool)

        return tools

    @staticmethod
    def prepare_book_tools():
        tools = [
            Tool(func=book_restaurant, name='Restaurant Reservation', description=''),
            Tool(func=book_hotel, name='Hotel Reservation', description=''),
            Tool(func=book_train, name='Train Tickets Purchase', description=''),
            Tool(func=book_taxi, name='Taxi Reservation', description=''),
        ]
        return tools

    @staticmethod
    def prepare_agent_executor(model):
        # LLM
        assert model.startswith('text-davinci-') or model.startswith('gpt-3.5-')
        if model.startswith('text-davinci-'):
            llm = OpenAI(
                model_name=model,
                temperature=0,
                max_tokens=-1,
                openai_api_key=OPENAI_API_KEY,
            )
        else:
            llm = MyOpenAI(
                model_name=model,
                temperature=0,
                # max_tokens=-1,
                openai_api_key=OPENAI_API_KEY,
            )

        # Tools
        tools = []
        tools += Agent.prepare_db_tools(llm)
        tools += Agent.prepare_book_tools()

        # Agent
        HUMAN_PREFIX = 'Human'
        AI_PREFIX = 'AI Assistant'

        prompt_temp = PromptTemplate.from_template(AGENT_TEMPLATE)
        llm_chain = LLMChain(
            llm=llm,
            prompt=prompt_temp,
        )
        agent = ConversationalAgent(
            llm_chain=llm_chain,
            ai_prefix=AI_PREFIX,
            output_parser=ConvoOutputParser(ai_prefix=AI_PREFIX)
        )

        memory = ConversationBufferMemory(
            human_prefix=HUMAN_PREFIX,
            ai_prefix=AI_PREFIX,
            memory_key='chat_history',
        )
        agent_executor = AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=tools,
            memory=memory,
            max_iterations=5,
            verbose=True,
        )
        return agent_executor

    def __call__(self, user_utter, callbacks=None):
        agent_utter = self.agent_executor.run(user_utter, callbacks=callbacks)
        agent_utter.strip()
        return agent_utter
    
    def run(self):
        self.reset()

        turn_idx = 1
        while True:
            print(HEADER_COLOR + '=' * HEADER_WIDTH + f' Turn {turn_idx} ' + '=' * HEADER_WIDTH + RESET_COLOR, end='\n\n')

            # User
            print(USER_COLOR + f'User: ', end='')
            user_input = input('User: ').strip()
            if user_input in ['exit', 'e']:
                break
            print(USER_COLOR + f'{user_input}' + RESET_COLOR, end='\n')

            # Agent
            agent_utter = self(user_input)
            print()
            print(AGENT_COLOR + f'AI Assistant: {agent_utter}' + AGENT_COLOR, end='\n\n')

            turn_idx += 1


if __name__ == '__main__':
    agent = Agent(model='gpt-3.5-turbo-0301')
    agent.run()
