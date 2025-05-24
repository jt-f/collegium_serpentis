<template>
  <div class="frontend-client-id">
    Frontend ID: <span>{{ clientId }}</span>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
const clientId = ref('Loading...');
onMounted(() => {
  clientId.value = 'fe-' + Math.random().toString(36).substring(2, 9);
});
</script>

<style scoped>
.frontend-client-id {
  font-size: 0.85em;
  color: #adb5bd; /* Lighter variant of Medium Gray */
}
.frontend-client-id span {
  font-weight: 600;
  color: #F8F9FA;
}
</style>
