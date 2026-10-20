#!/bin/bash

# does not work, since it still recognizes the newlne difference
# diff --ignore-all-space $1 $2

diff --ignore-all-space <(tr -d '\n' < $1) <(tr -d '\n' < $2)

