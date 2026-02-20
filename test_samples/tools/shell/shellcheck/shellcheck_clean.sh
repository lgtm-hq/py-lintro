#!/bin/bash
# Sample clean shell script with no ShellCheck violations for testing.
# This file demonstrates proper shell scripting practices.

set -euo pipefail

# Function with proper quoting and local variables
say_hello() {
    local name="$1"
    echo "Hello, ${name}!"
}

# Main function with proper argument handling
main() {
    if [[ -n "${1:-}" ]]; then
        say_hello "$1"
    else
        say_hello "World"
    fi
}

# Properly quoted array expansion
print_items() {
    local items=("one" "two" "three")
    for item in "${items[@]}"; do
        echo "$item"
    done
}

# Proper use of read with -r flag
read_lines() {
    while IFS= read -r line; do
        echo "$line"
    done < /dev/null
}

# Proper command substitution with $()
get_date() {
    local current_date
    current_date="$(date +%Y-%m-%d)"
    echo "$current_date"
}

main "$@"
