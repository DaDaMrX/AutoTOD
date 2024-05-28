import random
import sqlite3

from sgd.utils import INFO_DB_PATH, TRANS_DB_PATH, load_schemas

schemas = load_schemas()


def sgd_function_check(service_name, intent_name, args):
    if service_name not in schemas:
        return False, f'Service "{service_name}" does not exist.'
    schema = schemas[service_name]

    intent_dict = {intent['name']: intent for intent in schema['intents']}
    if intent_name not in intent_dict:
        return False, f'Service "{service_name}" does not have the intent "{intent_name}".'
    intent = intent_dict[intent_name]
    
    if missing_args := [arg for arg in intent['required_slots'] if arg not in args]:
        args_str = ', '.join(f'"{x}"' for x in missing_args)
        return False,  f'The required parameters {args_str} are missing.'
    
    if error_args := [arg for arg in args if arg not in intent['required_slots'] + list(intent['optional_slots'].keys())]:
        error_args_str = ', '.join(f'"{x}"' for x in error_args)
        required_args_str = ', '.join(f'"{x}"' for x in intent['required_slots'])
        optional_args_str = ', '.join(f'"{x}"' for x in intent['optional_slots'])
        msg = f'Parameters {error_args_str} are not valid. Please provide valid parameters.'
        msg += f' Required parameters {required_args_str} and optional parameters {optional_args_str}.'
        return False, msg
    
    return True, 'ok'


def make_table_string(cursor, max_items=5, max_chars=500):
    records = cursor.fetchall()

    if len(records) == 0:
        return 'No results found.'

    result = []
    n_chars = 0

    line = '| ' + ' | '.join(desc[0] for desc in cursor.description) + ' |'
    n_chars += len(line) + 1
    result.append(line)

    line = '| ' + ' | '.join(['---'] * len(cursor.description)) + ' |'
    n_chars += len(line) + 1
    result.append(line)

    for i, record in enumerate(records, start=1):
        line = '| ' + ' | '.join(str(v) for v in record) + ' |'
        n_chars += len(line) + 1
        if i > 0 and n_chars <= max_chars and i <= max_items:
            result.append(line)
        else:
            n_left = len(records) - i + 1
            result.append(f'\n{n_left} more records ...')
            break

    result = '\n'.join(result)
    return result


def sgd_function_info(service_name, intent, args, db_path=INFO_DB_PATH):
    fields = ', '.join(f'"{field}"' for field in intent['result_slots'])
    sql = f'SELECT {fields} FROM {service_name}'
    if args:
        conditions = ' AND '.join(f'"{k}" = "{v}"' for k, v in args.items())
        sql += f' WHERE {conditions}'

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql)
    except Exception as e:
        return f'SQL failed: {e.__class__.__name__}: {e}'

    return make_table_string(cursor)


def sgd_function_trans(service_name, args, db_path=TRANS_DB_PATH):

    def generate_reference_num():
        # genereate a 8 character long reference number with random lower letters and numbers
        return ''.join(random.choice('abcdefghijklmnopqrstuvwxyz0123456789') for i in range(8))
    
    refer_number = generate_reference_num()
    args['refer_number'] = refer_number

    fields = ', '.join(f'"{field}"' for field in args.keys())
    value_syms = ', '.join(['?'] * len(args))
    sql = f'INSERT INTO {service_name}_Transaction ({fields}) VALUES ({value_syms})'

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(sql, list(args.values()))
    except Exception as e:
        return f'SQL failed: {e.__class__.__name__}: {e}'
    conn.commit()
    cursor.close()
    conn.close()

    return f'Transaction succeed. The reference number is {refer_number}.'


def sgd_function(service_name, intent_name,
                 info_db_path=INFO_DB_PATH, trans_db_path=TRANS_DB_PATH,
                 **kwargs):
    passed, msg = sgd_function_check(service_name, intent_name, kwargs)
    if not passed:
        return msg

    schema = schemas[service_name]

    intent_dict = {intent['name']: intent for intent in schema['intents']}
    intent = intent_dict[intent_name]

    # sql_args = kwargs.copy()
    # for arg, default_value in intent['optional_slots'].items():
    #     if arg not in sql_args and default_value != 'dontcare':
    #         sql_args[arg] = default_value

    if not intent['is_transactional']:
        return sgd_function_info(service_name, intent, kwargs, info_db_path)
    else:
        return sgd_function_trans(service_name, kwargs, trans_db_path)
    
