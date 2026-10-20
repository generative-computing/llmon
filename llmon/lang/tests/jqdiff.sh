#!/bin/bash

# does not work, since it still recognizes the newlne difference
# diff --ignore-all-space $1 $2

diff --ignore-all-space \
     <(python -m json.tool --sort-keys < $1) \
     <(python -m json.tool --sort-keys < $2)

