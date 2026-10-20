#!/bin/sh

cd "$(dirname "$0")"

failures=0
res=json_test_results
src=json

mkdir -p $res

# run a JSON to LLMON using No Composite Tags and also with No Openend Tags
echo " "
echo "Testing JSON to LLMON (default options) ...."
for test in tools1 tools2 non-string empty-list empty-dict dict1 dict-nest dict-multi-nest list list2 list3 list-nest list-of-dict list-list test1 null-string-dict-value colon-key string+literal mellea-instruction paper-example benchmark_card 
do
    #echo "Testing $test ...."

    json-to-llmon $src/$test.json $res/$test.llmon

    ./mydiff.sh $res/$test.llmon $src/$test.llmon > $res/$test-json-to-llmon.diff
    if [ $? -ne 0 ]; then
        failures=$((failures + 1))
        echo "  *** Failure for json --> llmon  Results are in $res/$test-json-to-llmon.diff"
    else
        echo "  Passed json --> llmon ($test)"
    fi

    llmon-to-json $src/$test.llmon $res/$test.json

    ./mydiff.sh $res/$test.json $src/$test-noct-noot.json > $res/$test-llmon-to-json.diff
    if [ $? -ne 0 ]; then
        failures=$((failures + 1))
        echo "  *** Failure for llmon --> json  Results are in $res/$test-llmon-to-json.diff"
    else
        echo "  Passed llmon --> json ($test)"
    fi

    json-to-llmon -got $src/$test.json $res/$test-got.llmon

    ./mydiff.sh $res/$test-got.llmon $src/$test-got.llmon > $res/$test-got-json-to-llmon.diff
    if [ $? -ne 0 ]; then
        failures=$((failures + 1))
        echo "  *** Failure for json -got --> llmon  Results are in $res/$test-got-json-to-llmon.diff"
    else
        echo "  Passed json --> llmon -got ($test)"
    fi

done

for test in empty-list empty-dict dict1 dict-nest dict-multi-nest list list2 list3 list-nest list-of-dict list-list test1 null-string-dict-value colon-key string+literal mellea-instruction paper-example benchmark_card 
do
    #echo "Testing $test ...."

    json-to-llmon -got -gct $src/$test.json $res/$test-got-gct.llmon

    ./mydiff.sh $res/$test-got-gct.llmon $src/$test-got-gct.llmon > $res/$test-got-gct-json-to-llmon.diff
    if [ $? -ne 0 ]; then
        failures=$((failures + 1))
        echo "  *** Failure for json -got -gct --> llmon  Results are in $res/$test-got-gct-json-to-llmon.diff"
    else
        echo "  Passed json -got -gct --> llmon ($test)"
    fi

    llmon-to-json -eot $src/$test-got-gct.llmon $res/$test-eot.json

    ./jqdiff.sh $res/$test-eot.json $src/$test.json > $res/$test-got-gct-llmon-to-json.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for llmon -eot --> json.  Results are in $res/$test-got-gct-llmon-to-json.diff"
    else
        echo "  Passed llmon --> json ($test)"
    fi

    # Testing with the --keys_as_item option set
    json-to-llmon -kai -net -got -gct $src/$test.json $res/$test-kai.llmon

    ./mydiff.sh $res/$test-kai.llmon $src/$test-kai.llmon > $res/$test-kai-json-to-llmon.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for json -kai --> llmon  Results are in $res/$test-kai-json-to-llmon.diff"
    else
        echo "  Passed json -kai --> llmon ($test)"
    fi

    llmon-to-json -eot -kai -net $src/$test-kai.llmon $res/$test-kai.json

    ./jqdiff.sh $res/$test-kai.json $src/$test.json > $res/$test-kai-llmon-to-json.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "   *** failure for llmon -kai --> json.  Results are in $res/$test-kai-llmon-to-json.diff"
    else
        echo "  Passed llmon -kai  --> json ($test)"
    fi


    # Testing with the --no_type_tag_for_literals option set
    json-to-llmon -ntt -got -gct $src/$test.json $res/$test-ntt.llmon

    ./mydiff.sh $res/$test-ntt.llmon $src/$test-ntt.llmon > $res/$test-ntt-json-to-llmon.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for json -ntt --> llmon  Results are in $res/$test-ntt-json-to-llmon.diff"
    else
        echo "  Passed json -ntt --> llmon ($test)"
    fi

done

# run a few tests using the -nai option 
echo " "
echo "Testing -nai option ...."
for test in list-of-dict test1 benchmark_card
do
    #echo "Testing $test ...."
    json-to-llmon -nai -got -gct $src/$test.json $res/$test-nai.llmon

    ./mydiff.sh $res/$test-nai.llmon $src/$test-nai.llmon > $res/$test-json-to-llmon-nai.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for json --> llmon -nai Results are in $res/$test-json-to-llmon-nai.diff"
    else
        echo "  Passed json --> llmon -nai ($test)"
    fi

    llmon-to-json -eot -nai $src/$test-nai.llmon $res/$test-nai.json

    ./jqdiff.sh $res/$test-nai.json $src/$test.json > $res/$test-nai-llmon-to-json.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  $test *** failure for llmon -nai --> json.  Results are in $res/$test-nai-llmon-to-json.diff"
    else
        echo "  Passed llmon -nai  --> json ($test)"
    fi
done

# run a LLMON to JSON tests for soft types
echo " "
echo "Testing LLMON to JSON (soft type tests) ...."
for test in simple-softtype soft-colon-in-key exec-instr exec-instr2 exec-instr-input multi 
do
    #echo "Testing $test ...."

    llmon-to-json $src/$test.llmon $res/$test.json

    # TODO
    diff $res/$test.json $src/$test.json > $res/$test-llmon-to-json.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for llmon --> json.  Results are in $res/$test-llmon-to-json.diff"
    else
        echo "  Passed llmon --> json ($test)"
    fi
done


# run JSON to LLMON using -gnt (generate nested tagnames) 
echo " "
echo "Testing JSON to LLMON (-gnt options) ...."
for test in tools1 tools2 
do
    #echo "Testing $test ...."

    json-to-llmon -gnt $src/$test.json $res/$test-gnt.llmon

    ./mydiff.sh $res/$test-gnt.llmon $src/$test-gnt.llmon > $res/$test-gnt-json-to-llmon.diff
    if [ $? -ne 0 ]; then
        failures=$((failures + 1))
        echo "  *** Failure for json --> llmon  Results are in $res/$test-gnt-json-to-llmon.diff"
    else
        echo "  Passed json --> llmon -gnt ($test)"
    fi
done

echo ""
if [ "$failures" -eq 1 ]
then
    echo "Testing complete with $failures failure."
else
    echo "Testing complete with $failures failures."
fi

[ "$failures" -eq 0 ] || exit 1
