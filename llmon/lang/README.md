This directory contains code and tests related to LLMON (LLM-native markup language) language and its converters.

# Installation

To get access to these converters do the following in the root directory (two above this one) in a virtual environment, run

`pip install -e .`

This will make the following commands accessible in the command line:

| Command | Description |
|---|---|
| `json-to-llmon`| converts a JSON file to LLMON file |
| `llmon-segment` | converts regular free text file to LLMON file |
| `short-to-llmon` | converts a Shorthand file to a LLMON file |
| | |
| `llmon-to-json` | converts a LLMON file to JSON file |
| `llmon-to-short` | converts a LLMON file to a Shorthand file |
| `text-to-escaped-short`| takes a text file of (almost) Shorthand and adds the needed escapes to be valid Shorthand. The resulting file can then be converted by short-to-llmon |
| `process-llmon` | parses a LLMON file  |

# Directory structure

| Directory | What it covers |
|---|---|
| [`json/`](json/) | converters between LLMON and JSON |
| [`unstructured_text/`](unstructured_text/) | converters from free text to LLMON or shorthand, tests for these converters |
| [`shorthand/`](shorthand/) | converters between Shorthand and LLMON |
| [`llmon_parsing/`](llmon_parsing/) | simple main routine that can be used for parsing LLMON (leverages code in JSON folder) |
| [`common/`](common/) | common code among various converters |
| [`tests/`](tests/) | numerous tests for JSON and shorthand converters |

# Developers
Please run the regression test scripts in tests folder before submitting a PR for the converters.


# The LLMON Markup Language
The goal of the LLMON markup language is to provide the capability to highlight various segments (or spans) of text with a tag that signifies meta information about that text segment. 
LLMON models have been trained with some default tag names (`data`, `instruction`, `exec`), but users are free to develop their own tag vocabulary. (See the [examples folder](../examples/README.md) for an example of this.)
This capability can be used for many purposes. One important example is to enable an enforcement mechanism that only executes text that is tagged as an "instruction", which can prevent a category of malicious prompt injection attacks. 

To express this kind of meta information, we need to identify two quantities: the segment of text and the tag that should be associated with that text. 
We address these needs by enclosing the text segment inside a block
that indicate the tag of the segment, which is a popular approach for markup languages. In our case we use the sequence
`<|open|> tagname <|close|>`
to indicate the beginning of the segment of type `tagname`
and `<|open_end|>` to indicate the end of this segment.
Here is a simple example:
```
    <|open|>instruction<|close|>
      Suggest a title for the following email:
    <|open_end|> 
    <|open|>data<|close|>
      Dear customer support, I am writing about the wifi connection on my recent 
      flight from JFK to LAX. The quality was poor. I paid $25, and I want a refund.
    <|open_end|>
```

This example uses two default tag names: `instruction` and `data`.
This syntax enables annotating text with any tag, such as 
`int`, `data`, `Spanish`, or `ToolCallInput`.

LLMON contains two important features: 
- references to tag instances and 
- explicit nesting in tag names

Text segments can be named as instances, such as `attachment<|:|>3`, which can then be referenced by other text, similar to the anchor mechanism in HTML. 
Explicit nesting in tag names is achieved using a tag such as
`email<|.|>paragraph` to indicate the `paragraph` text is nested inside of `email`.
By explicitly providing the hierarchical structure in the tag name as opposed to having large nesting structures, we increase the likelihood that an LLM will be able to better learn the structure.
Nesting structures are suitable for parsers and humans (with the help of indentation and formatters), but are not well-suited to LLMs linear nature of processing.

## LLMON's Use of Special Tokens
Text provided to an LLM as a prompt or training are first transformed into tokens by a 
tokenizer.
A token is an integer that represents segments of the input.
These segments can vary from a single character to multiple words based on pattern analysis of the text.
In addition to regular tokens, one can instruct the tokenizer to treat certain character sequences as "special tokens" that the tokenizer will not decompose or combine with any adjacent characters.
This is typically done to ensure clarity in what is trying to be expressed with the goal of helping LLM learning. 

LLMON defines six special tokens:
|Special Token | Meaning |
|---|---|
|`<\|open\|>` | opens a highlighted segment |
|`<\|close\|>` | closes the tagname part of the segment |
|`<\|open_end\|>` | ends a highlighted segment |
|`<\|self_close\|>` | ends a tagname and highlight segment |
|`<\|.\|>` | joins a nested tag path, e.g., `exec<\|.\|>input` |
|`<\|:\|>` | separates a tag from its instance ID for a highlighted segment, e.g., `instr<\|:\|>2`

This means that a LLMON tag such as
`<|open|> email <|close|>`
is guaranteed to have 
`<|open|>` and `<|close|>` be unique tokens.
Similarly, a tag name such as `email<|.|>from` will have the `<|.|>` treated as a unique token, which will make it easier for the model to learn its special meaning.

## LLMON and JSON
JSON is a popular markup language for representing data and is often used to specify the required format for LLM output from a prompt. 
We have created converters between JSON and LLMON (both ways), called `json-to-llmon` and `llmon-to-json`.
Below is an example JSON object and how it is converted into LLMON using `json-to-llmon` with the default options.

JSON Example
```
{
    "Trip": {
        "Purpose": "Business",
        "Cities": [
            "New York",
            "Tokyo",
            "Cairo"
        ]
    }
}
```

LLMON Equivalent
```
<|open|>Trip<|.|>Purpose<|close|>Business<|open_end|>
<|open|>Trip<|.|>Cities<|:|>0<|close|>New York<|open_end|>
<|open|>Trip<|.|>Cities<|:|>1<|close|>Tokyo<|open_end|>
<|open|>Trip<|.|>Cities<|:|>2<|close|>Cairo<|open_end|>
```

The JSON object contains a dictionary (`Trip`) with two key/value pairs. The first pair is a simple key/value, where the value is a string (`Business`). The second pair has a value that is a list of strings (`New York`, `Tokyo`, `Cairo`).

The conversion to LLMON leverages LLMON's instances naming feature and its explicit nesting feature. 
Rather than using nesting, it creates blocks for each data value (`Business`, `New York`, `Tokyo`, and `Cairo`). It tags these blocks using the keys in the nesting from the JSON combined with the `<|.|>` special token (`Trip<|.|>Purpose` or `Trip<|.|>Cities`).
Finally, it encodes each list element with its id (`Trip<|.|>Cities<|:|>0`,
`Trip<|.|>Cities<|:|>1`,
`Trip<|.|>Cities<|:|>2`)
so it can be referenced by other text as needed.

Although there is apparent redundancy in repeating the outer nesting tag names, we believe this will help LLMs learn the tag semantics for each block.

We believe converters for other structured representations can be written in a similar way.

## Shorthand
Although the LLMON syntax was designed to ease LLM understanding, it can be
tedious for humans. To address this concern we've created a syntactic shorthand for the markup language. It uses the same grammar (described below) as LLMON, replacing special tokens with more concise characters. For example, `<|open|>` is written as `\`, `<|open_end|>` is expressed as `/`, 
`<|:|>` and `<|.|>` are simply written as `:` and `.`, respectively.

The table below provides a mapping from Shorthand to LLMON tokens.

| Shorthand  | LLMON |
|-----------|---------|
| \tag\    | <\|open\|>tag<\|close\|>      |
| /tag/    | <\|open_end\|>  |
| \tag/    | <\|open\|>tag<\|self_close\|>  |
|   .      | <\|.\|> |
|   :      | <\|:\|> |

Note: `:` is converted to the LLMON special token, `<|:|>` when it is being used in a LLMON-way, i.e, when it appears inside a tag to represent an instance, or when it is separating a key and value in a key/value (aka object item) pair. When `:` is used in regular text it will remain `:` in LLMON. The same is true of `.`, it is only converted when used in the name of a tag to represent flattened nesting.

The `<|self_close|>` tag in the third row that rather than surrounding some text just indicates the presence of some concept, where the concept is contained in the `tag`. One example would be communicating an event, like applause `\applause/` or change in author `\author_change/`.


The example above would be expressed in shorthand as follows:
```
\Travel_Information\
    \Purpose\Business/Purpose/
    \Cities\ \list\ New York, Tokyo, Cairo /list/ /Cities/
/Travel_Information/
```

We have created converters between shorthand and LLMON (both ways), called `short-to-llmon` and `llmon-to-short`.
The `short-to-llmon` converter would generate this LLMON
```
<|open|>Travel_Information<|.|>Purpose<|close|>Business<|open_end|>
<|open|>Travel_Information<|.|>Cities<|.|>list<|close|> New York<|,|> Tokyo<|,|> Cairo <|open_end|>
```

Note: The `<|,|>` character sequence is not a special token. It is just a character sequence created by this `short-to-llmon` converter when processing the `list` tag to helps the `llmon-to-short` converter generate the correct shorthand.

The `short-to-llmon` converter provides two useful features:
- like the `json-to-llmon` converter, it will flatten any nested structures into tag names representing the nesting. This allows a human to express nested structures succinctly in a natural way, but still have the flattened tags being used when passed to LLMs. 
- it allows a comment facility to exist in shorthand, represented as `\comment\` ... `/comment/`. Comments are removed during conversion to LLMON.



## Examples

We now explore the expressive power of LLMON, using its shorthand syntax, but LLMON is what should be sent to an LLM, i.e., the examples below should use `short-to-llmon` to convert the example to LLMON before giving it to an LLM.

Here is an example that leverages LLMON's ability to name instances of content so it can be referred to with other markup text. 

    \instr:a\ List five common fruits /instr:a/
    \instr:b\ Calculate 12 + 8 /instr:b/
    \instr:c\ Write a haiku about the ocean /instr:c/
    \exec:x\
        \instr\ \ref\ instr:b /ref/ /instr/
    /exec:x/

Here we have given several user-created tag (3 "instr" and 1 "exec") instance identifiers (`a`, `b`, `c`, and `x`), which allows other passages to refer to them, similar to an anchor in HTML or a line number in FORTRAN. This is done in the `instr` passage, where it refers to `instr:b` using `\ref\ instr:b /ref/` pattern. The intended meaning of this example is to enumerate 3 potential instructions for the LLM and then to have it only execute `instr:b`.


#### Note:
The `\ref\ .. /ref/` pattern is a message to the Shorthand to LLMON converter to ensure the enclosed tag is treated as a reference, which implies the insertion of special tokens surrounding the tag. Without this pattern, the converter will treat it as a just a string.

### Observation
Although the tags `email`, `instr`, `exec` (and their instance variants) may seem to be built-in constructs in the language, they are not, i.e., they have no special semantics. They are intended to communicate information to an LLM that when properly trained will, hopefully, infer their meaning.

The next example builds on the previous one by adding a parameter to the executed instruction. As before, the first 3 lines specify instruction with instances. After that we've added a `data` tag (with instance id `1`), which is then referred to in the added `exec:m.input` tag that is nested inside the `exec:m` tag.

    \instr:f\Translate the text into French /instr:f/
    \instr:g\Count the number of words in the given sentence /instr:g/
    \instr:h\Summarize the text in one short sentence /instr:h/
    \data:1\The quick brown fox jumps over the lazy dog /data:1/
    \exec:m\
        \instr\ \ref\instr:g/ref/ /instr/
        \input\ \ref\data:1/ref/ /input/
    /exec:m/

The point of these examples is to illustrate the various capabilities of user tags, i.e, instance identifiers (`a`, `m`, `1`), not the implied semantics of the tag identifiers. LLMON is agnostic to any semantic meaning of user-defined tags. However, one can use LLMON to train a model to understand the intended semantics, LLMON does not implement them. (However, the boundary separation implied by LLMON tags can be used by a training-independent technique called Span Masking.)

### Strings are the default
Since LLMs are character based, by default, LLMON treats all text as characters, i.e., 123 is a string of 3 characters, not an integer. If you want to express the integer, 123, you need to enclose it inside an int user tag:

    \int\ 123 /int/ 

or 
    \bool\ True /bool/

The built-in primitive tags supported are `int`, `bool`, `float`, and `none`. This may only be relevant if you want to convert LLMON into JSON, where there is a distinction between the string `123` and the integer `123`. If the information is being passed to an LLM, it will only see it as a string.

### LLMON composite types

LLMON can also express lists, a collection of values, and objects (or dictionaries), which are a collection of items that are specified as key/value pairs. We've already seen examples of both lists and objects, expressed in shorthand and LLMON above.

User types, lists, and objects can all be nested in various combinations as the grammar below suggests. (Each value of list, object item, or user type is a LLMON non-terminal) These are expressed in a natural nesting way in Shorthand, but flattened in LLMON, unless the `-net` flag is used during `short-to-llmon` conversion.

### LLMON Syntax
The above examples were all expressed using the Shorthand syntax, which is equivalent to the LLMON syntax that is used when communicating with an LLM.

Here is the example from above expressed in LLMON. Note that the `\ref\ ... /ref/` pattern has been replaced by the appropriate tag with the special token `<\|:\|>` (and `\<\|.\|>`)

```
<|open|>instr<|:|>f<|close|>    Translate the text into French<|open_end|>
<|open|>instr<|:|>g<|close|>    Count the number of words in the given sentence<|open_end|>
<|open|>instr<|:|>h<|close|>    Summarize the text in one short sentence<|open_end|>
<|open|>data<|:|>1<|close|>    The quick brown fox jumps over the lazy dog<|open_end|>
<|open|>exec<|:|>m<|.|>instr<|close|> instr<|:|>g <|open_end|>
<|open|>exec<|:|>m<|.|>input<|close|> data<|:|>1 <|open_end|>
```

### A few technical notes
- Spaces and indentation are not significant in Shorthand or LLMON, but humans often find them useful. :-) 
  -- However, in Shorthand one must be careful not to have two adjacent `\` characters because they will be interpreted as escaping the `\`, i.e., `\instr\\ref\ ...` should be written as `\instr\ \ref\ ...` to avoid being interpretted as an escape sequence.
- `user_tag_text` in full LLMON will encode `.` and `:` as the special tokens `<|.|>` and `<|:|>` 


## LLMON Grammar
Below is the grammar that specifies valid LLMON. Unlike JSON, a LLMON document can also contain regular text that is not tagged in any way. The grammar applies to both LLMON and shorthand. 

The LLMON_ITEM non-terminal can be a USER_TYPE (aka, a user-defined or system tag block) or literal types (integer, float, string, true, false, or null). The OBJECT and LIST types are used to represent objects and lists that may be present in shorthand.

###
    LLMON: LLMON_ITEM LLMON_LIST

    LLMON_LIST: LLMON_ITEM LLMON_LIST
        | epsilon 

    LLMON_ITEM: USER_TYPE
        | OBJECT
        | LIST
        | integer
        | float
        | string
        | 'true'
        | 'false'
        | 'null'

    USER_TYPE: start_user_tag LLMON end_user_tag
        | self_close_user_tag

    OBJECT: start_object_tag OBJECT_ITEMS end_object_tag
        | start_object_tag end_object_tag

    OBJECT_ITEMS:  OBJECT_ITEM  OBJECT_ITEMS_REST

    OBJECT_ITEM: start_object_item_tag string colon_separator_tag LLMON end_object_item_tag 

    OBJECT_ITEMS_REST: OBJECT_ITEMS 
        | epsilon

    LIST: start_list_tag LIST_ITEMS end_list_tag
        | start_list_tag  end_list_tag

    LIST_ITEMS: LLMON LIST_ITEMS_REST

    LIST_ITEMS_REST: list_separator_tag LIST_ITEMS
        | epsilon

### Legend: 
    terms in UPPER_CASE are non-terminals
    terms in single quotes or lower-case are terminals

Here are the current values for the non-quoted terminals above (expressed in LLMON)
```
    start_user_tag = <|open|>user_tag_text<|close|>
    end_user_tag = <|open_end|>
    self_close_user_tag = <|open|>user_tag_text<|self_close|>

    start_object_tag = <|open|>object<|close|>
    end_object_tag = <|open_end|>object<|close|>
    start_object_item_tag = <|open|>item<|close|>
    end_object_item_tag = <|open_end|>item<|close|>
    start_list_tag = <|open|>list<|close|>
    end_list_tag = <|open_end|>list<|close|>
    list_separator_tag = <|,|>
    colon_separator_tag = <|:|>
```

Note: 
- `user_tag_text` can be any identifier and can include `<|:|>` and `<|.|>` as illustrated in examples below
- `object`, `item`, `list` are exactly those strings
- user_tag_text is represented by the regular expression: [_a-zA-Z][_a-zA-Z0-9.:]*
