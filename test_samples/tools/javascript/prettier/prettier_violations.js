// This file intentionally violates Prettier rules
function foo() {
  console.log('Hello, world!'); // Missing semicolon and double quotes (should be single)
  const arr = [1, 2, 3]; // Missing spaces after commas
  return { a: 1, b: 2 }; // Missing spaces after colons, missing trailing comma
}

foo(); // Missing semicolon

// Long line that exceeds printWidth
const veryLongVariableNameThatExceedsTheMaximumLineLengthAndShouldTriggerAPrettierViolation =
  'this is a very long string that makes the line too long and should trigger a prettier violation';

// Inconsistent indentation
function bar() {
  console.log('inconsistent indentation'); // No indentation
  console.log('more inconsistent indentation'); // 4 spaces instead of 2
}

// Object with missing trailing comma and bad spacing
const obj = {
  name: 'test',
  value: 123,
};

// Array and object violations
const badArray = [1, 2, 3, 4, 5];
const badObject = { key1: 'value1', key2: 'value2' };

function badFunction(param1, param2, param3) {
  if (param1 === param2) {
    return true;
  }
  return false;
}

// Mixed quote usage (double quotes when single are expected)
const mixedQuotes = 'This uses double quotes';
const anotherString = 'Another double quotes string';

// Bad spacing in function calls
console.log('test');
console.log('test');
