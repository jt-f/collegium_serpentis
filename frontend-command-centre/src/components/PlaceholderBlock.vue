<template>
  <div class="status-block placeholder-block">
    <h3>{{ title }}</h3>
    <p>{{ content }}</p>
  </div>
</template>

<script setup>
defineProps({
  title: String,
  content: String
});
</script>

<style scoped>
.status-block {
  padding: 1rem; /* 16px */
  border: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  border-radius: 4px;
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
  position: relative; /* For pseudo-element positioning */
  overflow: hidden; /* To contain the pseudo-element if it bleeds */
}

/* "Classified Stamp" motif - consistent with ClientsTable.vue */
.status-block::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 0;
  height: 0;
  border-left: 30px solid transparent;
  border-top: 30px solid var(--color-accent-green);
}

.placeholder-block h3 {
  margin-top: 0;
  margin-bottom: 0.5rem; /* 8px */
  font-family: 'Special Elite', cursive; /* Thematic font for titles */
  font-size: 1.3rem; /* Adjusted for Special Elite */
  font-weight: normal; /* Special Elite is often bold by default */
  color: var(--color-heading);
  padding-bottom: 0.25rem; /* 4px, subtle separation */
  border-bottom: 1px dashed var(--color-text-muted); /* Dashed line for a "placeholder" or "field label" feel */
}

.placeholder-block p {
  font-size: 0.9rem;
  color: var(--color-text);
  line-height: 1.4;
  margin-bottom: 0; /* Remove default p margin if not needed */
}
</style>
