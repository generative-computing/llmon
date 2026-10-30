# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import sys
import os
import argparse
import llmon.lang.common.common as C   # contains common definitions
from llmon.lang.common.errors import LLMONError

"""
    This file will convert Shorthand to LLMON using a linear-time algorithm that reads and outputs a line at a time.
"""

class Shorthand_to_LLMON_Converter:

    #  These are reserved LLMON tags with special predefined semantics
    REF_TAG_NAME = "ref"      # treat the enclosed as a reference to a LLMON instance tag, such as "instr:23", the result will be to replace ":" with the <|:|>
    COMMENT_TAG_NAME = "comment"  # treat the enclosed as a comment, i.e., ignore everty that follows until /comment/ is seen

    def __init__(self, auto_expand = True):
        # Default = True, do we auto expanded nested tags to capture full nesting in their name and have terse closing structure that doesn't mention tagname and <|close|>
        self._auto_expand = auto_expand  

        self._tag_state = ""                # a 3 state variable that describes if we are processing an open tag, an open_end tag, or neither

        # A bunch of state variables that are used when auto_expand is on
        self._processing_reg_text = False # Indicates if we have crossed the threshold from tag chars to regular text
        self._processing_reg_text_before_comment = False # this will save the status of processing_reg_text before a comment is seen so it can be retrieved after comment
        self._processing_tagname = False # Indicates if we are processing tagnames
        self._processing_ref = False  # signifies if we are processing a "ref" tag
        self._processing_comment = False # signifies if we are processing a "comment" tag
        self._inside_ref_scope = False # are we currently inside the scope of a ref?
        self._emitted_open_block = False # set when we have emitted the open block text for the current free text
        self._nest_stack = [] # a list/stack of strings that captures the current tagnames at each nesting level
        self._current_tag = "" # the latest tag we are processing
        self._space_buffer = "" # this will hold any leading spaces that we may need to emit depending on what follows them

        self._processing_object_item_key = False  # state variable signifying if we are currently processing the key portion of a key/value pair, aka an object_item
        self._processing_list = False   # state variable signifying if we are currently processing a list

    def processing_reg_text(self) -> bool:
        return self._processing_reg_text

    def processing_comment(self) -> bool:
        return self._processing_comment
    
    def _is_special_char(self, char: str) -> bool:
        if char == '\\' or char == '/' or char == ":" or char == "." or char == ",":
            return True
        else :
            return False

    def _is_non_ending_backslash(self, char: str, pos: int, limit: int) -> bool:
        return char == '\\' and pos < limit

    #########  Nest stack methods
    def _empty_nest_stack(self) -> bool:
        return len(self._nest_stack) == 0

    def _push_nest_stack(self, item: str):
        self._nest_stack.append(item)

    def _pop_nest_stack(self) -> str:
        if self._empty_nest_stack():
            raise LLMONError("** Logic error: trying to pop empty nest stack")
        return self._nest_stack.pop()

    def _get_current_nest(self) -> str:
        """
            Construct a single string that includes each component in the stack
        """

        out = ""
        size = len(self._nest_stack)
        if size > 0:
            for i in range(size-1):
                out += self._nest_stack[i]
                out += C.DOT_JOINER_TAG
            out += self._nest_stack[size-1]
        return out

    def _enter_tag_nest(self, tagname: str):
        self._current_tag = tagname
        self._push_nest_stack(tagname)

    def _exit_tag_nest(self):
        tagname = self._pop_nest_stack()   # pop the last tag
        self._current_tag = ""

    def _check_for_non_output_special_tag(self, tagname: str) -> bool:
        """
            Determine if the passed tag name is something special, like a REF_TAG_NAME or COMMENT_TAG_NAME
        """
        return tagname == self.REF_TAG_NAME or tagname == self.COMMENT_TAG_NAME

    def _remove_tagname_from_output(self, output: str, tagname: str) -> str:
        return output[:-(len(tagname))]  # remove the unneeded tag

    def _set_special_tag(self, tagname: str, val: bool):
        """ Set the appropriate processing tag to the passed value """
        if tagname == self.REF_TAG_NAME:
            self._processing_ref = val  
        elif tagname == self.COMMENT_TAG_NAME:
            self._processing_comment = val
        else:
            raise LLMONError(f"** Logic error: unexpected special tagname: {tagname}")
    
    def _process_special_tag(self, output: str, tagname: str, llmon_tag: str) -> str:
        """
            Called after seeing the OPEN or OPEN_END of a special tag. We need to remove it from the end of output stream and return the result.
            llmon_tag will hold which tag it is.
        """
        if llmon_tag == C.OPEN_TAG:
            self._set_special_tag(tagname, True)
        elif llmon_tag == C.OPEN_END_TAG:
            self._set_special_tag(tagname, False)
        else:
            raise LLMONError(f"** Logic error: unexpected special tagname: {llmon_tag}")

        if not output.endswith(llmon_tag):
            raise LLMONError(f"** Logic Error in process_special_tag")
        return self._remove_tagname_from_output(output, llmon_tag)
    
    def _emit_space_buffer(self, out: str) -> str:
        """
            Called to emit the space buffer and reset it
        """
        out += self._space_buffer
        self._space_buffer = ""
        return out

    def _clear_space_buffer(self):
        """
            Called to clear the space buffer
        """
        self._space_buffer = ""
    
    def _check_for_transition_to_reg_text(self, ch: str) -> str:
        """
            Used when auto_expand is on to perform lazy emitting of open tag sequences.
            Called when a regular char is about to be emitted. Goal is to determine if this is the first such reg char and if so, to add appropriate open tag sequence.
            If auto_expand is not on, we just return the ch parameter
            However, if we are processing a comment, we have nothing to do, just ignore it and return empty string
        """

        if self._processing_comment and not self._processing_tagname:  # check if we are under a comment tag, but not processing a tagname
            return ""
        else:
            if self._auto_expand:
                out = ""
                if self._tag_state == "" and not ch.isspace():  # We don't let white space trigger the need for OPEN sequence
                    if not self._processing_reg_text:  # is this first time for reg text, i.e, the flag was set to false?
                        if not self._empty_nest_stack():
                            if not self._inside_ref_scope and not self._emitted_open_block: # don't emit open sequence for refs
                                out = C.OPEN_TAG + self._get_current_nest() + C.CLOSE_TAG
                                self._emitted_open_block = True
                        self._processing_reg_text = True  # set flag to prevent future generation for this transition
                        out = self._emit_space_buffer(out)  # found real text, so the spaces are relevant, emit them

                if not self._processing_reg_text and not self._processing_tagname and ch.isspace(): # if we see leading white space, not in tagname, we track it in space_buffer for possible future use
                    self._space_buffer += ch # save the "space" for possible use later
                    return out
                else:
                    return out + ch
            else:
                return ch

    def _check_for_transition_from_reg_text(self, ch: str) -> str:
        """
            Used when auto_expand is on to perform lazy emitting of open_end tag sequences
            Called when we are processing an OPEN or OPEN_END token to see if we just came from regular text and if so to emit the OPEN_END_TAG for previous block of text
        """
        if self._processing_reg_text and not self._empty_nest_stack() and not self._processing_comment and not self._processing_ref: # only emit the sequence if we are inside a tag scope
            return C.OPEN_END_TAG 
        else:
            return ""

    def _check_for_valid_tagname(self, tagname: str, line_num: int):
        """
            Tagname needs to be a valid identifier, but may also have LLMON connectors in it, <|:|> and <|.|>, so we test by stripping them
            Raise an error if found, otherwise just return
        """
        stripped_tagname = tagname.replace(C.DOT_JOINER_TAG,"").replace(C.COLON_SEPARATOR_TAG,"")
        if not stripped_tagname.isidentifier():
            raise LLMONError(f"** Parse Error on line {line_num}: tagname is not a valid identifier -->|{tagname}|")

    def process_line(self, line: str, line_num : int) :
        """
        Tags are surrounded by an opening and closing character, which are the same for each style of tag. Open tags use "\" and open end tags use "/" for both
        their opening and closing.
        Thus, when we see one of these characters we need to know if we are currently processing a tag and if so, which kind (open or close). Since tags
        can span multiple lines, this "state" of the processing needs to live beyond this function (hence, we use a field associated with the converter, which is like a global for a single conversion).
        We use a single instance variable tag_type to signify this state. It has 3 possible values
            "" (empty string): not currently processing any tag, i.e., we are in free text land
            OPEN_TAG: signifies we are processing an open tag
            OPEN_END_TAG: signifies we are processing an end tag

        We have similar logic for dealing with ":" related to object items; we track if we are currently processing an object item with a boolean.
        Likewise, we do the same for processing a list and dealing with "," correctly

        We also need to handle self-closing tags, which are of the form "\" tag name "/". This is handled in the one of the else clauses below.

        This function also implements auto_expanding, which does 2 things
            1 it expands tag names to include the tags from any outer nesting.
            2 it only generates OPEN and OPEN_END tag sequences when they are needed, i.e, it doesn't generate such a sequence if it's only goal is to add a nesting
                level. Instead it records that nesting level and uses it when emiting the tag name for the real content (#1) 
        
        To implement this functionality, we leverage a few data structures
            tagname: local variable to capture the tag name as it is seen

            self._current_tag: the latest tag we have seen, updated using local variable tagname
            self._nest_stack: a list/stack of strings that captures the current tagnames at each nesting level
                
            self._space_buffer: a buffer of seen white space that may be emited into output as appropriate

            The key is to generate <|open|> and <|open_end|> sequences during certain transitions, not when see the equivalent Shorthand tags.
            Here are the key transitions
                When transitioning from the Shorthand equivalent of <|open|> or <|open_end|> to regular (not tag) text ==> emit <|open|> tag sequence
                When transitioning from regular text to the Shorthand equivalent <|open|> or <|open_end|>: emit <|open_end|> tag sequence
        """

        output = ""
        tagname = ""   # a string to capture the tagname as we see it and later add it to output
        idx = 0
        while idx < len(line) and line[idx] != '\n':
            ch = line[idx]
            if self._is_special_char(ch):
                if self._is_non_ending_backslash(ch, idx+1, len(line)) and line[idx+1] == ch: # look for escape sequence on backslash, if so, just output 1 of them "\\ -> \"
                    output += self._check_for_transition_to_reg_text(ch) # returns ch (if auto_expand false) otherwise, implements auto expand logic
                    idx += 1
                elif self._is_non_ending_backslash(ch, idx+1, len(line)) and line[idx+1] == "/":   # look for "\/", which is needed when we have /, so output just /
                    output += self._check_for_transition_to_reg_text("/") # returns ch (if auto_expand false) otherwise, implements auto expand logic
                    idx += 1
                elif self._is_non_ending_backslash(ch, idx+1, len(line)) and line[idx+1] == ":":   # look for "\:", which is needed when we have : in the key part of a key/value pair, i.e., not a key/value pair separator
                    output += self._check_for_transition_to_reg_text(":") # returns ch (if auto_expand false) otherwise, implements auto expand logic
                    idx += 1
                elif ch == '\\':          # this is a single backslash
                    if self._tag_state == "":  # THIS IS THE START OF AN OPEN TAG  (state says we aren't currently processing any tags)
                        if not self._processing_comment:  # if we are inside a comment, ignore this
                            if self._auto_expand:
                                out = self._check_for_transition_from_reg_text(ch) 
                                output += out
                            else:
                                output += C.OPEN_TAG  # if we are not auto expanding, we create open tags, otherwise wait until we know we need it
                            self._tag_state = C.OPEN_TAG
                            # Before we overwrite the proc_reg_text flag, we save it in case we are about to see a comment
                            #  The code for exiting a comment scope will need to restore the state to what it was before we entered for correct processing
                            #  Since we don't yet know if we are going to see a comment we save it in all cases, but only look at it when we are exiting
                            #  a comment
                            self._processing_reg_text_before_comment = self._processing_reg_text
                            self._processing_reg_text = False  # no longer in reg text land

                            self._processing_tagname = True
                            self._clear_space_buffer()    # entering tag scope, reg text space is not needed
                    elif self._tag_state == C.OPEN_TAG: # THIS IS THE END OF AN OPEN TAG (state says we are already processing an open tag)
                        if self._processing_comment:  # if we are inside a comment, ignore this
                            self._remove_tagname_from_output(output, C.OPEN_TAG)  # remove OPEN tag from output stream since this is a comment and we don't need it
                        else:  # not inside a comment, but the open tag we are processing can be a comment
                            self._check_for_valid_tagname(tagname, line_num)  # make sure tagname is valid
                            if self._auto_expand:
                                # if we are auto expanding, we don't need to emit anything yet, but do need to record the tag nesting or set processing flag
                                if not self._check_for_non_output_special_tag(tagname): # make sure this isn't a special tag that requires any output, like \ref\ or \comment\
                                    self._enter_tag_nest(tagname)
                                    self._emitted_open_block = False # we found the end of open end for a non-ref block, so safe to reset flag
                                else:
                                    # remove the text if we didn't have any reg text before seeing this ref
                                    if not self._processing_reg_text:
                                        output = self._remove_tagname_from_output(output, C.OPEN_END_TAG)

                                    # add open sequence if this is first transition to reg text and we are in a nest
                                    if not self._empty_nest_stack() and not self._emitted_open_block and tagname != self.COMMENT_TAG_NAME:
                                        output = output + C.OPEN_TAG + self._get_current_nest() + C.CLOSE_TAG
                                        self._emitted_open_block = True
                                    self._set_special_tag(tagname, True)    # set appropripate processing flag to true

                                if tagname.endswith(C.ITEM_NAME):
                                    self._processing_object_item_key = True
                                elif tagname.endswith(C.LIST_NAME):
                                    self._processing_list = True

                                if tagname == self.REF_TAG_NAME:
                                    self._inside_ref_scope = True
                            else:
                                if self._check_for_non_output_special_tag(tagname):   # some tags don't require any output
                                    output = self._process_special_tag(output, tagname, C.OPEN_TAG)   # remove the OPEN tag from output stream
                                else:
                                    output = output + tagname + C.CLOSE_TAG   

                                    if output.endswith(C.START_OBJECT_ITEM_TAG_SUFFIX):
                                        self._processing_object_item_key = True
                                    elif output.endswith(C.START_LIST_TAG):
                                        self._processing_list = True

                        self._tag_state = ""             # mark that we are no longer within an open tag
                        tagname = ""                     # reset tagname, since we are done with this tag
                        self._processing_tagname = False
                    else:  # we are processing an open_end_tag, but now see a "\", so the open_end_tag wasn't closed, this is an error
                        if self._tag_state != C.OPEN_END_TAG:
                            raise LLMONError(f"** Unexpected tag state: {self._tag_state}, while processing \\")
                        raise LLMONError(f"** Syntax Error on line: {line_num}, expecting / and found {ch}\n ") # produce error msg
                elif ch == '/':
                    if self._tag_state == "":  # START OF AN OPEN END TAG
                        self._tag_state = C.OPEN_END_TAG
                        if self._auto_expand: # check for transition
                            output += self._check_for_transition_from_reg_text(ch) 
                            self._emitted_open_block = False # we found the end of open end for a non-ref block, so safe to reset flag
                        else:
                            output += C.OPEN_END_TAG  # if we are not auto expanding, we create open end tag
                        self._clear_space_buffer()  # entering tag scope, reg text space is no needed
                        self._processing_tagname = True
                        self._processing_reg_text = False  # no longer in reg text land
                    elif self._tag_state == C.OPEN_END_TAG: # FOUND END OF AN OPEN END TAG
                        if self._processing_comment: # if we are inside a comment, nothing to do except 
                            if not self._auto_expand:
                                output = self._remove_tagname_from_output(output, C.OPEN_END_TAG) # remove OPENEND from output stream          

                            if tagname == self.COMMENT_TAG_NAME:   # Check for end of comment
                                self._processing_comment = False

                                # We are at end of OPENEND for a comment, indicate we are now processing reg text, if we were before comment started
                                #  bc the comment scope is not a regular tag, so the requirements for transition
                                #  from tagspace to regular text are different, i.e, we don't emit a ref or comment in LLMON
                                self._processing_reg_text = self._processing_reg_text_before_comment
                        else:
                            # confirm the closing tagname is valid
                            self._check_for_valid_tagname(tagname, line_num)

                            if self._auto_expand:
                                if not self._check_for_non_output_special_tag(tagname):  # exit nest if it isn't a special one, like ref or comment
                                    self._exit_tag_nest()
                                else:
                                    self._set_special_tag(tagname, False)   # set appropriate processing flag to false

                                    # We are at end of OPENEND for a ref or comment, indicate we are now processing reg text
                                    #  bc the ref or comment scope is not a regular tag, so the requirements for transition
                                    #  from tagspace to regular text are different, i.e, we don't emit a ref or comment in LLMON
                                    self._processing_reg_text = True

                                if tagname == self.REF_TAG_NAME:  # leaving ref tag scope
                                    self._inside_ref_scope = False
                                else:
                                    self._emitted_open_block = False # we found the end of open end for a non-ref block, so safe to reset flag
                            else:
                                if self._check_for_non_output_special_tag(tagname):   
                                    output = self._process_special_tag(output, tagname, C.OPEN_END_TAG)  # Remove the OPEN_END tag from output string
                                else: 
                                    output = output + tagname + C.CLOSE_TAG

                            if output.endswith(C.END_LIST_TAG):
                                self._processing_list = False

                        self._processing_tagname = False
                        self._tag_state = ""   # mark that we are no longer within an open_end tag
                        tagname = ""        # reset tagname, since we are done with this tag
                    else:
                        if self._tag_state != C.OPEN_TAG:
                            raise LLMONError(f"** Logic error, unexpected tag state: {self._tag_state}")  # THIS IS the END of SELF-CLOSING TAG  We found a "/" after processing a "\" that wasn't ended. 
                        # NOTEs: 
                        # There is no processing needed for special tags (such as "ref" or "comment") in this case because there is no content.
                        # No need to check for any transitions (when _auto_expand is on) here bc we are already processing the self close tag, i.e., we just saw the end of it

                        if tagname == self.COMMENT_TAG_NAME or tagname == self.REF_TAG_NAME:
                            raise LLMONError(f"** Error: comment/ref is not allowed in a self-closing tag on line {line}")
                        
                        if not self._processing_comment:  # if we are inside a comment, ignore this
                            if self._auto_expand:  # if auto expanding we need to add the OPEN_TAG, if not auto_expanding this was already done when see "\"
                                output += C.OPEN_TAG
                            output = output + tagname + C.SELF_CLOSE_TAG
                            self._tag_state = ""  # reset tag_state and tagname
                            self._processing_tagname = False
                            tagname = ""                     
                elif ch == "." or ch == ":":  # . and : are special tokens when they are inside of a tag, otherwise they are just regular text
                    if self._tag_state == C.OPEN_TAG or self._tag_state == C.OPEN_END_TAG:
                        if ch == ".":
                            tagname += C.DOT_JOINER_TAG
                        else:
                            if ch != ":":
                                raise LLMONError(f"** Logic error, expecting : on line {line}")
                            tagname += C.COLON_SEPARATOR_TAG
                    else: # We are not processing the tag
                        # Need to check for ch == ":" when we are inside the key part of an object item
                        # For example, only the 1st use of : below should be converted
                        #   \item\ a: the : is a punction mark /item/
                        if ch == ":" and self._processing_object_item_key and not self._processing_comment:  
                            output += C.COLON_SEPARATOR_TAG
                            self._processing_object_item_key = False   # We are no longer processing the key, so set false so other uses of : are treated as just chars
                        elif ch == ":" and self._processing_ref:  # handling : inside of  \ref\ ... /ref/ correctly
                            if self._processing_comment:
                                raise LLMONError(f"** Logic error, processing_ref and processing_comment are both true")
                            output += C.COLON_SEPARATOR_TAG
                        else:  # saw a "." or a regular ":"
                            out = self._check_for_transition_to_reg_text(ch) # returns ch (if auto_expand false) otherwise, implements auto expand logic
                            if self._tag_state == "":
                                output += out    # not processing a tag, so just regular output
                            else: 
                                raise LLMONError(f"** Logic error, unexpected tag_state: {self._tag_state}")
                elif ch == "," and self._processing_list:
                    if self._processing_comment:
                        raise LLMONError(f"** Logic error, processing_ref and processing_comment are both true")
                    output += C.LIST_SEPARATOR_TAG
                else:
                    output += self._check_for_transition_to_reg_text(ch)    # returns ch (if auto_expand false) otherwise, implements auto expand logic
            else: # Not a special character, add it to output or tagname
                out = self._check_for_transition_to_reg_text(ch) # returns ch (if auto_expand false) otherwise, implements auto expand logic
                if self._tag_state == "":
                    output += out
                else: 
                    tagname += out
            idx += 1
        return output

## BEGIN MAIN

def stdout_or_file(name):
    if name == "-":
        return sys.stdout
    else:
        return open(name, "w")

def stdin_or_file(name):
    if name == "-":
        return sys.stdin
    else:
        # Check if the file exists and is a file
        if not os.path.isfile(name):
            sys.exit(f"*** Error: {name} is not a file")
        return open(name, "r")

def parse_args():
    parser = argparse.ArgumentParser(description="Convert a Shorthand file to a LLMON file.")

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
    
    # optional boolean flag to expand nested tags
    parser.add_argument("-et", 
                        "--expand_tags", 
                        help="Automatically expand nested tags to have explicit names, remove redundant nesting, and closing tagname", 
                        action="store_true")  # when flag is on set arg to true
    
    parser.add_argument("-net", 
                        "--no_expand_tags", 
                        help="Do NOT automatically expand nested tags to have explicit names, remove redundant nesting, and closing tagname", 
                        action="store_true")  # when flag is on set arg to false
        
    return parser.parse_args()

def main():

    args =parse_args()
    auto_expand = True  # auto_expand is on by default, can be turned off with no_expand_tags option.  expand_tags option exists for backward compatibility
    if args.no_expand_tags:
        auto_expand = False
    l2m = Shorthand_to_LLMON_Converter(auto_expand=auto_expand)
    output = ""
    line  = args.input.readline() # reads a line
    line_num = 1
    while line:
        try:
            res = l2m.process_line(line, line_num)
        except LLMONError as exc:
            sys.exit(str(exc))
        if line[-1] == "\n":  # if line ends with newline char, add it to output, unless expand_tags is on and we are not processing reg_text
            if (not args.expand_tags or l2m.processing_reg_text()) and not l2m.processing_comment():
                res += "\n"
        output += res
        args.output.write(output)
        output = ""
        line = args.input.readline() # reads next line
        line_num += 1

    if args.expand_tags:  # add newline at end
        args.output.write("\n")
