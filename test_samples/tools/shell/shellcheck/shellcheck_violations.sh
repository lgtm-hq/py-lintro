#!/bin/bash
# Sample shell script with intentional ShellCheck violations for testing.
# This file demonstrates common shell scripting issues that ShellCheck detects.

# SC2086: Double quote to prevent globbing and word splitting
MYVAR="hello world"
echo $MYVAR

# SC2046: Quote this to prevent word splitting
files=$(ls *.txt)
echo $files

# SC2006: Use $(...) notation instead of legacy backticks
DATE=$(date)
echo $DATE

# SC2034: Variable appears unused
UNUSED_VAR="I am never used"

# SC2164: Use 'cd ... || exit' or 'cd ... || return' in case cd fails
cd /some/directory
pwd

# SC2001: See if you can use ${variable//search/replace} instead
RESULT=$(echo "$MYVAR" | sed 's/hello/goodbye/')
echo $RESULT

# SC2012: Use find instead of ls to better handle non-alphanumeric filenames
for file in $(ls); do
	echo "$file"
done

# SC2039: In POSIX sh, arrays are undefined (if using #!/bin/sh)
# Using bash, so this is ok, but demonstrates array usage

# SC2068: Double quote array expansions to avoid re-splitting
myarray=("one" "two" "three")
for item in ${myarray[@]}; do
	echo $item
done

# SC2129: Consider using { cmd1; cmd2; } >> file instead of individual redirects
echo "line1" >>/tmp/output.txt
echo "line2" >>/tmp/output.txt
echo "line3" >>/tmp/output.txt

# SC2162: read without -r will mangle backslashes
while read line; do
	echo $line
done </dev/null

# SC2185: Some finds don't have a default path (add '.' or specify paths)
find -name "*.txt"
