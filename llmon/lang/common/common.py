# Copyright IBM Corp. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# The LLMON special tokens
OPEN_TAG = "<|open|>"
OPEN_END_TAG = "<|open_end|>"
CLOSE_TAG = "<|close|>"
SELF_CLOSE_TAG = "<|self_close|>"
DOT_JOINER_TAG = "<|.|>"
COLON_SEPARATOR_TAG = "<|:|>"

# Sanitized version of the LLMON special tokens
SANITIZED_OPEN_TAG = "<!open!>"
SANITIZED_OPEN_END_TAG = "<!open_end!>"
SANITIZED_CLOSE_TAG = "<!close!>"
SANITIZED_SELF_CLOSE_TAG = "<!self_close!>"
SANITIZED_DOT_JOINER_TAG = "<!.!>"
SANITIZED_COLON_SEPARATOR_TAG = "<!:!>"

# Common LLMON strings used during parsing and generation (OBJECT and DICT are synonymous)

# Here's the vocabulary to known tag names used for translating between JSON and LLMON
OBJECT_NAME = "object"
ITEM_NAME = "item"
LIST_NAME = "list"
INT_NAME = "int"
FLOAT_NAME = "float"
BOOL_NAME = "bool"
NONE_NAME = "none"
INSTR_NAME = "instr"
INSTRUCTION_NAME = "instruction"
EXEC_NAME = "exec"
DATA_NAME = "data"
INPUT_NAME = "input"
OUTPUT_NAME = "output"

LIST_SEPARATOR_TAG = "<|,|>"

START_OBJECT_TAG = OPEN_TAG + OBJECT_NAME + CLOSE_TAG
END_OBJECT_TAG = OPEN_END_TAG + OBJECT_NAME + CLOSE_TAG
START_OBJECT_ITEM_TAG = OPEN_TAG + ITEM_NAME + CLOSE_TAG
END_OBJECT_ITEM_TAG = OPEN_END_TAG + ITEM_NAME + CLOSE_TAG

START_OBJECT_ITEM_TAG_SUFFIX = ITEM_NAME + CLOSE_TAG

START_LIST_TAG = OPEN_TAG + LIST_NAME + CLOSE_TAG
END_LIST_TAG = OPEN_END_TAG + LIST_NAME + CLOSE_TAG

START_INT_TAG = OPEN_TAG + INT_NAME + CLOSE_TAG
END_INT_TAG = OPEN_END_TAG + INT_NAME + CLOSE_TAG
START_FLOAT_TAG = OPEN_TAG + FLOAT_NAME + CLOSE_TAG
END_FLOAT_TAG = OPEN_END_TAG + FLOAT_NAME + CLOSE_TAG
START_BOOL_TAG = OPEN_TAG + BOOL_NAME + CLOSE_TAG
END_BOOL_TAG = OPEN_END_TAG + BOOL_NAME + CLOSE_TAG
START_NONE_TAG = OPEN_TAG + NONE_NAME + CLOSE_TAG
END_NONE_TAG = OPEN_END_TAG + NONE_NAME + CLOSE_TAG

# Convenient collections of tags to test against
LLMON_SPECIAL_TOKEN_TAGS = {
    OPEN_TAG,
    OPEN_END_TAG,
    CLOSE_TAG,
    SELF_CLOSE_TAG,
    DOT_JOINER_TAG,
    COLON_SEPARATOR_TAG
}


def get_sanitized_version(llmon_token) -> str:
    if llmon_token == OPEN_TAG:
        return SANITIZED_OPEN_TAG
    elif llmon_token == OPEN_END_TAG:
        return SANITIZED_OPEN_END_TAG
    elif llmon_token == CLOSE_TAG:
        return SANITIZED_CLOSE_TAG
    elif llmon_token == SELF_CLOSE_TAG:
        return SANITIZED_SELF_CLOSE_TAG
    elif llmon_token == DOT_JOINER_TAG:
        return SANITIZED_DOT_JOINER_TAG
    elif llmon_token == COLON_SEPARATOR_TAG:
        return SANITIZED_COLON_SEPARATOR_TAG
    else:
        return llmon_token

LLMON_TAGS = {
    OPEN_TAG,
    OPEN_END_TAG,
    CLOSE_TAG,
    SELF_CLOSE_TAG,
    DOT_JOINER_TAG,
    COLON_SEPARATOR_TAG,
    LIST_SEPARATOR_TAG
}

LLMON_NON_CONNECTOR_TAGS = {
    OPEN_TAG,
    OPEN_END_TAG,
    CLOSE_TAG,
    SELF_CLOSE_TAG,
    LIST_SEPARATOR_TAG  # This tag is not valid inside of a tag, so we include it here
}

LLMON_COMPOSITE_TAGS = {
    OPEN_TAG,
    OPEN_END_TAG
}

# JSON constants
JSON_START_OBJECT_TAG = "{"
JSON_END_OBJECT_TAG = "}"
JSON_START_LIST_TAG = "["
JSON_END_LIST_TAG = "]"
JSON_KEY_VALUE_SEPARATOR = ":"
JSON_LIST_SEPARATOR = ","

INDENT_VALUE = 2  # how many spaces to indent when pretty printing LLMON and JSON

def indent(value: int) -> str:
    res = ""
    for i in range(value):
        res += " "
    return res

def is_llmon_tag(token: str) -> bool:
    for tag in LLMON_TAGS :
        if  token.startswith(tag):
            return True
    return False

def is_llmon_composite_tag(tag: str) -> bool:
    for t in LLMON_COMPOSITE_TAGS:
        if tag == t :
            return True
    return False

def is_llmon_open_composite_tag(tag: str) -> bool:
    return tag == START_OBJECT_TAG or tag == START_LIST_TAG

def is_llmon_close_composite_tag(tag: str) -> bool:
    return tag == END_OBJECT_TAG or tag == END_LIST_TAG

def is_llmon_close_item_tag(tag: str) -> bool:
    return tag == END_OBJECT_ITEM_TAG or tag == LIST_SEPARATOR_TAG
