<template>
  <div class="status-block redis-status">
    <h3>Redis Connection</h3>
    <p :class="statusClass">{{ statusText }}</p>
  </div>
</template>

<script setup>
// Script content from previous step (Turn 89)
import { ref, onMounted, onUnmounted } from 'vue';

const statusText = ref('Unknown');
const statusClass = ref('status-unknown');
let intervalId = null;

async function fetchRedisStatus() {
  try {
    const response = await fetch('/statuses');
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const data = await response.json();
    const redisConnected = data.redis_status === 'connected';
    statusText.value = redisConnected ? 'Connected' : (data.redis_status || 'Error');
    statusClass.value = redisConnected ? 'status-connected' : 'status-error';
  } catch (error) { // Corrected from previous worker report
    console.error("Failed to fetch Redis status:", error);
    statusText.value = 'Error';
    statusClass.value = 'status-error';
  }
}

onMounted(() => {
  fetchRedisStatus();
});
</script>

<style scoped>
.status-block {
  padding: 1rem 1.5rem;
  border: 1px solid #495057; /* Subtle Gray */
  border-radius: 4px;
  background-color: #343A40; /* Dark Cool Gray */
}
.status-block h3 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-size: 1.1rem;
  font-weight: 600;
  color: #F8F9FA;
  border-bottom: 1px solid #495057;
  padding-bottom: 0.5rem;
}
.status-unknown { color: #D97706; /* Desaturated Amber */ font-weight: 600; }
.status-connected { color: #28a745; /* Desaturated Green */ font-weight: 600; }
.status-error { color: #A94442; /* Desaturated Red */ font-weight: 600; }
</style>
