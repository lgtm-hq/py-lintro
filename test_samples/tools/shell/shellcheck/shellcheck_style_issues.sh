#!/bin/bash
# Sample shell script with style-level ShellCheck violations for testing.
# These are style issues that don't affect correctness but are not best practices.

# SC2006: Use $(...) notation instead of legacy backticks
output=$(ls -la)

# SC2028: echo may not expand escape sequences. Use printf.
echo -e "Hello\nWorld"

# SC2129: Consider using { cmd1; cmd2; } >> file instead of individual redirects
echo "line1" >>/tmp/style_output.txt
echo "line2" >>/tmp/style_output.txt

echo "$output"
