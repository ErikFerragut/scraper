import yaml, time

from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup as bs
import pandas as pd
import sqlalchemy
import click

from scrapelib import *


@click.group()
def cg():
    pass


@cg.command(help='Scan a site to grab form metadata')
@click.option('--debug', is_flag=True, help='use a visible browser')
@click.option('--url', required=True, help='site (include http(s)://) with form(s)')
@click.option('--output', help='output file to store forms meta-data')
@click.option('--form-tag', default='form', help='by defaults looks for "form" tags')
def scan(debug, url, output, form_tag):
    # If exploring -- load the page, process the forms, output what we learned
    print('comment: Creating browser')
    browser = create_new_browser(not debug) # create a browser instance and get page
    print('comment: Accessing site "{}"'.format(url))
    browser.get(url)
    soup = bs(browser.page_source, 'lxml')  # parse the page
    forms = get_forms(soup, form_tag)                 # extract forms and take the one we want
    print('comment: Writing results to', output)
    if output == 'stdout' or output is None:
        print(yaml.dump(forms))
    else:
        with open(output, 'w') as fout:
            fout.write(yaml.dump(forms))
    browser.close()
    # Use the output to figure out what range of inputs you want to scrape over
    # and put that ino the config yaml file for your scraping project


@cg.command(help='Collect results from a range of form inputs')
@click.option('--debug', is_flag=True, help='use a visible browser')
@click.option('--kth', default=1, help='process kth input of every n')
@click.option('--n', default=1, help='of every n inputs will process the kth')
@click.option('--max-to-work', default=0, help='max number of inputs to process; 0 for all')
@click.argument('config')
def scrape(debug, kth, n, config, max_to_work):
    # 1. read globals and open DB, set derivative values
    G = yaml.load(open(config), yaml.Loader)
    G['form'] = yaml.load(open(G['form_yaml']), yaml.Loader)[G['input_form_id']]
    con = sqlalchemy.create_engine(G['output_db']).connect()

    if 'form_wait' in G:
        form_wait_elt  = (By.CLASS_NAME if G['form_wait'].get('by') == 'class' else By.ID,
                          G['form_wait']['value'], G['form_wait']['delay'])
        form_throttle  = G['form_wait'].get('throttle', 0)
    else:
        form_wait_elt = (By.TAG_NAME, 'body', 10)
        form_throttle = 0
        
    if 'table_wait' in G:
        table_wait_elt = (By.CLASS_NAME if G['table_wait'].get('by') == 'class' else By.ID,
                          G['table_wait']['value'], G['table_wait']['delay'])
        table_throttle = G['table_wait'].get('throttle', 0)
        no_table_str   = G['table_wait'].get('absent_str')
    else:
        table_wait_elt = (By.TAG_NAME, 'body', 10)
        table_throttle = 0
        no_table_str   = None
        
    # 2. make sure the inputs and results tables are up to date
    inputs  = update_inputs_table(con, G)
    results = updated_results_table(con)
    results = results[ results.index % n == (kth - 1) ]

    # 3. grab a not started input from results and see its inputs
    browser = create_new_browser(not debug)     # create a browser instance
    last_url = ''
    for i, ind in enumerate(results.index):
        print('Working on input', ind, 'which is', i+1, 'of', len(results.index))
        fill_with = dict(inputs.loc[ind])
        next_url = fill_with.pop('url')
        submit_with = { fill_with.pop('subkey'): fill_with.pop('subval') }
        set_status(con, ind, 'started')


        print('Going to form page, verifying form has not changed')
        if G.get('form-on-table-page') and last_url != '':
            print('-- form is also on the table page')
        elif last_url == next_url:
            browser.back()
        else:
            browser.get(next_url)
            last_url = next_url
        assert wait_for(browser, *form_wait_elt), 'Form not loading'
        time.sleep(form_throttle)
        page_form = get_forms(bs(browser.page_source, 'lxml'))[G['input_form_id']]
        if page_form != G['form']:
            print('WARNING: page form has changed')

        print('Filling and submitting form')
        fill_and_submit(browser, G['form'], fill_with, submit_with)
        if not wait_for(browser, *table_wait_elt):
            if no_table_str in browser.page_source:
                print('No results for this one')
                set_status(con, ind, 'done')
                continue
            else:
                set_status(con, ind, 'error')
                browser.save_screenshot('debug.png')
                os.system('open debug.png')
                raise TimeoutException
        time.sleep(table_throttle)

        print('Parsing the table(s)')
        tables = get_tables(browser, G['output_table'])

        print('Posting to output table(s)')
        fill_with['url'] = G['url']
        fill_with['subkey'] = list(submit_with.keys())[0]
        fill_with['subval'] = list(submit_with.values())[0]
        fill_with['input_form_id'] = G['input_form_id']
        fill_with['input_index'] = ind
        for name, table in tables.items():
            post_table(con, name, table, **fill_with)

        print('Updating results status')
        set_status(con, ind, 'done')

        if i+1 == max_to_work:
            break

    # 4. close up
    print('Done')
    con.close()
    browser.close()

if __name__ == '__main__':
    cg()
