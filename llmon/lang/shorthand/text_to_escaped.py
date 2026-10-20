# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import sys
from llmon.lang.common.utils import define_parser

"""
    This file will convert a text file to one that has shorthand special chars ("/" and "\\") tags escaped, i.e., it will convert "/" to "//" and "\" to "\\".
    Note: "\\" is a single backslash, we need the extra one to escape in python :-) 
"""

def is_llmon_special_char(char: str) -> bool :
    if char == '\\' or char == '/' :
        return True
    else :
        return False

def process_line(line: str) -> str :
    """
    Look for a special char and if you find it emit 2 of them, otherwise just emit the char
    """
    output = ""
    idx = 0
    length = len(line)
    while idx < length and line[idx] != '\n' :
        ch = line[idx]
        if is_llmon_special_char(ch) :
            output = output + ch + ch
        else :
            output += ch
        idx += 1
    return output

## BEGIN MAIN

def main():
    parser = define_parser(description="Convert Shorthand special chars into escaped sequences.")

    args = parser.parse_args()

    line = args.input.readline() # reads a line
    while line:
        res = process_line(line) + '\n'
        args.output.write(res)
        line = args.input.readline()

    pass
