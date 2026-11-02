# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# for forward reference types in recursive type definitions
from __future__ import annotations

import warnings
import json
import sys
from typing import Any, TypeAlias

import llmon.lang.common.common as C
from llmon.lang.common.errors import LLMONError

PrimitiveType : TypeAlias =  str | int | float | bool | None

class LLMONConverters:
    """ A class to hold static converters to and from LLMON notation. It also holds related constants. """

    def __init__(self, 
                 converting_to_json: bool=False, # Default: not being used to convert to JSON
                 no_type_tags_for_literals: bool=False, # Default: Generate LLMON type tags for JSON literals, when converting from JSON to LLMON
                 with_mellea: bool=False,       # Default: Not being used as part of mellea installation
                 keys_as_item: bool=False,      # JSON-to-LLMON: Default: Don't generate JSON dictionary keys as separate LLMON item tags, when converting from JSON to LLMON
                                                # LLMON-to-JSON: Default: Don't expect object item to have \\item\\ key : value /item/ format
                 no_expand_tags: bool=False,    # Default: Expand tags to include outer nest names when converting from JSON to LLMON
                 name_array_instances: bool=False, # Default: Don't generate instance ids for all JSON lists, when converting from JSON to LLMON
                 compact: bool=True,            # Default: Make JSON to LLMON generation compact, i.e., no extra white space for human readability
                 no_composite_tags: bool=True,  # Default: Don't generated composite tag names when converting from JSON to LLMON
                 no_openend_tagname: bool=True, # Default: Don't generate openend tagname and close tag when converting from JSON to LLMON
                 generate_nested_tagnames: bool=False # Default: Don't generate outer nested blocks for user tags
                 ):
        
        self._converting_to_json = converting_to_json 
        self._add_type_tags_for_literals = not no_type_tags_for_literals 
        self._with_mellea = with_mellea  
        self._keys_as_item = keys_as_item 
        self._no_expand_tags = no_expand_tags 
        self._name_array_instances = name_array_instances 
        self._compact = compact 
        self._composite_tags = not no_composite_tags # flip the boolean bc it is easier to reason about the affirmative rather than the negation
        self._no_openend_tagname = no_openend_tagname 
        self._generate_nested_tagnames = generate_nested_tagnames 

        # A global variable to capture the nesting state
        self._current_nest = "" # will hold the string of the current nesting, a new nest tag will append to this

        self._parse_llmon_recurse_level=0 # captures how many recursive levels of parse_llmon we are currently in
        self._next_index = 1


    def sanitize(self, data: str) -> str:
        """
            Convert the input string into a string that changes each character sequence that matches a LLMON special token into an alternative character
            sequence that doesn't match a LLMON special token. All other characters are preserved as seen.
            For example, we could map  <|open|> to <!open!>
            As the method name suggests, we expect call to use this method to sanitize their strings to unsure unwanted to LLMON special tokens aren't passed
            to the tokenizer.
        """

        output = data
        for llmon_token in C.LLMON_SPECIAL_TOKEN_TAGS:   # Look for each special token in data and replace with its sanitized version
            output = output.replace(llmon_token, C.get_sanitized_version(llmon_token))
        return output

    # Public methods to Convert JSON to LLMON. They differ based on the representation for JSON (input) and LLMON (output)
    def json_str_to_llmon_str(self, data: str, prefix:str = "") -> str:
        """ Convert JSON object (stored as a python string) into equivalent LLMON (python) string
            data: the python dict to convert
            prefix: a string that should be the first part of all created soft type tags           
        """

        output = self.json_str_to_llmon_list(data, prefix) # returns list of LLMON tokens as strings
        return self.convert_llmon_list_to_formatted_str(output)  # convert list to a single formatted string

    def json_str_to_llmon_list(self, data: str, prefix:str = "") -> list[PrimitiveType]:
        """ Convert a JSON object (stored as a python string) into equivalent LLMON, expressed as a (python) list of types
            data: the python dict to convert
            prefix: a string that should be the first part of all created soft type tags        
        """
        self._current_nest = prefix # All tags for this dict will start with the passed optional prefix
        return self._translate_python_to_llmon(json.loads(data), []) # loads converts json str to python representation
    
    def json_dict_to_llmon_str(self, data: dict, compact:bool = False, prefix:str = "") -> str:
        """ Convert JSON object (stored as a python dictionary) into equivalent LLMON (python) string
            data: the python dict to convert
            compact=True, output has no whitespace/newlines between tokens
            prefix: a string that should be the first part of all created soft type tags
        """

        output = self.json_dict_to_llmon_list(data, prefix) # returns list of LLMON tokens as strings
        if compact or self._compact: # will make it compact if the LLMONConverters construction said so, or if this particular call wants it
            return "".join(str(t) if t is not None else "null" for t in output)

        return self.convert_llmon_list_to_formatted_str(output)  # convert list to a single string

    def json_dict_to_llmon_list(self, data: dict, prefix:str = "") -> list[PrimitiveType]:
        """ Convert a JSON object (stored as a python dictionary) into equivalent LLMON, expressed as a (python) list of types
            data: the python dict to convert
            prefix: a string that should be the first part of all created soft type tags        
        """
        self._current_nest = prefix # All tags for this dict will start with the passed optional prefix
        return self._translate_python_to_llmon(data, [])

    def convert_python_object_to_llmon(self, data: Any, prefix:str = "", formatted:bool = True) -> str:
        """Convert the passed python object into LLMON
            data: the python dict to convert
            prefix: a string that should be the first part of all created soft type tags         
        """
        self._current_nest = prefix # All tags for this dict will start with the passed optional prefix
        llmon_strings = self._translate_python_to_llmon(data, [])
        if formatted:
            return self.convert_llmon_list_to_formatted_str(llmon_strings)
        else:
            return self.convert_llmon_list_to_str(llmon_strings)   
    

    def convert_llmon_list_to_str(self, data: list[PrimitiveType]) -> str:
        """ Convert a list of LLMON tokens into a single string, doesn't add newlines or indentation
            Although most tokens are strings, some are values, such as ints, floats, bools
        """
        ## strings get ""s around them, tags are just tags, primitives have no quotes
        output = ""
        for token in data:
            if type(token) == str:
                if self._is_llmon_tag(token):
                    output += token
                else:
                    output = output + "\"" + token + "\""
            else:
                if token is None:
                    output += "null"
                else:
                    output += str(token)
        return output

    def convert_llmon_str_to_formatted_str(self, data: str) -> str:
        """ Convert a LLMON string to a formatted LLMON string, i.e., a pretty-printer for LLMON.
            Since we have a function that takes a LLMON list
            of tokens and does the formatting conversion, we'll convert to a list and then call that function.
            Future work, could look to avoid this conversion step.
        """

        list = []
        token, rest = self._get_token(data)
        while token != "":
            list.append(token)
            token, rest = self._get_token(rest)

        output = self.convert_llmon_list_to_formatted_str(list)
        return output

    def convert_llmon_list_to_formatted_str(self, data: list[PrimitiveType]) -> str:
        """ Convert a list of LLMON tokens into a single formatted string, i.e. with indentations and new lines.
        """

        ## strings get ""s around them, tags are just tags, primitives have no quotes
        output = ""
        indent_level = 0  # the number of spaces to indent for the current token
        new_indent_level = 0 # the number of spaces to indent for the next token, after we output the current token
        for token in data:
            if type(token) == str:
                if self._is_llmon_tag(token):
                    if self._is_llmon_open_composite_tag(token):
                        # opening a composite tag gets its own line, so add newline if doesn't already exist and end with a newline
                        # we add to output so that indenting will work correct at bottom of for loop
                        if output != "" and output[-1] != "\n":
                            output += "\n"
                        token_str = token + "\n"

                        # subsequent tokens in this composite tag need to be indented
                        new_indent_level = indent_level + C.INDENT_VALUE

                    elif self._is_llmon_open_end_composite_tag(token):
                        # Closing a composite tag gets its own line too
                        # we add to output so that indenting will work correct at bottom of for loop
                        if output != "" and output[-1] != "\n":
                            output += "\n"
                        token_str = token + "\n"

                        # this tag needs to be outdented, so we adjust current indent level (indent_level)
                        indent_level -= C.INDENT_VALUE
                        new_indent_level = indent_level

                    elif self._is_llmon_close_item_tag(token):
                        token_str = token + "\n"
                    else:
                        token_str = token
                else:  # a non-LLMON string type, just pass it through
                    if token == "":  # an empty string is valid for a JSON value, so need to handle is specially to ensure it survives conversion
                        token_str = "\"\""
                    else:
                        token_str = token
            else:
                if token is None:
                    token_str = "null"
                else:
                    # It's another primitive type, int, boolean, float, make is a string so it fits in output
                    token_str = str(token)

            # Only add indentaton if the previous character is a newline, ie this token will be first on this line
            if output != "" and output[-1] == "\n":
                output += self._indent(indent_level)
            output += token_str
            indent_level = new_indent_level  # update indent level for next token

        return output

    def _convert_python_primitive_to_json(self, data: str) -> str:
        """
          Simple function to get the canonical LLMON tag for the passed python primitive type
        """
        if type(data) == int or type(data) == float: # Since python treats the int 1 as True, first check for ints (and floats)
            return data
        
        if data == True:
            return "true"
        elif data == False:
            return "false"
        elif data == None:
            return "null"
        else:
            raise LLMONError(f"** Logic Error: unexpected literal: {data}")

    def _enter_tag_nest(self, tagname: str):
        """ Called when a new soft/user type is seen to record the tag for nesting expansion purposes"""
        if self._current_nest != "":
            self._current_nest = self._current_nest + C.DOT_JOINER_TAG + tagname      # add a separator       
        else:
            self._current_nest = tagname

    def _exit_tag_nest(self):
        """ Called when a soft/user type is closed, used for nesting expansion purposes"""
        # remove the last tag from the end of current_nest
        idx = self._current_nest.rfind(C.DOT_JOINER_TAG)
        if idx == -1:  # only 1 tag in the current nest
            self._current_nest = ""  # remove it by setting nest to null
        else:
            self._current_nest = self._current_nest[:idx]

    def _enter_tag_instance(self, idx: int):
        """ Called when a new array instance is seen to record the tag for nesting expansion purposes
            we may have x.emp_data[2], we need to append ":2" to current nest
        """
        if self._current_nest != "":
            self._current_nest = self._current_nest + C.COLON_SEPARATOR_TAG + str(idx)   
        else:
            self._current_nest = C.COLON_SEPARATOR_TAG + str(idx)  

    def _exit_tag_instance(self):
        """ Called when an array instance has completed processing, so we need to remove it from current_nest"""
        # remove the last tag from the end of current_nest
        idx = self._current_nest.rfind(C.COLON_SEPARATOR_TAG)
        if idx == -1:  # only 1 tag in the current nest
            self._current_nest = ""  # remove it by setting nest to null
        else:
            self._current_nest = self._current_nest[:idx]

    def _translate_python_to_llmon(self, data:Any, out: list[PrimitiveType]) -> list[PrimitiveType]:
        """ This recursive function is used to convert python types into LLMON.
        It has 2 parameters
        1) data: the python object to process
        2) out : the current list of LLMON tokens to append to
        It returns the updated version of the 2nd parameter
        """

        # First check for python composite types (dict, list)
        if isinstance(data, dict):
            if self._composite_tags:  # Emit the START_OBJECT_TAG if requested
                out.append(C.START_OBJECT_TAG)
            for key, val in data.items():
                if self._keys_as_item:
                    out.append(C.START_OBJECT_ITEM_TAG)
                    out.append(key)
                    if val == "":    # if we have an empty (null string) value then we just need a SELF_CLOSE_TAG
                        out.append(C.SELF_CLOSE_TAG)
                    else:
                        out.append(C.COLON_SEPARATOR_TAG)
                        out = self._translate_python_to_llmon(val, out)
                        out.append(C.END_OBJECT_ITEM_TAG)
                else:  # We will use the key name as the tag in the soft type

                    if not self._no_expand_tags:  # expand tags!
                        self._enter_tag_nest(key)
                        ## update key name to contain full nest
                        key = self._current_nest

                    val_is_not_composite = type(val) is not dict and type(val) is not list # conputed here, but needed twice below

                    # Check for an empty (null string) value. 
                    if val == "":
                        # we have found something like  "Application Domains": ""
                        # we will represent this as a self-closing LLMON tag, i.e., <|open|>Application Domains<|self-close|>
                        open_str = C.OPEN_TAG + key + C.SELF_CLOSE_TAG
                        out.append(open_str)
                    else: # val is not empty string

                        # We are generating the key as a tag and the value as the content
                        # Generate if requested or type is not composite
                        if self._composite_tags or val_is_not_composite or self._generate_nested_tagnames:
                            open_str = C.OPEN_TAG + key + C.CLOSE_TAG
                            out.append(open_str)
                       
                        out = self._translate_python_to_llmon(val, out) # out is read and returned

                    if not self._no_expand_tags:  # expand tags!
                        key = self._current_nest  # update key name
                        self._exit_tag_nest()

                    if val != "":
                        # create the end of a soft type (user tag) with "key" as the tag
                        if self._composite_tags or val_is_not_composite or self._generate_nested_tagnames:
                            if self._no_openend_tagname:  # don't emit tagname and close if not requested
                                close_str = C.OPEN_END_TAG
                            else: 
                                close_str = C.OPEN_END_TAG + key + C.CLOSE_TAG
                            out.append(close_str)

            if self._composite_tags:  # Emit the END_OBJECT_TAG as needed
                if self._no_openend_tagname:
                    out.append(C.OPEN_END_TAG)
                else:
                    out.append(C.END_OBJECT_TAG)

        elif isinstance(data, list):
            if self._composite_tags:  # Emit the START_LIST_TAG 
                out.append(C.START_LIST_TAG)
            num_elements = len(data)
            for idx, val in enumerate(data):  # visit each element of the list/array
                # Update instance info if we have to track list instances
                if self._name_array_instances or (not self._composite_tags): 
                    self._enter_tag_instance(idx)

                #   we emit if name_array_instance flag is on or if we don't want composite_tags and val is not a composite
                val_is_not_composite = type(val) is not dict and type(val) is not list
                if self._name_array_instances or (not self._composite_tags and val_is_not_composite) or self._generate_nested_tagnames:
                    if self._current_nest != "":  # ensure we have a tag to generate
                        out.append(C.OPEN_TAG + self._current_nest + C.CLOSE_TAG)  # Each element will be a soft type with appropriate tag

                out = self._translate_python_to_llmon(val, out)  # recursively handle the values

                # if self._name_array_instances or not self._no_composite_tags or not val_is_composite:
                if self._name_array_instances or (not self._composite_tags and val_is_not_composite) or self._generate_nested_tagnames:
                    if self._current_nest != "":  # ensure we have a tag to generate
                        if self._no_openend_tagname:  # don't emit tagname and close if not requested
                            out.append(C.OPEN_END_TAG)
                        else:
                            out.append(C.OPEN_END_TAG + self._current_nest + C.CLOSE_TAG)

                # Update instance info if we have to track list instances?
                if self._name_array_instances or (not self._composite_tags): 
                    self._exit_tag_instance()

                if idx < num_elements - 1 and self._composite_tags:   # check for a next element in list and comp flag set
                    out.append(C.LIST_SEPARATOR_TAG)  # add LLMON list separator when needed
                
            if self._composite_tags:  # Emit the END_LIST_TAG 
                if self._no_openend_tagname:
                    out.append(C.OPEN_END_TAG)
                else:
                    out.append(C.END_LIST_TAG)

        elif isinstance(data, str):
            # if this is a string, need to make sure escaped chars are represented correctly
            out.append(self._adjust_for_escape_chars(data))
        elif self._is_nonstring_primitive_type(data):

            if self._add_type_tags_for_literals:
                # Add the type info in LLMON. This allows the model to distinguish from the string version, i.e, 100 vs "100"
                out.append(self._get_start_tag_for_primitive(data))
                out.append(self._convert_python_primitive_to_json(data))
                out.append(self._get_end_tag_for_primitive(data))
            else:
                # Don't add typo info, save tokens and hope model isn't confused
                out.append(self._convert_python_primitive_to_json(data))
        else:
            # Now check for user-defined types that we support and adjust data appropriately
            # Currently, this only happens with known mellea types, but this can
            #  be expanded in the future
            if self._with_mellea:
                from mellea.core.base import CBlock # only do import if we are running with mellea
                if isinstance(data, CBlock):
                    if isinstance(data.value, str):
                        out.append(self._adjust_for_escape_chars(data.value))
                    else:
                        out.append(data.value)     
                else:
                    raise TypeError(f"unsupported type for LLMON conversion: {type(data)}")
                
            else:
                if not self._is_nonstring_primitive_type(data):
                    raise LLMONError(f"** Error, expecting non string primitive type, found: {data}, {type(data)}" )

                # if this is a string, need to make sure escaped chars are represented correctly
                if isinstance(data, str):
                    out.append(self._adjust_for_escape_chars(data))
                else:
                    out.append(data)   

        return out

    def _adjust_for_escape_chars(self, data:str)->str:
        result = ""
        for char in data:
            if char == "\"":
                result += "\\\""     # need to escape quote char (\")
            elif char == "\n":
                result += "\\n"
            else:
                result += char
        return result

    def _is_llmon_tag(self, token: str) -> bool:
        for tag in C.LLMON_TAGS :
            if  token.startswith(tag):
                return True
        return False

    def _is_llmon_composite_tag(self, tag: str) -> bool:
        for t in C.LLMON_COMPOSITE_TAGS:
            if tag == t:
                return True
        return False

    def _is_llmon_open_composite_tag(self, token: str) -> bool:
        # check for built-in composite tags
        if token == C.START_OBJECT_TAG or token == C.START_LIST_TAG:
            return True
        # Items look better when not indented
        if token == C.START_OBJECT_ITEM_TAG:
            return False
        # check for a user-defined tag
        if token.startswith(C.OPEN_TAG) and token.endswith(C.CLOSE_TAG):
            return True
        return False

    def _is_llmon_open_end_composite_tag(self, token: str) -> bool:
        if token == C.END_OBJECT_TAG or token == C.END_LIST_TAG:
            return True
        # Items look better when not indented
        if token == C.END_OBJECT_ITEM_TAG:
            return False
        # check for a user-defined tag
        if token.startswith(C.OPEN_END_TAG) and token.endswith(C.CLOSE_TAG):
            return True
        
        if self._no_openend_tagname and token.startswith(C.OPEN_END_TAG):
            return True
        return False

    def _is_llmon_close_item_tag(self, token: str) -> bool:
        return token == C.END_OBJECT_ITEM_TAG or token == C.LIST_SEPARATOR_TAG


    def _is_nonstring_primitive_type(self, obj: PrimitiveType) -> bool:
        """
        Determines if the passed object is a nonstring primitive type (in python),
        rather than a composite type like list or array.

        This function is for runtime checking; For static checking, use PrimitiveType type hint.
        """
        if type(obj) == int or \
        type(obj) == float or \
        type(obj) == bool or \
        obj is None:
            return True
        else:
            return False
        

    def _get_start_tag_for_primitive(self, obj: PrimitiveType) -> str:
        """
        Returns the LLMON start tag corresponding to the primititve type passed
        """
        # Since bool is an instance of int, we use type rather than isintance

        if type(obj) == int:
            return C.START_INT_TAG
        elif type(obj)== float:
            return  C.START_FLOAT_TAG
        elif type(obj) == bool:
            return C.START_BOOL_TAG
        elif obj is None:
            return C.START_NONE_TAG
        else:
            raise LLMONError(f"** Logic Error, unexpected type: {type(obj)}, for {obj}")

    def _get_end_tag_for_primitive(self, obj: PrimitiveType) -> str:
        """
        Returns the LLMON end tag corresponding to the primititve type passed
        """
        if self._no_openend_tagname:  # if this option is on, it is simple
            return C.OPEN_END_TAG
        
        if type(obj) == int:
            return C.END_INT_TAG
        elif type(obj)== float:
            return  C.END_FLOAT_TAG
        elif type(obj) == bool:
            return C.END_BOOL_TAG
        elif obj is None:
            return C.END_NONE_TAG
        else:
            raise LLMONError(f"** Logic Error, unexpected type: {type(obj)}, for {obj}")


    ###################################
    """ Main entry point for general parsing of LLMON files """
    def process_llmon_str(self, llmon_str: str) -> str:
        """ Parse the passed LLMON object (as a string). We'll probably want to add functionality to it as we need it.
            For now this is just ensuring that a general LLMON object is correct and returns that LLMON object as a string.
            So, it is basically just a LLMON validator for now.
        """
        self._parse_llmon_recurse_level = 0
        rest = llmon_str
        output = ""
        while rest != "":
            json_str, rest = self._parse_llmon(rest)

            # the output (json_str) needs some fine-tuning to adjust for escape chars and format appropriately
            output += self._add_needed_escaping_in_strings(json_str)

        output = self.convert_llmon_str_to_formatted_str(output)
        return output

    ##############################################################################
    """  START LLMON to JSON conversion methods """

    """ Main entry point """
    def llmon_str_to_json_str(self, llmon_str: str, **json_dump_kwargs) -> str:
        """ Convert LLMON string into equivalent JSON string.
            Since LLMON may not be 1 nested structure, we may need to add outer {..}
        """

        # We iterative over the LLMON states, parsing each one and collecting the resulting string in output
        self._parse_llmon_recurse_level = 0
        rest = llmon_str
        output = []
        while rest != "":
            json_str, rest = self._parse_llmon(rest)

            # the output (json_str) needs some fine-tuning to adjust for escape chars and format appropriately
            if json_str != "":
                output.append(self._add_needed_escaping_in_strings(json_str))

        # Convert the unformatted list of JSON strings into python objects ...
        # .... and convert it back to nicely formatted JSON for human consumption, 
        #    putting results in single output string

        if len(output) > 1:   #check if we have a list, if so, put it inside a JSON object, if needed
            new_output = "{"
            for idx, item in enumerate(output):
                item = item.replace("\n", "\\n")  # change newlines to \n to get valid JSON
                if item.find(":") == -1:  # Check for regular non-LLMON text, which will not have the ":" in the key value pair. if found create a special key
                    item = C.JSON_UNTAGGED_KEY_STRING + self._new_index() +"\":" + item
                else:
                    if item[0] == "{":  # If item is already a dict, which is common, remove the redundant braces
                        item = item[1:-1]

                new_output += item
                if idx != len(output) -1:  # add , if not last element
                    new_output += ", "

            new_output += "}"
        elif len(output) == 1:
            new_output = output[0]  # just single element list
        else:
            return "{}"  # empty output, so produce empty JSON object

        try: # exit gracefully if we feail the json conversion
            python_version = json.loads(new_output)
            formatted_output = json.dumps(python_version, **json_dump_kwargs)  # ensure_ascii outputs the char rather than unicode
        except Exception as e:
            sys.exit(f"Failed conversion to JSON with msg: {str(e)}\nConversion input:\n{new_output}")

        return formatted_output

    def _new_index(self) -> str:
        """ return a new integer, exprssed as a string, each time it is called  """
        val = self._next_index
        self._next_index += 1
        return str(val)

    """ Helper functions """
    def _find_closing_tag(self, data: str):
        """ Look in data to find first instance of a closing LLMON tag, i.e, either CLOSE_TAG or SELF_CLOSE_TAG
            return the idx and type of close, if found
        """

        idx1 = data.find(C.CLOSE_TAG)
        idx2 = data.find(C.SELF_CLOSE_TAG)
        if idx1 == -1:
            if idx2 == -1:
                return -1, ""
            else: 
                return idx2, C.SELF_CLOSE_TAG
        else:
            if idx2 == -1:
                return idx1, C.CLOSE_TAG
            else: # found both
                min_idx = min(idx1, idx2)
                if data[min_idx:].startswith(C.CLOSE_TAG):   # This assumes that CLOSE_TAG is not a prefix of SELF_CLOSE_TAG, which is true now
                    if data.startswith(C.SELF_CLOSE_TAG):
                        raise LLMONError(f"** Logic Error in find_closing_tag")
                    return min_idx, C.CLOSE_TAG
                else:
                    return min_idx, C.SELF_CLOSE_TAG

    def _find_llmon_token(self, data: str, ignore_connectors: bool):
        """ find the index in data of the first LLMON token, return -1 if none exists 
            If ignore_connectors is set to True, we only look for LLMON non-connector tags
        """

        min_idx = sys.maxsize
        found_tag = ""
        # Use the correct list depending on the ignore_connector parm
        if ignore_connectors:  
            tag_list = C.LLMON_NON_CONNECTOR_TAGS
        else:
            tag_list = C.LLMON_TAGS
        for tag in tag_list:
            idx = data.find(tag)
            if idx != -1 and idx < min_idx:
                min_idx = idx
                found_tag = tag

        # check if found_tag is part of a pair, if so, return the full token
        if self._is_llmon_composite_tag(found_tag):
            if self._no_openend_tagname and found_tag == C.OPEN_END_TAG:
                pass
            else:
                # look for a closing tag
                idx, found_closing_tag = self._find_closing_tag(data)
                if idx == -1:
                    raise LLMONError(f"** Error: Didn't find a closing tag for {found_tag} in {data}")
                found_tag = data[:(idx+len(found_closing_tag))]

        return min_idx, found_tag

    def _get_token(self, data: str, ignore_connectors: bool=False):
        """ finds next token in the passed string, returning that token and the rest of the string
            The ignore_connectors parameter is passed on to _find_llmon_token. 
            It true it means that we shouldn't tream connector tokens (like <|:|> and <|.|> as terminator a token.
            For example, if true, the following would be considered as a single token
                    instr<|:|>1
                rather than 3 tokens: "instr", "<|:|>", and "1"
        """
        data = data.lstrip()    # remove any leading white space
        index, llmon_token = self._find_llmon_token(data, ignore_connectors)
        if index == 0: # first token is a LLMON token
            # partition the token
            list = data.partition(llmon_token)
            token = list[0] + list[1]  # suffix is part of the token
            rest = list[2]
        else:
            if index != -1:  # first token is not a LLMON one, but there are other LLMON ones later that should NOT be returned
                token = data[:index].rstrip()    # remove any trailing whitespace to ensure JSON strings don't span a line
                rest = data[index:]
            else:
                # no more LLMON tokens in input, but found next token
                token = data.rstrip()  # remove any trailing whitespace to ensure JSON strings don't span a line
                rest = ""
        return token, rest

    def _is_user_start_tag(self, token: str) -> bool:
        # Check that this token begins with OPEN_TAG and ends with CLOSE_TAG, everything in the middle is the user tag
        if token.startswith(C.OPEN_TAG) and token.endswith(C.CLOSE_TAG):
            return True
        else:
            return False

    def _is_user_start_self_close_tag(self, token: str) -> bool:
        # Check that this token begins with OPEN_TAG and ends with SELF_CLOSE_TAG, everything in the middle is the user tag
        if token.startswith(C.OPEN_TAG) and token.endswith(C.SELF_CLOSE_TAG):
            return True
        else:
            return False        

    def _is_float(self, value) -> bool:
        """ Is the passed value a float? """
        try:
            float(value)  # if this conversion success, next stmt is executed, otherwise we go to exception handler and return false
            return True
        except ValueError:
            return False

    def _indent(self, value: int) -> str:
        res = ""
        for i in range(value):
            res += " "
        return res

    def _add_needed_escaping_in_strings(self, output: str)->str:
        """ return the passed string, except any \" char or \\ should be escaped"""

        result = ""
        idx = 0
        while idx < len(output):
            if output[idx:].startswith("\\\""):
                result += "\\\""
                idx += 2
            else:
                result += output[idx]
                idx += 1
        return result

    ###  Here's the parser code
    """
        The code below is a recursive descent (predictive) parser that is using syntax-directed translation technique. This means that each non-terminal
        in the grammer are represented by a python function below. Each function is responsible for processing the grammar rules associated with the
        corresponding non-terminal. For example, parse_llmon, is handling the grammer rule that start with "LLMON". The grammar rule for
        each non-terminal is included in the comment associated with the function.
        The syntax-directed translation aspect is that in addition to parsing the input file (passed as a string), we are also translating to
        JSON (when that flag is set). Thus, each function will have parsing code and generation code (when that flag is set). 
        (See the variable "output" for the generation part.)
        The input to each function is the rest of the string to be parsed.
        We don't worry about newlines and indenting in the generated JSON here, but instead have a post-processing step using python's json package.
        The return values are
            - the translation for the scope of this function/non-terminal. This could be a simple literal "True", "null", 34, or a composite
            type "[ 34, 23]".  For such a list type, recursion is used to handle the inner components of the list.
            - the rest of the input string to be processed
        
        NOTE: By default, all LLMON free text is a string. If one wants to represent the integer 100, it should be surrounded by tags using INT_NAME, as in
            <|open|>int<|close|>100<|open_end|>int<|close|>
        
        Thus code for converting from JSON to LLMON or vice versa will use this feature to determine how to handle

    Grammar (for LLMON data that is equivalent to JSON, i.e., can be translated into JSON)
        LLMON: LLMON_ITEM LLMON_LIST
        LLMON_LIST: LLMON_ITEM LLMON_LIST | epsilon 
        LLMON ==> USER_TYPE | OBJECT | LIST | integer | float | string | true | false | null 
        USER_TYPE ==> START_USER_TAG LLMON END_USER_TAG  | SELF_CLOSE_USER_TAG
        OBJECT ==> START_OBJECT_TAG  OBJECT_ITEMS  END_OBJECT_TAG   |  START_OBJECT_TAG  END_OBJECT_TAG
        OBJECT_ITEMS ==> OBJECT_ITEM   OBJECT_ITEMS_REST
        #  This is KAI option: OBJECT_ITEM: start_object_item_tag string colon_separator_tag LLMON end_tag
        OBJECT_ITEM: open_tag string close_tag  LLMON  open_end_tag string close_tag
                | open_tag string self_close_tag  
        OBJECT_ITEMS_REST ==>    OBJECT_ITEMS | epsilon   # NOTE: no comma separator needed for LLMON object items
        LIST ==> START_LIST_TAG  LIST_ITEMS  END_LIST_TAG |  START_LIST_TAG  END_LIST_TAG
        LIST_ITEMS ==> LLMON LIST_ITEMS_REST
        LIST_ITEMS_REST ==>   list_separator_tag  LIST_ITEMS | epsilon

    """
    def _parse_llmon(self, llmon_data: str):
        """ This is the root of a recursive-descent parser for the LLMON grammar
        Grammar Rule: LLMON ==> LIST | OBJECT | integer | float | true | false | null | string
        """

        self._parse_llmon_recurse_level+=1

        # If we are not converting to JSON, we will add an extra space after primitive tokens to improve formatting
        if self._converting_to_json:
            extra = ""
        else:
            extra = " "

        # Since we are the main parsing node, we want get_token to ignore connectors (<|:|> and <|.|>) as token separators
        token, rest = self._get_token(llmon_data, ignore_connectors=True)
        if token == C.START_LIST_TAG:
            result, rest = self._parse_List(llmon_data) # call appropriate function for grammar non-terminal and don't advance in input
        elif token == C.START_OBJECT_TAG:
            result, rest = self._parse_Object(llmon_data) # call appropriate function for grammar non-terminal and don't advance in input
        elif self._is_user_start_tag(token):
            result, rest = self._parse_User_Type(llmon_data)
        elif self._is_user_start_self_close_tag(token):  # a user tag that is self closing
            result, rest = self._parse_User_Self_Close_Type(llmon_data) 
        else:   
            ## This can include strings that look like literals, such as "1000", "3.14", and "True"
            ## We treat all of these as strings. If the non-string version (int, float, bool) is desired
            ##  it needs to be surrounded by the appopriate LLMON tag, such as \int\ 1000 /int/.
            #   This case is handled above in "_parse_User_Type".
            if self._converting_to_json:
                if token == "\"\"" or token == "":
                    result = token
                else:
                    result = "\"" + token + extra + "\""
            else:
                result = token + extra

        self._parse_llmon_recurse_level-=1
        return result, rest

    def _parse_Object(self, llmon_data: str):
        """
        Grammar Rule: OBJECT ==> START_OBJECT_TAG  OBJECT_ITEMS  END_OBJECT_TAG   |  START_OBJECT_TAG epsilon END_OBJECT_TAG
        """
        token, rest = self._get_token(llmon_data)
        if token != C.START_OBJECT_TAG:
            raise LLMONError(f"** Logic Error: found {token}")

        if self._converting_to_json:
            output = C.JSON_START_OBJECT_TAG    # Translate to JSON
        else:
            output = C.JSON_START_OBJECT_TAG  # This will likely change once we know more

        # peek at next token to determine if we have an empty object,
        # but don't consume it just yet,
        # i.e., don't update "rest", bc if it is not an empty object the parse_Object_Items will need to see it
        token, rest1 = self._get_token(rest)
        if token == C.END_OBJECT_TAG:  # found an empty object
            rest = rest1       # now we can consume the token, ie. update "rest", so code after if will work.
        else:
            result, rest = self._parse_Object_Items(rest)  # Consume token by passing in "rest"
            output += result
            token, rest = self._get_token(rest)

        output += C.JSON_END_OBJECT_TAG
        if token != C.END_OBJECT_TAG:
            raise LLMONError(f"** Error: found {token}, expecting {C.END_OBJECT_TAG}")
        return output, rest

    def _parse_Object_Items(self, llmon_data: str):
        """
        Grammar Rule: OBJECT_ITEMS ==> OBJECT_ITEM   OBJECT_ITEMS_REST
        """
        output1, rest = self._parse_Object_Item(llmon_data)
        output2, rest = self._parse_Object_Items_Rest(rest)
        return output1 + output2, rest

    def _parse_Object_Item(self, llmon_data: str):
        """
        Grammar Rule:  OBJECT_ITEM: open_tag string close_tag  LLMON  open_end_tag string close_tag
                                  | open_tag string self_close_tag
        """
        token, rest = self._get_token(llmon_data)
        if self._keys_as_item:
            if token != C.START_OBJECT_ITEM_TAG:
                raise LLMONError(f"** Error, expecting {C.START_OBJECT_ITEM_TAG}, found {token}")

            key, rest = self._get_token(rest)  # This should be the key in the key-value pair
            if type(key) is not str:
                raise LLMONError(f"Expecting string type, but found {key}, which is type {type(key)}")

            token, rest = self._get_token(rest)
            if token == C.SELF_CLOSE_TAG:
                # Found self close, place key followed by JSON separator for key/value pairs, value is null string ""
                if self._converting_to_json:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " \"\"" 
                else:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " \"\""  
                return output1, rest
            
            else:
                if token != C.COLON_SEPARATOR_TAG:
                    raise LLMONError(f"** Error, expecting {C.COLON_SEPARATOR_TAG}, but found {token}")

                # place key followed by JSON separator for key/value pairs, JSON expects keys to be quoted so add them
                if self._converting_to_json:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " "
                else:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " "  # for now we produce the same output, this may change in the future, so making it explicit in the code

                output2, rest = self._parse_llmon(rest)  # get the value in the key-value pair
                output2 = str(output2)

                # now process the closing tag
                token, rest = self._get_token(rest)
                if token != C.END_OBJECT_ITEM_TAG:
                    raise LLMONError(f"** Logic Error, expecting {C.END_OBJECT_ITEM_TAG}, found {token}")

                return output1 + output2, rest
        else:
            # This should be C.OPEN_TAG tag C.CLOSE_TAG or C.OPEN_TAG tag C.SELF_CLOSE_TAG
            if not token.startswith(C.OPEN_TAG):
                raise LLMONError(f"** Logic Error, expecting {C.OPEN_TAG}, but found: {token}")

            if token.endswith(C.SELF_CLOSE_TAG):
                # We convert  <|OPEN|>name<|SELF_CLOSE|> to
                #     "name": ""

                key = token[len(C.OPEN_TAG): -len(C.SELF_CLOSE_TAG)]   # The key is the tag, so bypass the OPEN_TAG and don't include the CLOSE_TAG

                if not self._no_expand_tags: # if key has expanded nesting in it only use the last tag as the key for generated JSON
                    idx = key.rfind(C.DOT_JOINER_TAG)
                    if idx != -1:  # if there are not DOT_JOINER_TAGs there is no nesting
                        key = key[idx+len(C.DOT_JOINER_TAG):]  # get last tag

                # place key followed by JSON separator for key/value pairs, value is null string ""
                if self._converting_to_json:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " \"\"" 
                else:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " \"\""  
                return output1, rest

            else:
                if not token.endswith(C.CLOSE_TAG):
                    raise LLMONError(f"** Logic Error")

                key = token[len(C.OPEN_TAG): -len(C.CLOSE_TAG)]   # The key is the tag, so bypass the OPEN_TAG and don't include the CLOSE_TAG

                if not self._no_expand_tags: # if key has expanded nesting in it only use the last tag as the key for generated JSON
                    idx = key.rfind(C.DOT_JOINER_TAG)
                    if idx != -1:  # if there are not DOT_JOINER_TAGs there is no nesting
                        key = key[idx+len(C.DOT_JOINER_TAG):]  # get last tag

                # place key followed by JSON separator for key/value pairs, JSON expects keys to be quoted so add them
                if self._converting_to_json:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " " 
                else:
                    output1 = "\"" + key + "\"" + C.JSON_KEY_VALUE_SEPARATOR + " "  # for now we produce the same output, this may change in the future, so making it explicit in the code

                output2, rest = self._parse_llmon(rest)  # get the value in the key-value pair
                output2 = str(output2)

                # now process the closing tag
                token, rest = self._get_token(rest)
                if not token.startswith(C.OPEN_END_TAG):
                    raise LLMONError(f"** Logic Error, expecting {C.OPEN_END_TAG}, but found: {token}")

                key2 = token[len(C.OPEN_END_TAG): -len(C.CLOSE_TAG)]   # The key is the tag, so bypass the OPEN_TAG and don't include the CLOSE_TAG
                
                if not self._no_expand_tags: # if key has nesting in it only use the last tag as the key for generated JSON 
                    idx = key2.rfind(C.DOT_JOINER_TAG) 
                    if idx != -1:
                        key2 = key2[idx+len(C.DOT_JOINER_TAG):]

                if key != key2:
                    raise LLMONError(f"** Error: tagnames don't match, expecting {key}, but found {key2}")

                return output1 + output2, rest
        
    def _parse_Object_Items_Rest(self, llmon_data: str):
        """
        Grammar Rule: OBJECT_ITEMS_REST ==>   OBJECT_ITEMS    Selection Set: C.START_OBJECT_ITEM_TAG (for key_value_option 1), C.OPEN_TAG (for key_value_option 2)
                                            | epsilon         Selection Set: rest
                                                            # NOTE: no comma separator needed for LLMON object items
        """
        token, rest = self._get_token(llmon_data)
        if self._keys_as_item:  # key/values are expressed using \item\ key : value /item/ syntax
            if token == C.START_OBJECT_ITEM_TAG:  # check for another object item
                output1 = ",\n"
                output2, rest = self._parse_Object_Items(llmon_data)
                return output1 + output2, rest
            return "", llmon_data
        else:                                # key/values are expressed as \key\ value /key/
            if token.startswith(C.OPEN_TAG):  # check for an open tag for another item
                output1 = ",\n"
                output2, rest = self._parse_Object_Items(llmon_data)
                return output1 + output2, rest            
            return "", llmon_data
        
    def _parse_List(self, llmon_data: str):
        """
        Grammar Rule: LIST ==> START_LIST_TAG  LIST_ITEMS  END_LIST_TAG |  START_LIST_TAG epsilon END_LIST_TAG
        """
        token, rest = self._get_token(llmon_data)
        if token != C.START_LIST_TAG:
            raise LLMONError(f"** Logic Error, expecting {C.START_LIST_TAG}, found {token}")
        output = C.JSON_START_LIST_TAG

        # peek at next token to determine if we have an empty list or not,
        # but don't consume it just yet, i.e., don't update "rest", bc if it is not an empty list the parse_List_Items will need to see it
        token, rest1 = self._get_token(rest)
        if token == C.END_LIST_TAG:  # found an empty list
            rest = rest1       # now we can consume the token, ie. update "rest", so code after if will work.
        else:
            result, rest = self._parse_List_Items(rest)  # passing in original rest
            output += result
            token, rest = self._get_token(rest)  # get next token to check for end tag for post if code to work

        output += C.JSON_END_LIST_TAG
        if token != C.END_LIST_TAG:
            raise LLMONError(f"** Logic Error, expecting {C.END_LIST_TAG}, found {token}")
        return output, rest

    def _parse_List_Items(self, llmon_data: str):
        """
        Grammar Rule: LIST_ITEMS ==> LLMON LIST_ITEMS_REST
        """
        output1, rest = self._parse_llmon(llmon_data)
        output2, rest2 = self._parse_List_Items_Rest(rest)
        return output1 + output2, rest2

    def _parse_List_Items_Rest(self, llmon_data: str):
        """
        Grammar Rule: LIST_ITEMS_REST ==>   ,  LIST_ITEMS | epsilon
        """
        token, rest = self._get_token(llmon_data)
        if token == C.LIST_SEPARATOR_TAG:
            output, rest = self._parse_List_Items(rest)
            if self._converting_to_json:
                return C.JSON_LIST_SEPARATOR + " " + output, rest
            else:
                return C.JSON_LIST_SEPARATOR + " " + output, rest  # This will change in the future
        else: # found the end of the list
            return "", llmon_data

    def _check_for_known_tags(self, tag_name: str, content: str):
        """
        Check for a known tag, currently this is just literal tags, if found return the name without the quotes
          ie, we need to transform content from "1000" to 1000 or "true" to true
        """
        if tag_name == C.INT_NAME or tag_name == C.FLOAT_NAME or tag_name == C.BOOL_NAME or tag_name == C.NONE_NAME:
            if content[0] !="\"" or content[-1] != "\"":
                raise LLMONError(f"** Logic Error, expected a quote literal, but found: {content}")
            return(content[1:-1])   # remove the quotes 
        else:
            return ""
    
    def _parse_User_Type(self, llmon_data: str):
        """
        Grammar Rule:  USER_TYPE ==> START_USER_TAG LLMON END_USER_TAG  | SELF_CLOSE_USER_TAG
        """
        token, rest = self._get_token(llmon_data)
        ## 1 Process OPEN_TAG
        if not token.startswith(C.OPEN_TAG):
            raise LLMONError(f"** Logic Error, expecting {C.OPEN_TAG}, found {token}")

        ## Find the close tag, everything in the middle is the tag name
        tag_name_idx = len(C.OPEN_TAG)

        close_idx = token.find(C.CLOSE_TAG)
        if close_idx == 0:
            raise LLMONError(f"** Logic Error, didn't find {C.CLOSE_TAG} in {llmon_data}")
        tag_name = token[tag_name_idx:close_idx]

        ## 2 Process content by looking for the content after the open tag and recursively processing it
        ## The content can be a multiple sequential LLMON objects, so we loop until we see an OPEN_END_TAG
        token = "dummy"
        output = ""
        rest_tmp = ""
        out = ""
        multi_elements = False
        while token != "" and  not token.startswith(C.OPEN_END_TAG):
            out, rest = self._parse_llmon(rest)
            # If this is 2nd element, we need to add comma and set flag so we can create a JSON object
            if self._converting_to_json and output != "":
                output += ","  # append comma to seperate list/dict elements
                multi_elements = True
            output += out
            token, rest_tmp = self._get_token(rest)  # peaking ahead (token) to see if we need another iteration
        
        if multi_elements:   # if output has multiple elements in it, we need to wrap them in a JSON object
            if self._converting_to_json:
                output = C.JSON_START_OBJECT_TAG + output + C.JSON_END_OBJECT_TAG
            else:
                output = C.JSON_START_OBJECT_TAG + output + C.JSON_END_OBJECT_TAG  # This will likely change in the future

        rest = rest_tmp  # update rest, i.e. we consume the token that we just peaked at in the loop

        ## 3 Process the open end tag
        open_end_token = token
        if not open_end_token.startswith(C.OPEN_END_TAG):
            raise LLMONError(f"** Logic Error, expecting {C.OPEN_END_TAG}, found {open_end_token}")

        if self._no_openend_tagname:
            pass
        else:
            # confirm the open_end tag_name matches open tag name
            close_idx = open_end_token.find(C.CLOSE_TAG)
            end_tag_name = open_end_token[len(C.OPEN_END_TAG): close_idx]
            if tag_name != end_tag_name:
                raise LLMONError(f"** Logic Error, open [{tag_name}] and open end [{end_tag_name}] tag names don't match")

        value = self._check_for_known_tags(tag_name, out)
        if value != "":
            result = value
        else: 
            if self._converting_to_json:
                if self._name_array_instances:
                    # if we created tagging for array_instances during translation from JSON, than we need to not emit them when translating back
                    # So, we just return the output computed above

                    # I DON'T THIS IS CORRECT ANY MORE
                    # TODO FIX!!
                    result = output

                else:
                    # tag_name is the key, output is the value
                    # If we are at outer most nest, we need to wrap in JSON object, otherwise calling code will deal with when to add JSON object
                    result = "\"" + tag_name + "\":" + output 
                    if self._parse_llmon_recurse_level == 1:  
                        result = C.JSON_START_OBJECT_TAG + result + C.JSON_END_OBJECT_TAG
            else:
                # If we aren't translating to JSON, it is not clear at this point what to do, just returning original text 
                result = C.OPEN_TAG + tag_name + C.CLOSE_TAG + output + C.OPEN_END_TAG
                if not self._no_openend_tagname:
                    result = result + tag_name + C.CLOSE_TAG

        return result, rest


    def _parse_User_Self_Close_Type(self, llmon_data: str):
        """
        Grammar Rule:  USER_TYPE ==> SELF_CLOSE_USER_TAG
        """
        token, rest = self._get_token(llmon_data)
        ## 1 Process OPEN_TAG
        if not token.startswith(C.OPEN_TAG):
            raise LLMONError(f"** Logic Error, expecting {C.OPEN_TAG}, found {token}")

        ## Find the close tag, everything in the middle is the tag name
        tag_name_idx = len(C.OPEN_TAG)
        close_idx = token.find(C.SELF_CLOSE_TAG)
        if close_idx == 0:
            raise LLMONError(f"** Logic Error, didn't find {C.SELF_CLOSE_TAG} in {llmon_data}")
        tag_name = token[tag_name_idx:close_idx]

        if self._converting_to_json:
            # self-close tags don't have a value, so we use an empty string for the value
            result = C.JSON_START_OBJECT_TAG + "\"" + tag_name + "\":" + "\"\"" + C.JSON_END_OBJECT_TAG
        else:
            # This will likely change in the future
            result = C.JSON_START_OBJECT_TAG + "\"" + tag_name + "\":" + "\"\"" + C.JSON_END_OBJECT_TAG

        return result, rest
