<script setup lang="ts">
// vue_tsc_clean.vue - Valid Vue code without type errors

interface Props {
    title: string;
    count?: number;
}

const props = withDefaults(defineProps<Props>(), {
    count: 0,
});

const message: string = "Welcome to Vue";

function add(a: number, b: number): number {
    return a + b;
}

const result: number = add(1, 2);
</script>

<template>
  <div>
    <h1>{{ props.title }}</h1>
    <p>{{ message }}</p>
    <span>Count: {{ props.count }}</span>
    <span>Result: {{ result }}</span>
  </div>
</template>
