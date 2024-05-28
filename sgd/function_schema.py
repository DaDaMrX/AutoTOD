from collections import defaultdict
import os
from sgd.utils import load_schemas, load_dialogs

schemas = load_schemas()


def collect_db_records(dialogs):
    tables = defaultdict(list)

    for dialog in dialogs.values():
        for turn in dialog['turns']:
            if turn['speaker'] != 'SYSTEM':
                continue
            for frame in turn['frames']:
                if 'service_results' not in frame:
                    continue
                for result in frame['service_results']:
                    tables[frame['service']].append(result)

    # Deduplication
    for name, table in tables.items():
        d = {str(a): a for a in table}
        tables[name] = list(d.values())

    return tables


def detect_field_data_type(tables):

    def is_float(string):
        try:
            float(string)
            return True
        except ValueError:
            return False

    field_data_type = defaultdict(dict)  # service -> field -> type
    for service_name, schema in schemas.items():
        table = tables[service_name]
        for slot in schema['slots']:
            field = slot['name']
            if all(item[field].isdigit() for item in table if field in item):
                field_data_type[service_name][field] = 'integer'
            elif all(is_float(item[field]) for item in table if field in item):
                field_data_type[service_name][field] = 'number'
            elif all(item[field].lower() in ['true', 'false'] for item in table if field in item):
                field_data_type[service_name][field] = 'boolean'
            else:
                field_data_type[service_name][field] = 'string'

    return field_data_type


def get_field_data_type():
    # file_name = os.path.basename(__file__)
    # print(f'{file_name}: Temporary load dialogs for detecting field type.')
    dialogs = load_dialogs()
    tables = collect_db_records(dialogs)
    field_data_type = detect_field_data_type(tables)
    return field_data_type


field_data_type = get_field_data_type()


def make_one_function_schema(service_schema, intent_name):
    service_name = service_schema['service_name']
    intent_dict = {intent['name']: intent for intent in service_schema['intents']}
    intent = intent_dict[intent_name]

    func_schema = {
        'name': f'{service_name}_{intent_name}',
        'description': None,
        'parameters': {
            'type': 'object',
            'properties': {},
            'required': intent['required_slots'].copy(),
        }
    }

    desc = intent['description'] + '.'
    if not intent['is_transactional']:
        desc += ' (Query function. Return db recored that meets conditions.)'
    else:
        desc += ' (Transaction function. Return a reference number when calling succeeds.)'
    func_schema['description'] = desc

    slot_dict = {slot['name']: slot for slot in service_schema['slots']}
    for slot_name in intent['required_slots'] + list(intent['optional_slots'].keys()):
        slot = slot_dict[slot_name]

        # Apply data type
        service_name = service_schema['service_name']
        if slot_name in field_data_type[service_name]:
            field_type = field_data_type[service_name][slot_name]
        elif set(slot['possible_values']) == {'True', 'False'}:
            field_type = 'boolean'
        else:
            field_type = 'string'

        type_func = {'string': str, 'integer': int, 'number': float, 'boolean': lambda s: s.lower() == 'true'}
        if slot['possible_values']:
            possible_values = list(map(type_func[field_type], slot['possible_values']))
            

        # Schema
        property_schema = {
            'type': field_type,
            'description': slot['description'],
        }

        if slot['possible_values'] and field_type != 'boolean':
            if slot['is_categorical']:
                property_schema['enum'] = possible_values
            else:
                property_schema['examples'] = possible_values

        func_schema['parameters']['properties'][slot_name] = property_schema

    return func_schema


def make_function_schemas(service_name_list):
    functions = []
    for service_name in service_name_list:
        service_schema = schemas[service_name]
        for intent in service_schema['intents']:
            func_schema = make_one_function_schema(service_schema, intent['name'])
            functions.append(func_schema)
    return functions
