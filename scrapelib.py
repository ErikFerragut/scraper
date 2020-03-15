# make sure you:
#      brew cask install chromedriver  # for chrome
#      brew install geckodriver        # for firefox
#      pip install selenium

import os, itertools, datetime

import pandas as pd
import sqlalchemy

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ASSUMPTIONS:
# 1. Assumes radio button id is linked to with label having for="that_id"

################################################################################
# Generic functions -- belongs elsewhere
################################################################################
def dict_tree(adict, indent=0):
    indentation = ' ' * (3*indent)
    for key,value in adict.items():
        if isinstance(value, dict):
            print(indentation, key + ':')
            dict_tree(value, indent+1)
        else:
            print(indentation, key + ':', value)


class one_up():
    '''A way of generating new 1-up integers as needed'''
    def __init__(self, first_num=1):
        self.count = first_num - 1
    def __call__(self):
        self.count += 1
        return self.count


def hash_it(something):
    '''Given something, turn it into a string, replace whitespace with | and hash it.
    Returns the hexdigest of the md5 hash.'''
    if isinstance(something, dict):
        the_string = sorted(something.items())   # can break on nested dicts
    the_string = str(something)
    return hashlib.md5('|'.join(the_string.split()).encode()).hexdigest()


################################################################################
# Construct the product iterator for inputs
################################################################################
def to_iterator(key, type, value, options=[]):
    if type == 'const':
        assert isinstance(value, str), 'Type const must have str value'
        return [(key, value)]
    elif type == 'list':
        assert isinstance(value, list), 'Type list must have list value'
        return [(key, v) for v in value]
    elif type == 'all':
        return [(key,option) for option in options]
    elif type == 'all-but':
        assert isinstance(value, list), 'Type all-but must have list value'
        return [(key,option) for option in options if option not in value ]
    elif type == 'slice':
        from_,to_,by_ = map(int, value.split())
        return ((key,x) for x in range(from_,to_,by_))
    else:
        raise KeyError('Unknown variable range type: ' + type)
    

def form_inputs_to_input_generator(form, form_inputs):
    iterators = {
        k:to_iterator(k, v['type'], v['value'], form['inputs'][k].get('texts', []))
        for k,v in form_inputs.items()
    }

    return  map(dict, itertools.product(*iterators.values()))


################################################################################
# Scraping functionality
################################################################################
def create_new_browser(headless=True):
    options = webdriver.FirefoxOptions()
    options.headless = headless
    options.add_argument('--ignore-certificate-errors')
    options.add_argument('--test-type')
    browser = webdriver.Firefox(
        options=options,
        executable_path='/usr/local/bin/geckodriver'
    )
    return browser


def get_forms(soup, form_container='form'):
    forms = {}
    for form in soup.find_all(form_container):
        newnum = one_up()
        
        id = form.get('id') or newnum()
        form_metadata = { 'id': id }

        # process forms -- organize by id b/c name repeats over radio buttons
        inputs = {
            inp.get('id') or newnum():
            {
                'type':'text',
                'name':inp.get('name')
            }
            for inp in form.find_all('input', {'type':'text'})
        }

        # inputs[name]['options'] = {id1:text1, id2:text2, id3:text3}
        for inp in form.find_all('input', {'type':'radio'}):
            name = inp.get('name') or newnum()
            if name not in inputs:
                inputs[name] = {
                    'type':'radio', 'radio_ids':[], 'label_ids':[], 'label_texts':[]}
            rad_id = inp.get('id')
            label_ids = soup.find_all(attrs={'for':rad_id})
            assert len(label_ids) == 1, 'Broken radio button ' + rad_id
            inputs[name]['radio_ids'].append(rad_id)  # currently uses this
            inputs[name]['label_ids'].append(label_ids[0].get('id'))
            inputs[name]['label_texts'].append(label_ids[0].text)
            
        inputs.update( {
            inp.get('id') or newnum():
            {
                'type':'select',
                'name':inp.get('name'),
                'values': [ s.get('value') for s in inp.find_all('option') ],
                'texts': [ s.text for s in inp.find_all('option') ]
            }
            for inp in form.find_all('select')
        } )

        inputs.update( {
            inp.get('id') or newnum():
            {
                'type':'hidden',
                'name':inp.get('name')
            }
            for inp in form.find_all('hidden')
        } )

        form_metadata['inputs'] = inputs

        form_metadata['buttons'] = {
            button.get('id') : button.text
            for button in form.find_all('button')
        }

        forms[ id ] = form_metadata

        if newnum.count:
            print('WARNING: Form {} had {} id-less elements'.format(id, newnum.count))
            
    return forms


def fill_and_submit(browser, form, fill_with, submit_with):
    '''Takes a browser and a form (from get_forms) to be filled with
    fill_with, a dict from id to value for text and selection, but
    from name to id for radio button.  When filled, clicks the button
    whose id is given by submit_with.
    '''
    # uses value_selection AND form
    for k,v in fill_with.items():
        item_type_data = form['inputs'].get(k)
        assert item_type_data is not None, 'Unexpected key: '+k

        if item_type_data['type'] == 'text':
            text_element = browser.find_element_by_id(k)
            text_element.clear()
            text_element.send_keys(str(v))

        elif item_type_data['type'] == 'select':
            select_element = Select(browser.find_element_by_id(k))
            select_element.select_by_visible_text(v)

        elif item_type_data['type'] == 'radio':
            # at least this works for oddshark
            label_elements = browser.find_elements_by_xpath("//label[@for='{}']".format(v))
            assert len(label_elements) == 1, \
                'Value {} had {} element matches'.format(v,len(label_lements))
            label_elements[0].click()

    if 'id' in submit_with.keys():
        submit_button = browser.find_element_by_id(submit_with['id'])
    elif 'name' in submit_with.keys():
        submit_button = browser.find_element_by_name(submit_with['name'])
        
    submit_button.click()
    # if this doesn't work, do: submit_button.send_keys(Keys.ENTER)


def wait_for(browser, what_type, what_value, delay):
    '''wait_for(By.CLASS_NAME, 'table-wrapper') waits up to delay seconds
    for the class "table-wrapper" to load, returning True if it did,
    False o/w.  Can also use By.ID.
    '''    
    elt_wait_for = (what_type, what_value)
    delay = 3 # seconds
    try:
        myElem = WebDriverWait(browser, delay).until(
            EC.presence_of_element_located(elt_wait_for))
        return True
    except TimeoutException:
        return False
    

def get_tables(browser, output_table):
    '''Get tables in the browser's page source selected according to
    output_table, which is a dictionary with a select key.

    If select is 'by position' then a which key gives the integer
    (1-up) of which table to return.

    If select is 'by positions' then a which key gives the list of all
    tables to return (1-up).
    '''
    tables = pd.read_html(browser.page_source)
    
    table_select = output_table['select']
    if table_select == 'by position':
        tables = [tables[output_table['which']-1]]
    elif table_select == 'by positions':
        tables = [ tables[i-1] for i in tables[output_table['which']] ]
    elif table_select == 'flatten':
        tables = [
            pd.DataFrame([ t.columns[0] + tuple(t.values.flatten()) for t in tables ])
        ]
    else:
        raise KeyError('Unknown select "{}" for output_table'.format(table_select))

    table_names = output_table.get('table_names') or [ output_table['table_name'] ]

    return dict(zip(table_names, tables))
    
################################################################################
# Storage into Database
################################################################################
def set_status(con, ind, status, status_table='results'):
    con.engine.execute('''
        update {}
        set status = '{}',
            last_update = datetime('now')
        where id = '{}' 
    '''.format(status_table, status, ind))


def update_inputs_table(con, G):
    '''Update input table with the rows from form_inputs  (BUGGY!)'''
    inputs = pd.DataFrame(list(
        form_inputs_to_input_generator(G['form'], G['form_inputs'])))
    inputs['url'] = G['url']
    inputs['subkey'] = list(G['submit_with'].keys())[0]
    inputs['subval'] = list(G['submit_with'].values())[0]

    if 'inputs' in con.engine.table_names():  # read the existing inputs
        old_inputs = pd.read_sql('select * from inputs', con, index_col='id')
        both_inputs = pd.concat([old_inputs, inputs])
        repeats = both_inputs.duplicated(keep='first')
        is_new_input = ~repeats.values[ len(old_inputs): ]
        num_new = is_new_input.sum()
        print('Found {} of {} new inputs are not in {} old inputs'.format(
            num_new, len(inputs), len(old_inputs)))

        if num_new:
            last_index = old_inputs.index.max()
            inputs.index = pd.RangeIndex(last_index, last_index + len(inputs))
            
            print('Updating inputs table')
            inputs.to_sql(
                name='inputs',
                con=con,
                if_exists='append',
                index=True,
                index_label='id'
            )
    else:
        print('No input table present. Creating {} rows'.format(len(inputs)))
        inputs.to_sql(
            name='inputs',
            con=con,
            if_exists='append',
            index=True,
            index_label='id'
        )

    return pd.read_sql("select * from inputs", con, index_col='id')


def updated_results_table(con):
    no_status_table = pd.read_sql('inputs', con)[ ['id'] ].assign(
        status = 'not started',
        last_update = datetime.datetime.now()
    ).set_index('id')

    if 'results' in con.engine.table_names():
        actual_status = pd.read_sql('results', con, index_col='id')
        new_indices = set(no_status_table.index).difference(actual_status.index)
        append_this = no_status_table.loc[new_indices]
        print('Updating results table with {} rows'.format(len(append_this)))
    else:
        print('Creating results table')
        append_this = no_status_table

    append_this.to_sql(
        name='results',
        con=con,
        if_exists='append',
        index=True,
        index_label='id'
    )

    return pd.read_sql("select * from results where status <> 'done' and status <> 'error'",
                       con, index_col='id')


def post_table(con, table_name, table, **scrape_args):
    '''Post a table to the named output table.

    Takes table, augments with constant columns from scrape_args, and
    posts to the table called table_name accessible via connection
    con.  Also augments with a timestamp, which it returns.
    '''
    timestamp = datetime.datetime.now()
    scrape   = scrape_args.copy()
    scrape['posted'] = timestamp
    scrapes = []

    for k,v in scrape.items():
        table[k] = v

    table.to_sql(
        name = table_name,
        con = con,
        if_exists = 'append',
        index = False,
    )
