// tsc_clean.ts - Valid TypeScript code without type errors

const count: number = 42;

function greet(name: string): string {
    return `Hello, ${name}`;
}

function add(a: number, b: number): number {
    return a + b;
}

const result: number = add(1, 2);

// Correct handling of optional property
interface User {
    name: string;
    email?: string;
}

function getEmailLength(user: User): number {
    return user.email?.length ?? 0;
}

// Correct type narrowing
const value: unknown = "hello";
if (typeof value === "string") {
    const str: string = value;
    console.log(str.toUpperCase());
}
