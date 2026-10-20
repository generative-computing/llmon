# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import sys

def stdout_or_file(name):
    if name == "-":
        return sys.stdout
    return open(name, "w")

def stdin_or_file(name):
    if name == "-":
        return sys.stdin
    return open(name, "r")

def define_parser(description):
    parser = argparse.ArgumentParser(description)

    parser.add_argument("input",
                        metavar = "FILE",
                        type    = stdin_or_file,
                        nargs   = "?",
                        const   = sys.stdin,
                        default = sys.stdin,
                        help    = "Read from FILE; use '-' or omit to read from stdin")

    parser.add_argument("output",
                        metavar = "FILE",
                        type    = stdout_or_file,
                        nargs   = "?",
                        const   = sys.stdout,
                        default = sys.stdout,
                        help    = "Write output to FILE; use '-' or omit to write to stdout")
    return parser
