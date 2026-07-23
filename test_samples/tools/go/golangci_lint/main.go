// Package main contains intentional golangci-lint violations used as a
// binary-gated integration fixture. It is a valid Go module so golangci-lint
// can build it, but triggers errcheck (unchecked error) and ineffassign
// (ineffectual assignment).
package main

import (
	"fmt"
	"os"
)

func main() {
	os.Open("foo.txt") // errcheck: unchecked error return value
	x := 1             // ineffassign: this assignment is never used
	x = 2
	fmt.Println(x)
}
