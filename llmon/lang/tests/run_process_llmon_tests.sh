#!/bin/sh

cd "$(dirname "$0")"

failures=0
src=process_llmon
res=process_llmon_test_results

mkdir -p $res

for test in example1 example2 example3 example4
do
    echo "Testing $test ...."

    process-llmon  $src/$test.llmon $res/$test-parsed.llmon

    ./mydiff.sh $res/$test-parsed.llmon $src/$test.llmon > $res/$test-parsed-llmon.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for parse llmon.  Results are in $res/$test-parsed-llmon.diff"
    else
        echo "  Passed process llmon ($test)"
    fi

    process-llmon -eot $src/$test-eot.llmon $res/$test-eot-parsed.llmon

    ./mydiff.sh $res/$test-eot-parsed.llmon $src/$test-eot.llmon > $res/$test-eot-parsed-llmon.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for parse llmon -eot.  Results are in $res/$test-eot-parsed-llmon.diff"
    else
        echo "  Passed process llmon ($test) -eot"
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
