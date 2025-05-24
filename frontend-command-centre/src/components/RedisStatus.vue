<template>
  <div class="status-block redis-status">
    <h3>Redis Connection</h3>
    <p :class="statusClass">{{ statusText }}</p>
    <div v-if="lastUpdate" class="last-update">
      Last update: {{ formatTime(lastUpdate) }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const redisStatus = computed(() => wsStore.redisStatus)
const lastUpdate = computed(() => wsStore.lastUpdate)

const statusText = computed(() => {
  switch (redisStatus.value) {
    case 'connected':
      return 'Connected'
    case 'unavailable':
      return 'Unavailable'
    case 'error':
      return 'Error'
    case 'unknown':
    default:
      return 'Unknown'
  }
})

const statusClass = computed(() => {
  switch (redisStatus.value) {
    case 'connected':
      return 'status-connected'
    case 'unavailable':
    case 'error':
      return 'status-error'
    case 'unknown':
    default:
      return 'status-unknown'
  }
})

function formatTime(isoString) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleTimeString()
}
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

.last-update {
  font-size: 0.8em;
  color: #6C757D; /* Medium Gray */
  margin-top: 0.5rem;
}

.status-unknown {
  color: #D97706; /* Desaturated Amber */
  font-weight: 600;
}

.status-connected {
  color: #10B981; /* Desaturated Green */
  font-weight: 600;
}

.status-error {
  color: #EF4444; /* Desaturated Red */
  font-weight: 600;
}
</style>
