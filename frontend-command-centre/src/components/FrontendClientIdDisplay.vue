<template>
  <div class="frontend-client-id">
    <div class="connection-indicator">
      <span :class="connectionClass">●</span>
      {{ connectionText }}
    </div>
    <div class="client-id-text">
      Frontend ID: <span>{{ clientId }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const clientId = computed(() => wsStore.frontendClientId)
const connectionStatus = computed(() => wsStore.connectionStatus)

const connectionClass = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'connection-connected'
    case 'connecting':
      return 'connection-connecting'
    case 'disconnected':
      return 'connection-disconnected'
    case 'error':
      return 'connection-error'
    default:
      return 'connection-unknown'
  }
})

const connectionText = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'Connected'
    case 'connecting':
      return 'Connecting...'
    case 'disconnected':
      return 'Disconnected'
    case 'error':
      return 'Connection Error'
    default:
      return 'Unknown'
  }
})
</script>

<style scoped>
.frontend-client-id {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.2rem; /* Slightly reduced gap */
  padding: 0.3rem 0.6rem; /* ~5px 10px */
  background-color: var(--color-background); /* Darker background for a "tag" feel */
  border: 1px solid var(--color-border); /* Olive drab border */
  border-radius: 3px;
  box-shadow: inset 0 0 3px rgba(0,0,0,0.3); /* Subtle inner shadow for depth */
}

.connection-indicator {
  font-size: 0.75em; /* Slightly smaller */
  display: flex;
  align-items: center;
  gap: 0.3rem; /* Adjusted gap */
}

.connection-indicator .indicator-dot {
  width: 8px; /* Small dot */
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.client-id-text {
  font-size: 0.8em; /* Slightly smaller */
  color: var(--color-text-muted); /* Label color */
}

.client-id-text span {
  font-weight: 700; /* Bolder ID */
  color: var(--color-text); /* Brighter color for ID */
  font-family: 'Courier New', monospace;
  margin-left: 0.25rem; /* Space between label and ID */
}

/* Connection status colors using theme variables */
.connection-connected .indicator-dot { background-color: var(--color-accent-green); }
.connection-connected { color: var(--color-accent-green); }

.connection-connecting .indicator-dot { 
  background-color: var(--color-accent-manila);
  animation: pulse-yellow 1.2s infinite; /* Consistent with ServerConnectionMonitor */
}
.connection-connecting { color: var(--color-accent-manila); }

.connection-disconnected .indicator-dot { background-color: var(--color-text-muted); }
.connection-disconnected { color: var(--color-text-muted); }

.connection-error .indicator-dot { background-color: var(--color-accent-red); }
.connection-error { color: var(--color-accent-red); }

.connection-unknown .indicator-dot { background-color: var(--color-text-muted); }
.connection-unknown { color: var(--color-text-muted); }


/* Re-define pulse-yellow if not globally available or imported from ServerConnectionMonitor */
@keyframes pulse-yellow {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(1.1); }
}
</style>
