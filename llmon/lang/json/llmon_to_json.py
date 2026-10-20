# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from llmon.lang.json.llmon_converters import LLMONConverters
from llmon.lang.common.errors import LLMONError
from llmon.lang.common.utils import define_parser

def main():
    parser = define_parser(description="Convert a LLMON file to a JSON file.")

    # optional boolean flag to specify how keys in key/value pairs should be represented
    parser.add_argument("-kai", 
                        "--keys_as_item", 
                        help="Expect object item to have \\item\\ key : value /item/ format", 
                        action="store_true")  # when flag is on set arg to true, otherwise it is false
    
    # optional boolean flag to specify if nested tags were explicitly expanded during conversion from JSON to LLMON, 
    # and thus, should be unexpanded on the translation back to JSON 
    parser.add_argument("-net", 
                        "--no_expand_tags", 
                        help="Do NOT automatically unexpand nested tags", 
                        action="store_true")  # when flag is on set arg to true, otherwise it is false

    # optional boolean flag to specify if array instances have tags created for them
    parser.add_argument("-nai", 
                        "--name_array_instances", 
                        help="Automatically create tags for each array instance", 
                        action="store_true")  # when flag is on set arg to true, otherwise it is false        

    parser.add_argument("-eot",
                       "--expect_openend_tagnames",
                       action="store_true",
                       help="Expect LLMON to have tag name and CLOSE token for OPEN_END section")
    
    # copied from lib/python3.12/json/tool.py
    parser.add_argument('--sort-keys', action='store_true', default=False,
                        help='sort the output of dictionaries alphabetically by key')
    parser.add_argument('--no-ensure-ascii', dest='ensure_ascii', action='store_false',
                        help='disable escaping of non-ASCII characters')
    parser.add_argument('--json-lines', action='store_true', default=False,
                        help='parse input using the JSON Lines format. '
                        'Use with --no-indent or --compact to produce valid JSON Lines output.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--indent', default=4, type=int,
                       help='separate items with newlines and use this number '
                       'of spaces for indentation')
    group.add_argument('--tab', action='store_const', dest='indent',
                       const='\t', help='separate items with newlines and use '
                       'tabs for indentation')
    group.add_argument('--no-indent', action='store_const', dest='indent',
                       const=None,
                       help='separate items with spaces rather than newlines')
    group.add_argument('--compact', action='store_true',
                       help='suppress all whitespace separation (most compact)')
    
    args = parser.parse_args()

    dump_args = {
        'sort_keys': args.sort_keys,
        'indent': args.indent,
        'ensure_ascii': args.ensure_ascii,
    }
    if args.compact:
        dump_args['indent'] = None
        dump_args['separators'] = ',', ':'

    expect_openend_tagnames = False  # Default is not to expect openend tagnames
    if args.expect_openend_tagnames:
        expect_openend_tagnames = True

    llmon_data = args.input.read() # reads full file into a single string
    l = LLMONConverters(converting_to_json=True, 
                        with_mellea=False, 
                        keys_as_item=args.keys_as_item,  # key/value keys should be default as soft types
                        no_expand_tags=args.no_expand_tags, # expand tags is the default
                        name_array_instances=args.name_array_instances,
                        no_openend_tagname= not expect_openend_tagnames
                        )
    try:
        output = l.llmon_str_to_json_str(llmon_data, **dump_args)
    except LLMONError as exc:
        sys.exit(str(exc))
    args.output.write(output)
    args.output.write('\n')

    pass
