<template>
  <div class="status-block redis-status">
    <h3>Redis Connection</h3>
    <p class="status-text-container">
      <span class="status-indicator" :class="indicatorClass"></span>
      <span :class="statusTextClass">{{ statusText }}</span>
    </p>
    <div v-if="lastUpdate && redisStatus === 'connected'" class="last-update"> <!-- Only show last update if connected -->
      Last update: {{ formatTime(lastUpdate) }}
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const redisStatus = computed(() => wsStore.redisStatus)
// Assuming lastUpdate is relevant for Redis status updates from the server
const lastUpdate = computed(() => wsStore.redisLastUpdate || wsStore.lastUpdate) 

const statusText = computed(() => {
  switch (redisStatus.value) {
    case 'connected':
      return 'Operational' // Changed text for more "spy" feel
    case 'unavailable':
      return 'System Offline'
    case 'error':
      return 'Compromised'
    case 'unknown':
    default:
      return 'Status Unknown'
  }
})

const statusTextClass = computed(() => {
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

const indicatorClass = computed(() => {
  switch (redisStatus.value) {
    case 'connected':
      return 'indicator-connected'
    case 'unavailable':
    case 'error':
      return 'indicator-error' // Use error styling for unavailable too
    case 'unknown':
    default:
      return 'indicator-unknown'
  }
})

function formatTime(isoString) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
</script>

<style scoped>
.status-block {
  padding: 1rem 1.5rem; /* 16px 24px */
  border: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  border-radius: 4px;
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
  position: relative; /* For pseudo-element positioning */
  overflow: hidden; /* To contain the pseudo-element if it bleeds */
}

/* "Classified Stamp" motif */
.status-block::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 0;
  height: 0;
  border-left: 30px solid transparent; /* Adjust size as needed */
  border-top: 30px solid rgba(140, 58, 58, 0.6); /* --color-accent-red (#8C3A3A) with alpha */
}

.status-block h3 {
  margin-top: 0;
  margin-bottom: 0.5rem; /* 8px */
  font-family: 'Special Elite', cursive; /* Thematic font for titles */
  font-size: 1.3rem; /* Adjusted for Special Elite */
  font-weight: normal; /* Special Elite is often bold by default */
  color: var(--color-heading);
  padding-bottom: 0.25rem; /* 4px, subtle separation */
  border-bottom: 1px dashed var(--color-text-muted); /* Dashed line for a "field label" feel */
}

.status-text-container {
  display: flex;
  align-items: center;
  gap: 0.5rem; /* 8px */
  margin: 0.5rem 0; /* Consistent margin */
}

.status-indicator {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  position: relative;
}

.indicator-connected {
  background-color: var(--color-accent-green);
  animation: pulse-green 2s infinite; /* Defined in ServerConnectionMonitor.vue, or define globally/re-define here */
}

.indicator-error { /* Covers 'unavailable' and 'error' */
  background-color: var(--color-accent-red);
  animation: pulse-red 2.5s infinite; /* Defined in ServerConnectionMonitor.vue, or define globally/re-define here */
}

.indicator-unknown {
  background-color: var(--color-text-muted);
}

/* Re-define animations if not globally available or imported */
@keyframes pulse-green {
  0%, 100% { opacity: 1; box-shadow: 0 0 4px var(--color-accent-green); }
  50% { opacity: 0.7; box-shadow: 0 0 7px var(--color-accent-green); }
}

@keyframes pulse-red {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.last-update {
  font-size: 0.8em;
  color: var(--color-text-muted);
  margin-top: 0.75rem; /* 12px */
  padding-top: 0.5rem; /* 8px */
  border-top: 1px solid var(--color-border); /* Use theme border color */
  text-align: right; /* Align to the right for a more "status report" feel */
}

/* Text styling for status */
.status-text-container span {
  font-weight: 700;
  font-size: 0.95rem;
}

.status-connected {
  color: var(--color-accent-green);
}

.status-error { /* Covers 'unavailable' and 'error' */
  color: var(--color-accent-red);
}

.status-unknown {
  color: var(--color-text-muted);
}
</style>
