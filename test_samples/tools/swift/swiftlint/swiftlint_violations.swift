import Foundation

// Intentional SwiftLint violations for testing lintro's swiftlint plugin.
func doStuff( ) {
    let x = 1
    let value = 1 ;
    print("hello world this is a very long line that exceeds the default line length limit set by swiftlint rules for sure yes")
    print(x)
    print(value)
}

class foo {
    func Bar() {
    }
}
