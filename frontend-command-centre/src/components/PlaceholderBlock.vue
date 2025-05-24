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
  padding: 1rem;
  border: 1px solid #ddd; /* Placeholder */
  border-radius: 4px;
  background-color: #fff;
}
.placeholder-block h3 {
  margin-top: 0;
  font-size: 1.1rem;
  color: #333; /* Placeholder */
}
.placeholder-block p {
  font-size: 0.9em;
  color: #555; /* Placeholder */
}
</style>
