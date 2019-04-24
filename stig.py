'''
Filename: stig.py
Python Tested With: 3.7.3
Author: Cody Dostal (cody@dostal.co)
Based on work by: Nicholas Russo (njrusmc@gmail.com)
Description: Performs a fast but imperfect scan of Cisco IOS configuration
             files against specific rule sets corresponding to the STIGs
             specified in the file. The tool provides a variety of outputs
             available depending on user preference. The tool does NOT yet
             create a standard STIG checklist .ckl file (XCCDF) and only
             outputs plain text or CSV.
'''
from os import path
from glob import glob
import argparse
import sys
import yaml
from ciscoconfparse import CiscoConfParse

## Global Variables
version = "0.0.1"

def print_rule_result(rule_data, rule_result, verbosity=0):
    '''
    Print the test result to stdout based on verbosity:
      0: One line per rule showing the vuln ID, description, and result
      1: Verbose output showing all rule info, including pass/fail objects
      2: CSV format, one rule per line, including pass/fail objects

    The rule_data parameter was read in from the YAML rule file, and the
    rule_result parameter is a dictionary containing the results of the test.
    '''
    if verbosity == 0:
        print('{0: <10} {1: <62} {2}'.format(
            rule_data['vuln_id'], rule_data['desc'], rule_result['success']))
    elif verbosity == 1:
        print('----------------------------------------------------------------------')
        print('Vuln ID:     {}'.format(rule_data['vuln_id']))
        print('Severity:    {}'.format(rule_data['severity']))
        print('Description: {}'.format(rule_data['desc']))
        for k, v in rule_result['iter'].items():
            print('{0} objects:'.format(k))
            for obj in v:
                print('  - {}'.format(obj.text))
        print('Success:     {}'.format(rule_result['success']))
    elif verbosity == 2:
        csv_str = '{0},{1},{2},{3}'.format(
            rule_data['vuln_id'], rule_data['severity'],
            rule_data['desc'], rule_result['success'])
        for k, v in rule_result['iter'].items():
            str_list = [line.text for line in v]
            csv_str += ',' + '~'.join(str_list)
        print(csv_str)

def check(parse, rule):
    '''
    Wrapper function that determines whether the text to check has
    parents (hierarchical check) or has no parents (global check).
    '''
    if rule['check']['parent']:
        return _check_hier(parse, rule)
    return _check_global(parse, rule)

def _check_global(parse, rule):
    '''
    Finds all objects matching the search text, then counts the number of
    times the text was found in global config. If the match count equals
    the specified text_cnt, the test succeeds and the objects matched
    are considered pass objectives. Otherwise, the test fails and the
    objects matched are considered fail objects.

    Note that the "when" condition is never evaluated here.
    '''
    objs = parse.find_objects(rule['check']['text'])
    if len(objs) == rule['check']['text_cnt']:
        success = 'PASS'
        pass_objs = objs
        fail_objs = []
    else:
        success = 'FAIL'
        pass_objs = []
        fail_objs = objs
    return {'success': success, 'iter': {'pass': pass_objs, 'fail': fail_objs, 'na': []}}

def _check_hier(parse, rule):
    '''
    Get all subjects under the specified parent from the rule data. If
    "when" is a boolean True then the test is always performed. If "when" is
    a string, it is treated as a search regex to look for other child elements
    before running the test. For example, proxy-ARP disabled is only relevant
    when the interface has an IP address, so "ip(backslash)s+address" is a
    valid "when" condition.

    Similar to the global check, parents that have properly matching children
    are added to the pass list, and those that lack the proper match string
    are added to the fail list. Not applicable list contains elements where
    "when" was false (interfaces that don't have IPs don't care about whether
    proxy-ARP is enabled).
    '''
    pass_objs = []
    fail_objs = []
    na_objs = []
    parents = parse.find_objects(rule['check']['parent'])

    for parent in parents:
        when = isinstance(rule['check']['when'], bool) and rule['check']['when']
        if when or parent.re_search_children(rule['check']['when']):
            search = parent.re_search_children(rule['check']['text'])
            if len(search) == rule['check']['text_cnt']:
                pass_objs.append(parent)
            else:
                fail_objs.append(parent)
        else:
            na_objs.append(parent)

    if fail_objs:
        success = 'FAIL'
    elif na_objs and not pass_objs:
        success = 'N/A'
    else:
        success = 'PASS'
    return {'iter':{'pass': pass_objs, 'fail': fail_objs, 'na': na_objs}, 'success': success}

def process_args():
    '''
    Process command line arguments using argparse. The positional argument
    "config_file" is mandatory and specifies the file to scan. There are two
    optional arguments. --verbosity changes the format of the stdout
    output as the program runs. The default verbosity is 0, the most brief.
    --failonly is used to reduce output and only print failing rules.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument('config_file', help='configuration text file to scan',
                        type=str)
    parser.add_argument("-v", "--verbosity", type=int, choices=[0, 1, 2],
                        help="0 for brief, 1 for details, 2 for CSV rows", default=0)
    parser.add_argument("-f", "--failonly", help="print failures only", action="store_true")
    return parser.parse_args()

def main():
    '''
    Program entrypoint.
    '''

    # Process CLI arguments
    args = process_args()

    # Parse the config file and store as variable
    parse = CiscoConfParse(args.config_file)

    # Determine what STIGs a specific config should be compared against.
    # Note that multiple STIGs can be specified for a single config, and
    # if a bogus STIG is specified, nothing happens.
    stig_objs = parse.find_objects(r'!@#stig:\S+')
    stigs = [obj.text.split(':')[1] for obj in stig_objs]

    # Determine the Vendor: Cisco, Juniper, or Brocade
    # Only the first 'vendor' directive is honored.
    vendor_objs = parse.find_objects(r'!@#vendor:\S+')
    vendor = vendor_objs[0].text.split(':')[1]

    # Determine the network OS type: ios, xr, nxos, asa
    # Only the first 'type' directive is honored.
    os_type_objs = parse.find_objects(r'!@#type:\S+')
    os_type = os_type_objs[0].text.split(':')[1]

    # Find all the rules files and iterate over them
    rule_files = sorted(glob('rules/{}/{}/*.yml'.format(vendor, os_type)))
    fail_cnt = 0
    for rule_file in rule_files:
        with open(rule_file, 'r') as stream:
            try:
                # Load the YAML data from file into memory for processing
                rule_data = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)

            # Find out if the rule is needed. Basically find out
            # if the STIGs specified in a rule file overlap with the
            # STIGs specified in a config. Only one match is needed.
            overlap = [v for v in stigs if v in rule_data['part_of_stig']]
            if not overlap:
                continue

            # Rather than specify the vuln ID in each vuln file, which
            # is a waste of time, dynamically update the rule data with
            # the vuln file name.
            vuln_str = path.basename(rule_file).split('.')[0]
            rule_data.update({'vuln_id': vuln_str})

            # Perform the rule checking and print the output with
            # the user-supplied verbosity. Always print failing rules,
            # but only print passing/NA rules when failonly is not set.
            rule_result = check(parse, rule_data)
            if rule_result['success'] == 'FAIL':
                fail_cnt += 1
                print_rule_result(rule_data, rule_result, args.verbosity)
            elif not args.failonly:
                print_rule_result(rule_data, rule_result, args.verbosity)

    # Provide the number of failed rules back to the invoking process.
    sys.exit(fail_cnt)

if __name__ == '__main__':
    main()
