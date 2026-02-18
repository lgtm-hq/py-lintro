<script setup lang="ts">
// vue_tsc_violations.vue - Deliberate Vue type errors for testing

// Type mismatch: string assigned to number
const count: number = "hello";

// Using undefined variable
const message = undefinedVariable;

// Wrong return type assignment
function add(a: number, b: number): number {
  return a + b;
}
const result: string = add(1, 2);
</script>

<template>
  <div>
    <h1>{{ count }}</h1>
    <p>{{ message }}</p>
    <span>{{ result }}</span>
  </div>
</template>
