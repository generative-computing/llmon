# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from llmon.lang.json.llmon_converters import LLMONConverters
from llmon.lang.common.errors import LLMONError
from llmon.lang.common.utils import define_parser

def check_arg_compatibility(args):
    if args.keys_as_item and not args.no_expand_tags:  # expanding tags only makes sense if we are using keys for tags
        print(f"** ERROR ** Can't expand tag names if keys are not being used in tags. \nPlease set command line options to avoid this situation.")
        print(f"Options requested")
        default = lambda x : "Default" if x == True else "Not Default"
        print(f"   No Expand Tags: {args.no_expand_tags} ({default(args.no_expand_tags)})")

        print(f"   Use item for keys rather than tags: {args.keys_as_item}")
        print(f" Valid combinations are")
        print(f"\t1) default ")
        print(f"\t2) --no_expand_tags (-net)")
        print(f"\t3) --keys_as_item (-kai) --no_expand_tags (-net)")
        exit()

    if args.compact and args.formatted:
        print(f"** ERROR ** Can't have both --compact and --formatted command line options.")
        exit()

def main():
    parser = define_parser(description="Convert a JSON file to a LLMON file.  (If --keys_as_item is set, --no_expand_tags must be chosen.)")

    # optional boolean flag to specify how keys in key/value pairs should be represented
    parser.add_argument("-ntt", 
                        "--no_type_tags_for_literals", 
                        help="Do NOT add LLMON type tags when translating from JSON non-string literals", 
                        action="store_true")  # when flag is on set arg to true, Default is false
    
    # optional boolean flag to specify how keys in key/value pairs should be represented
    parser.add_argument("-kai", 
                        "--keys_as_item", 
                        help="Generate JSON dictionary keys as separate LLMON item tags",
                        action="store_true")  # when flag is on set arg to true, Default is false
    
    # optional boolean flag to specify if nested tags are explicitly expanded
    parser.add_argument("-net", 
                        "--no_expand_tags", 
                        help="Do NOT automatically expand nested tags to have explicit names", 
                        action="store_true")  # when flag is on set arg to true, Default is false

    # optional boolean flag to specify if array instances have tags created for them
    parser.add_argument("-nai", 
                        "--name_array_instances", 
                        help="Automatically create tags for each array instance", 
                        action="store_true")  # when flag is on set arg to true, Default is false        
    
    # optional boolean flag to specify if generated LLMON should have no white space, most appropriate for sending to LLMs
    parser.add_argument("-compact", 
                        "--compact", 
                        help="Generated LLMON should have no white space", 
                        action="store_true")  # when flag is on set arg to true, Default is false  

    # optional boolean flag to specify if generated LLMON should be formatted, useful for humans, but not LLLMs
    parser.add_argument("-formatted", 
                        "--formatted", 
                        help="Generated LLMON that is nicely formatted for human consumption", 
                        action="store_true")  # when flag is on set arg to true, Default is false    

    # optional boolean flag to specify if generated LLMON should have tags for composite types
    parser.add_argument("-gct", 
                        "--generate_composite_tags", 
                        help="Generated LLMON should have tags for OBJECT or LIST AND tag name and CLOSE token for OPEN_END section.", 
                        action="store_true")  # when flag is on set arg to true, Default is false     
    
    # optional boolean flag to specify if open_end sections do need a tagname and CLOSE special token
    parser.add_argument("-got", 
                        "--generate_openend_tagname", 
                        help="Generated LLMON should have tag name and CLOSE token for OPEN_END section.", 
                        action="store_true")  # when flag is on set arg to true, Default is false    
    
    # optional boolean flag to specify if we should generate tag blocks for nested user tags
    parser.add_argument("-gnt", 
                        "--generate_nested_tagnames", 
                        help="Generated LLMON should include nested tag names even if they only contain content that is nested within them.", 
                        action="store_true")  # when flag is on set arg to true, Default it is false  
    
    args = parser.parse_args()

    check_arg_compatibility(args)

    compact_flag = True   # compact is the default unless formatted command line option is requested
    if args.formatted:
        compact_flag = False

    gen_ct = False
    gen_ot = False
    if args.generate_composite_tags:
        gen_ct = True
        gen_ot = True # Generate composite tags will imply also generating openend tagnames

    if args.generate_openend_tagname:
        gen_ot = True

    json_data = json.load(args.input)
    l = LLMONConverters(converting_to_json=False,  # not converting to JSON
                        with_mellea=False,   # not using Mellea
                        no_type_tags_for_literals=args.no_type_tags_for_literals,
                        keys_as_item=args.keys_as_item,  # key/value keys should be default as soft types
                        no_expand_tags=args.no_expand_tags, # expand tags is the default
                        name_array_instances=args.name_array_instances,
                        compact=compact_flag,
                        no_composite_tags= not gen_ct,
                        no_openend_tagname= not gen_ot,
                        generate_nested_tagnames=args.generate_nested_tagnames
    )

    try:
        output = l.json_dict_to_llmon_str(json_data) # process data recursively producing a list of LLMON tokens
    except LLMONError as exc:
        sys.exit(str(exc))
    args.output.write(output)

    pass

