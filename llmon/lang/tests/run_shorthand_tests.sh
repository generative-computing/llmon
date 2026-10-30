#!/bin/sh

# Script to test conversions between Shorthand and LLMON

cd "$(dirname "$0")"

failures=0
src=shorthand
res=shorthand_test_results

mkdir -p $res

for test in list-test id-test hier-test dict-test scalar-test reg-text self-close colon-key-item colon-key2 example1 example2-ref example2 example3 example4 email-example nest nest2 nest3 ref ref2 ref3 ref4 ref5 escape2 comment-test comment-catalog t1 t2 t3 t4
do

    # run tests with the default, "-et" (expand tags)
    short-to-llmon $src/$test.short $res/$test.llmon

    ./mydiff.sh $res/$test.llmon $src/$test.llmon > $res/$test-short-to-llmon.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for short -> llmon ($test).  Results are in $res/$test-short-to-llmon.diff"
    else
        echo "  Passed short --> llmon ($test)"
    fi

    # Run tests now with no expand tags (-net) flag, this will create the openend tag names
    short-to-llmon -net $src/$test.short $res/$test-net.llmon

    ./mydiff.sh $res/$test-net.llmon $src/$test-net.llmon > $res/$test-short-to-llmon-net.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for short --> llmon -net ($test).  Results are in $res/$test-short-to-llmon-net.diff"
    else
        echo "  Passed short --> llmon -net ($test)"
    fi

    # comment related tests won't produce the exact same source bc the comments are not translated to llmon file. 
    # For now we don't use it for round trip testing
    if [ $test != comment-test ] && [ $test != comment-catalog ] && [ $test != t4 ]
    then

        # test input with no openend tags [default]
        llmon-to-short $src/$test.llmon $res/$test.short

        # we compare to *-et.short file rather than *.short bc if original short file had x.y tag names these will be expanded when converting to llmon
        #   and then when converted back won't be the same *.short file.
        # such an input is not really expected, but we test for it anyway.
        ./mydiff.sh $res/$test.short $src/$test-et.short > $res/$test-llmon-to-short.diff
         if [ $? -ne 0 ]
        then
            failures=$((failures + 1))
            echo "  *** Failure for llmon --> short ($test).  Results are in $res/$test-llmon-to-short.diff"
        else
            echo "  Passed llmon --> short ($test)"
        fi

        # test input with openend tags generated [not default]
        llmon-to-short -eot $src/$test-net.llmon $res/$test-eot.short

        ./mydiff.sh $res/$test-eot.short $src/$test.short > $res/$test-llmon-to-short-eot.diff
        if [ $? -ne 0 ]
        then
            failures=$((failures + 1))
            echo "  *** Failure for llmon --> short ($test-eot).  Results are in $res/$test-llmon-to-short-eot.diff"
        else
            echo "  Passed llmon --> short ($test-eot)"
        fi
    fi
done

for test in space
do
    # run a test that should fail in parse error
    short-to-llmon $src/$test.short $res/$test.llmon 2> $res/$test.err

    ./mydiff.sh $res/$test.err $src/$test.err > $res/$test-short-to-llmon.diff

    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for short -> llmon ($test).  Results are in $res/$test-short-to-llmon.diff"
    else
        echo "  Passed short --> llmon ($test)"
    fi
done

for test in escape3
do

    text-to-escaped-short $src/$test.txt $res/$test-txt.short

    ./mydiff.sh $res/$test-txt.short $src/$test-txt.short > $res/$test-text-to-short.diff
    if [ $? -ne 0 ]
    then
        failures=$((failures + 1))
        echo "  *** Failure for text -> short ($test).  Results are in $res/$test-text-short.diff"
    #    cat $res/$test-text-short.diff
    else
        echo "  Passed text --> short ($test)"
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
