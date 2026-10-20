# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import sys
import re
import llmon.lang.common.common as C   # contains common definitions
from llmon.lang.common.errors import LLMONError
from llmon.lang.common.utils import define_parser

""" This file converts a LLMON file into a corresponding Shorthand file """

class LLMON_to_Shorthand_Converter:

    def __init__(self, interesting_tags: list[str], no_close_tag:bool):
        self._interesting_tags = interesting_tags
        self._no_close_tag = no_close_tag
        self._processing_special_tag = False
        self._tag_type = ""
        self._processing_key = False
        self._nest_stack: list[str] = [] # a list/stack of strings that captures the current tagnames at each nesting level
        self._current_tagname = ""    # The current tagname being constructed
        self._processing_tagname = False  # True when we are in the opening scope of a tagname

    #########  Nest stack methods
    def _empty_nest_stack(self) -> bool:
        return len(self._nest_stack) == 0

    def _push_nest_stack(self, item: str):
        self._nest_stack.append(item)

    def _pop_nest_stack(self) -> str:
        if self._empty_nest_stack():
            raise LLMONError("** Logic error: trying to pop empty nest stack")
        return self._nest_stack.pop()

    def set_processing_key(self, val: bool):
        self._processing_key = val

    def _get_next_token(self, line: str):
        """
        This function takes in a string and return all text up to the next LLMON token, if one exists in the string, or end of string, 
        If the string starts with a LLMON token, it returns that.
        """

        min_idx = sys.maxsize
        min_tag = ""
        for t in self._interesting_tags:
            idx = line.find(t)
            if idx != -1 :
                if idx < min_idx :
                    min_idx = idx
                    min_tag = t

        if min_idx == 0 : # is tag at first position?
            return min_tag, line[len(min_tag):]
        else :
            if min_idx != sys.maxsize : # did we find a tag elsewhere
                return line[:min_idx], line[min_idx:]
            else : # no tags, so return full line
                return line, ""

    def _map_to_shorthand(self, token: str, line_num: int):
        """
        Tags are surrounded by an opening and closing delimiter. See OPEN_TAG & CLOSE_TAG and OPEN_END_TAG & CLOSE_TAG.
        However, although we use a single CLOSE_TAG to close both OPEN_TAG and OPEN_END_TAG, the Shorthand syntax distinguished these 2 different types of closes.
        Thus, when we see CLOSE_TAG we need to know if we are currently processing a tag and if so, which kind (OPEN or OPEN_END). Since tags
        can span multiple lines, this "state" of the processing needs to go beyond this function (hence the parameters and return values).
        We use a single intance variable tag_type to signify this state. It has 3 possible values
            "" empty string: not currently processing any tag, i.e., we are in free text land
            OPEN_TAG: signifies we are processing an open tag
            OPEN_END_TAG: signifies we are processing an end tag

        We also need to handle self-closing tags, which are of the form "\\" tag name "/". This is handled in the one of the else clauses below.

        """

        if token == C.OPEN_TAG:
            if self._tag_type != "":
                raise LLMONError(f"**  ERROR, line {line_num}, found {token}, but was expecting something else, tag_type: {self._tag_type}. \nCould this be a LLMON file without closing tagnames?")
            self._tag_type = C.OPEN_TAG
            self._processing_tagname = True
            return "\\"
        elif token == C.OPEN_END_TAG:
            if self._no_close_tag:
                self._tag_type = ""
                tagname = self._pop_nest_stack()
                return "/" + tagname + "/"
            else:
                if self._tag_type != "":
                    raise LLMONError(f"**  ERROR, line {line_num}, found {token}, but was expecting something else, tag_type: {self._tag_type}. \nCould this be a LLMON file without closing tagnames?")
                self._tag_type = C.OPEN_END_TAG
                return "/"
        elif token == C.CLOSE_TAG:
            if self._no_close_tag:
                if self._tag_type != C.OPEN_TAG:
                    raise LLMONError(f"** Logic error, line {line_num}, found close of OPEN_END tag, which was not expected with -et option")
                self._processing_tagname = False
                self._push_nest_stack(self._current_tagname)
                self._current_tagname = ""
                self._tag_type = ""
                return "\\"
            else:
                if self._tag_type == C.OPEN_TAG:
                    self._tag_type = ""
                    return "\\"
                elif self._tag_type  == C.OPEN_END_TAG:
                    self._tag_type = ""
                    return "/"
                else:
                    raise LLMONError(f"** ERROR, line {line_num}, found {token}, but there was nothing to close")
        elif token == C.SELF_CLOSE_TAG:
            if self._tag_type != C.OPEN_TAG:
                raise LLMONError(f"** ERROR, line {line_num}, found {token}, tag_type : {self._tag_type }")
            self._processing_tagname = False
            self._current_tagname = ""   # clear out tagname for next one
            self._tag_type = ""
            return "/"
        elif token == C.DOT_JOINER_TAG:         # DOT tag only allowed inside of tag
            if self._tag_type != C.OPEN_TAG and self._tag_type != C.OPEN_END_TAG:
                raise LLMONError(f"** ERROR, line {line_num}, found {token}, but it wasn't inside an open or open_end tag")
            return "."
        elif token == C.COLON_SEPARATOR_TAG:  # COLON special token (ie. <|:|>) can be used inside a tag or it can be used for dictionary items
            if self._tag_type  == C.OPEN_TAG or self._tag_type  == C.OPEN_END_TAG: # If COLON used inside a tag, we return ":",
                return ":"
            else:
                if self._processing_key:   # If we were processsing a key and are outside of tag space then the COLON_SEPARATOR signifies that they key processing is done, so we set to false
                    self._processing_key = False
                return ":"          # We are in regular text, let's return as ":" (common case) and
                                    # force escaping at LLMON side if they want to have : as part of the key
                                    #  [keeping as separate case to help understand logic, in case we change our minds later]
        elif token == C.LIST_SEPARATOR_TAG:
            return ","
        else:
            raise LLMONError(f"** LOGIC ERROR, line {line_num}")

    def _get_reference_name(self, token: str, string1: str, string2: str, line_num: int) -> str:
        """ 
            This function is given 2 strings representing what we've generated so far (in Shorthand) and what still needs to be processed as input (in LLMON)
            It needs to find the reference, which is at the end of the first string and the beginning of the second string
            For example, we could have
               string1 ==> "\\exec:x.instr\\instr:"
               string2 ==> "b<|open_end|>exec<|:|>x<|.|>instr<|close|>"
            We need to capture the "instr" and the "b" and return "instr:b"
        """

        if token != C.COLON_SEPARATOR_TAG:
            raise LLMONError(f"** Internal Logic Error, line {line_num} in get_reference_name")

        # include all chars starting from the end up until you see the end of a LLMON token or whitespace
        split_list = re.split(r"[\\ ]", string1)  # reference tags will always follow open tags, so we look for the close open tag symbol in Shorthand
        prefix_string = split_list[-1].strip().lstrip(":") # take the last item and strip out white space and then a leading ":", which can happen when we have \: (rare)

        # include all chars starting from the beginning of string2 up until you see the beginning of a LLMON token or whitespace
        split_list = string2.split()  # split on space
        suffix_string = split_list[0]
        idx = suffix_string.find("<")  # check for LLMON tokens that are immediately adjacent to end of suffix, i.e.,  a:x<|open_end|> ...
        if idx != -1:
            suffix_string = suffix_string[:idx] # remove the surplus LLMON token and whatever follows

        return prefix_string + ":" + suffix_string
    
    def _adjust_for_reference(self, ref_name: str, output: str, rest: str, line_num: int):
        """
            This function is called when we've just seen a sequence that needs to be mapped back to LLMON as  \\ref\\  xxx /ref/  pattern
            In addition to inserting that pattern, it will also adjust the output and rest variables appropriately.
            Parms:
            ref_name: a reference of the form X:Y, where X & Y can be non-white space strings
            output: generated output so far for this line (in Shorthand)
            rest: rest of input stream to process for this line (in LLMON)
        """
        # decompose ref_name to prefix and suffix
        idx = ref_name.find(":")
        prefix = ref_name[:idx]
        suffix = ref_name[idx+1:]  # need extra 1 for the ":""

        # remove prefix from the end of output and add in the ref pattern
        if not output.endswith(prefix):
            raise LLMONError(f"** Logic Error in adjust_for_reference, line {line_num}")
        output = output[:output.rfind(prefix)]

        # check if we need to add leading space before \ref\ to avoid generating \\ sequence
        if len(output) != 0 and output[-1] == "\\":
            output = output + " \\ref\\ " + ref_name + " /ref/"
        else:
            output = output + "\\ref\\ " + ref_name + " /ref/"

        # remove suffix from rest 
        if not rest.startswith(suffix):
            raise LLMONError(f"** Unexpected input, line {line_num}: \"{prefix[1:]}\". If this is a LLMON file with openend tag names, use -eot option")
        rest = rest[len(suffix):]

        # check if we need a trailing space, which is needed if next char will map into a "/"
        if len(rest) != 0 and rest[0] == C.CLOSE_TAG:  
            output += " "

        return output, rest

    def _adjust_for_escape_chars(self, input:str) -> str:
        """ Scan string passed and add escape chars where needed, such as for / or \
        """
        out = ""
        for c in input:
            if c == '\\':
                out += "\\\\"  # This is \\
            elif c == '/':
                out += "\\/"   # This is \/
            else:
                out += c

        return out

    def process_line(self, line: str, line_num : int):
        """
        LLMON tags are similar versions of Shorthand tags, just more verbose.
            Constant Name in common.py    |  LLMON Current mapping   |  Shorthand Current mapping
            _____________________________________________________________________________________________
            OPEN_TAG                      |     <|open|>             |      \\ (*)
            OPEN_END_TAG                  |     <|open_end|>         |      /
            CLOSE_TAG                     |     <|close|>            |     \\ (*) or / [depending on context]
            SELF_CLOSE_TAG                |     <|self_close|>       |      /
            DOT_JOINER_TAG                |     <|.|>                |      .
            COLON_SEPARATOR_TAG           |     <|:|>                |      :
            LIST_SEPARATOR_TAG            |     <|list-separator|>   |      ,

            * - just single backslash, pydantic is making me type 2

        Examples:
            The LLMON string
                <|open|>email<|close|> this is the content of an email.|open_end|>email<|close|>
            will look like this in Shorthand
                \\email\\ this is the content of an email./email/
        """

        output = ""
        rest = line
        while rest != "" :
            # call a funtion that takes in string and returns all text up to the next LLMON token, or, if next char is a LLMON special token, return it
            token, rest = self._get_next_token(rest)
            if self._tag_type == C.OPEN_TAG and token == C.ITEM_NAME:  # Set a flag if we are about to process a key name, so we can escape the use of colon in the key name
                self._processing_key = True

            if C.is_llmon_tag(token):
                # Need to look for <|:|> outside a tag and not while processing any item
                #    and if found add the \ref\ xxx /ref/ pattern
                if token == C.COLON_SEPARATOR_TAG and self._tag_type == "" and not self._processing_key:  
                    reference_name = self._get_reference_name(token, output, rest, line_num)   # get the reference name
                    output, rest = self._adjust_for_reference(reference_name, output, rest, line_num)  # insert \ref\ .. /ref/ pattern adjust output and rest s
                else:
                    result = self._map_to_shorthand(token, line_num)
                    output += result

                # Check for things that are part of tagname
                if self._processing_tagname and (token == C.COLON_SEPARATOR_TAG or token == C.DOT_JOINER_TAG):
                    self._current_tagname += self._map_to_shorthand(token, line_num)

            elif self._processing_key and token == ":":   # If we see a : in the key name, we need to escape it
                output += "\\:"
            else:
                if self._processing_tagname:
                    self._current_tagname = self._current_tagname + token
                token = self._adjust_for_escape_chars(token)
                output += token
        return output

## BEGIN MAIN

def main():
    parser = define_parser(description="Convert a LLMON file to a Shorthand file.")

    # optional boolean flag to support LLMON files with no closing tagname
    parser.add_argument("-noot",
                        "--no_openend_tags", 
                        help="Convert assuming the passed LLMON file will not have openend tagname and <|close|> token", 
                        action="store_true")  # when flag is on set arg to true

    parser.add_argument("-eot", 
                        "--expect_openend_tags",
                        help="Convert assuming the passed LLMON file will have openend tagname and <|close|> token", 
                        action="store_true")  # when flag is on set arg to true
    
    args = parser.parse_args()
      
    interesting_tags = C.LLMON_TAGS.copy()
    interesting_tags.add(":")   # we need to add ":" to the list of interesting tags bc we need to escape it when it appears in the name of an item (i.e. key/value pair) key

    no_openend = True
    if args.expect_openend_tags:
        no_openend = False
    m2l = LLMON_to_Shorthand_Converter(interesting_tags, no_openend)

    output   = ""
    m2l.set_processing_key(False)  # boolean that keeps track of if we are processing a key/value key
    line     = args.input.readline() # reads a line
    line_num = 1
    while line:
        try:
            res = m2l.process_line(line, line_num)
        except LLMONError as exc:
            sys.exit(str(exc))
        output += res
        args.output.write(output)
        output = ""
        line = args.input.readline() # reads a line
        line_num += 1
        m2l.set_processing_key(False)  # key names can't span multiple lines

    pass
