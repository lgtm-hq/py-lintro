#!/bin/bash
# Sample script with formatting violations for shfmt testing
# This file intentionally contains formatting issues

# Extra whitespace around brackets
if [ "$foo" = "bar" ]; then
	echo "match"
fi

# Inconsistent indentation (tabs vs spaces mixed)
function example_func() {
	echo "no indent"
	echo "two spaces"
	echo "tab indent"
}

# Missing spaces around operators
x=1+2

# Binary operator should be on next line (if binary_next_line enabled)
long_command_one && long_command_two && long_command_three

# Switch case not indented (if switch_case_indent enabled)
case "$1" in
start)
	echo "Starting"
	;;
stop)
	echo "Stopping"
	;;
esac

# No space after redirect (if space_redirects enabled)
echo "test" >output.txt
cat <input.txt

# Code that can be simplified (if simplify enabled)
[ ! -z "$var" ] && echo "not empty"

# Trailing whitespace
echo "line with trailing space"

# Multiple blank lines

echo "after multiple blank lines"
