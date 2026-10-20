# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

""" This file process a general LLMON file by parsing it, checking for errors. """

import json
import sys
from llmon.lang.json.llmon_converters import LLMONConverters
from llmon.lang.common.errors import LLMONError
from llmon.lang.common.utils import define_parser

def main():
    parser = define_parser(description="Parse a LLMON file. Code can be modified for usage-specific actions. Default is to output an identical LLMON file.")

    parser.add_argument("-eot", 
                        "--expect_openend_tags",
                        help="Convert assuming the passed LLMON file will have openend tagname and <|close|> token", 
                        action="store_true")  # when flag is on set arg to true
    
    args = parser.parse_args()

    no_openend = True
    if args.expect_openend_tags:
        no_openend = False

    llmon_data = args.input.read() # reads full file into a single string, looks good, has newlines
    l = LLMONConverters(converting_to_json=False, 
                        with_mellea=False,
                        keys_as_item=False,
                        no_expand_tags=False,
                        no_composite_tags=True,
                        no_openend_tagname=no_openend
                        )
    try:
        output = l.process_llmon_str(llmon_data)
    except LLMONError as exc:
        sys.exit(str(exc))
    args.output.write(output)

    pass

